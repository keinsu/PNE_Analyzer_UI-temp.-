"""장비 CSV를 사용하는 독립 dQ/dV 분석 화면."""
from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QStackedWidget, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QScrollArea, QFileDialog, QTableWidgetItem,
)

from backend.mapping_loader import (
    parse_headers_from_text, build_df_from_mapping, detect_pretreat_steps,
    suggest_cycle_offsets, combine_multi_file_dfs,
)
from backend.dqdv import compute_dqdv, get_qv_curve
from .upload_page import UploadPage
from .mapping_page import MappingPage
from .merge_page import MergePage
from .widgets import parse_cycle_spec, CopyableTableWidget
from .report_page import configure_plot_interaction


class DQDVApp(QWidget):
    homeRequested = Signal()

    def __init__(self, read_text, parent=None):
        super().__init__(parent)
        self.read_text = read_text
        self.paths = []
        self.texts = []
        self.df = None
        self.pending_dfs = []
        self.upper_voltage = None
        self.curve_rows = []
        self.peak_rows = []
        self.result_settings = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        header = QHBoxLayout()
        title = QLabel("dQ/dV 분석")
        title.setStyleSheet("font-size:22px; font-weight:700;")
        header.addWidget(title)
        header.addStretch()
        home = QPushButton("🏠 홈으로")
        home.clicked.connect(self.homeRequested.emit)
        header.addWidget(home)
        reset = QPushButton("새 파일 업로드")
        reset.clicked.connect(self.reset)
        header.addWidget(reset)
        outer.addLayout(header)
        notice = QLabel("입력: 충방전 장비의 전체 Raw Data CSV · 기존 컬럼 매핑과 단위 보정을 사용합니다.")
        notice.setWordWrap(True)
        outer.addWidget(notice)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)
        self.upload = UploadPage()
        self.mapping = MappingPage()
        # 공용 매핑 화면의 단위 보정은 Cycle/dQ/dV에도 적용된다.
        for label in self.mapping.findChildren(QLabel):
            if label.text() == "DCR 단위 확인":
                label.setText("입력 단위 확인")
        self.merge = MergePage()
        self.stack.addWidget(self.upload)
        self.stack.addWidget(self.mapping)
        self.stack.addWidget(self.merge)
        self._build_report()
        self.upload.fileSelected.connect(lambda path, enc, skip: self.load_files([path], enc, skip))
        self.upload.multiFilesSelected.connect(self.load_files)
        self.mapping.reparseRequested.connect(self.reparse)
        self.mapping.pretreatDetectRequested.connect(self.detect_pretreat)
        self.mapping.startAnalysis.connect(self.start_analysis)
        self.merge.mergeConfirmed.connect(self.confirm_merge)
        self.merge.cancelRequested.connect(lambda: self.stack.setCurrentWidget(self.mapping))

    def _build_report(self):
        self.report = QScrollArea()
        self.report.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        self.report.setWidget(body)
        self.stack.addWidget(self.report)
        self.file_info = QLabel()
        self.file_info.setWordWrap(True)
        layout.addWidget(self.file_info)
        controls = QGridLayout()
        self.cycles = QLineEdit()
        self.cycles.setPlaceholderText("예: 1,5,10-50:5")
        self.direction = QComboBox()
        self.direction.addItem("충전 + 방전", "both")
        self.direction.addItem("충전", "charge")
        self.direction.addItem("방전", "discharge")
        self.points = QSpinBox()
        self.points.setRange(50, 20000)
        self.smooth = QCheckBox("스무딩 사용")
        self.auto_window = QCheckBox("윈도 자동")
        self.window = QSpinBox()
        self.window.setRange(5, 19999)
        self.window.setSingleStep(2)
        self.polyorder = QSpinBox()
        self.polyorder.setRange(1, 10)
        self.prominence = QDoubleSpinBox()
        self.prominence.setRange(0.01, 100)
        self.prominence.setDecimals(2)
        self.prominence.setSuffix(" %")
        for row, (label, widget) in enumerate([
            ("사이클", self.cycles), ("분석 방향", self.direction),
            ("보간 포인트 수", self.points), ("스무딩", self.smooth),
        ]):
            controls.addWidget(QLabel(label), row, 0)
            controls.addWidget(widget, row, 1)
        controls.addWidget(self.auto_window, 0, 2)
        controls.addWidget(self.window, 0, 3)
        controls.addWidget(QLabel("스무딩 다항식 차수"), 1, 2)
        controls.addWidget(self.polyorder, 1, 3)
        controls.addWidget(QLabel("피크 민감도 (작을수록 많은 피크)"), 2, 2)
        controls.addWidget(self.prominence, 2, 3)
        defaults = QPushButton("기본 조건 복원")
        defaults.clicked.connect(self.restore_defaults)
        controls.addWidget(defaults, 3, 2)
        self.apply = QPushButton("조건 적용 / 다시 분석")
        self.apply.clicked.connect(self.analyze)
        controls.addWidget(self.apply, 3, 3)
        layout.addLayout(controls)
        self.settings_hint = QLabel()
        self.settings_hint.setWordWrap(True)
        layout.addWidget(self.settings_hint)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.vq_plot = pg.PlotWidget(title="원본 V–Q")
        self.vq_plot.setLabel("bottom", "Capacity", units="mAh")
        self.vq_plot.setLabel("left", "Voltage", units="V")
        self.dqdv_plot = pg.PlotWidget(title="dQ/dV")
        self.dqdv_plot.setLabel("bottom", "Voltage", units="V")
        self.dqdv_plot.setLabel("left", "dQ/dV", units="mAh/V")
        for plot in (self.vq_plot, self.dqdv_plot):
            plot.setMinimumHeight(300)
            for axis in ('bottom', 'left'):
                plot.getAxis(axis).enableAutoSIPrefix(False)
            plot.addLegend()
            plot.showGrid(x=True, y=True, alpha=0.2)
            configure_plot_interaction(plot)
            layout.addWidget(plot)
        layout.addWidget(QLabel("검출 피크 (선택 후 Ctrl+C로 복사)"))
        self.table = CopyableTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Cycle", "방향", "피크 전압 (V)", "dQ/dV (mAh/V)"])
        self.table.setMinimumHeight(180)
        layout.addWidget(self.table)
        exports = QHBoxLayout()
        self.export_curves = QPushButton("dQ/dV CSV 저장")
        self.export_peaks = QPushButton("피크 CSV 저장")
        self.export_curves.clicked.connect(lambda: self.export_csv(False))
        self.export_peaks.clicked.connect(lambda: self.export_csv(True))
        exports.addWidget(self.export_curves)
        exports.addWidget(self.export_peaks)
        exports.addStretch()
        layout.addLayout(exports)
        self.points.valueChanged.connect(self._conditions_changed)
        self.smooth.toggled.connect(self._conditions_changed)
        self.auto_window.toggled.connect(self._conditions_changed)
        self.window.valueChanged.connect(self._conditions_changed)
        self.polyorder.valueChanged.connect(self._conditions_changed)
        self.prominence.valueChanged.connect(self._conditions_changed)
        self.cycles.textChanged.connect(self._conditions_changed)
        self.direction.currentIndexChanged.connect(self._conditions_changed)
        self.restore_defaults()
        self._clear_results()

    def restore_defaults(self):
        self.points.setValue(500)
        self.smooth.setChecked(True)
        self.auto_window.setChecked(True)
        self.window.setValue(21)
        self.polyorder.setValue(3)
        self.prominence.setValue(5.0)
        self._conditions_changed()

    def _conditions_changed(self, *_):
        automatic = max(11, (self.points.value() // 25) | 1)
        if self.auto_window.isChecked():
            self.window.setValue(automatic)
        self.window.setEnabled(self.smooth.isChecked() and not self.auto_window.isChecked())
        self.auto_window.setEnabled(self.smooth.isChecked())
        self.polyorder.setEnabled(self.smooth.isChecked())
        self.settings_hint.setText(
            f"자동 윈도: {automatic}포인트 · 스무딩은 보간된 용량과 미분 결과에 각각 적용합니다. "
            "충전 CV 구간은 기존 상한전압 기준으로 제외합니다. 조건 변경 후 ‘다시 분석’을 누르세요."
        )
        if self.curve_rows:
            self.status.setText("조건이 변경되었습니다. 다시 분석하면 그래프와 저장 결과에 반영됩니다.")
            self.export_curves.setEnabled(False)
            self.export_peaks.setEnabled(False)

    def _clear_results(self):
        self.curve_rows = []
        self.peak_rows = []
        self.result_settings = {}
        self.vq_plot.clear()
        self.dqdv_plot.clear()
        self.table.setRowCount(0)
        self.export_curves.setEnabled(False)
        self.export_peaks.setEnabled(False)
        self.status.clear()

    def reset(self):
        self.df = None
        self.paths = []
        self.texts = []
        self.pending_dfs = []
        self._clear_results()
        self.upload.set_error("")
        self.stack.setCurrentWidget(self.upload)

    def load_files(self, paths, encoding, skip):
        if not paths:
            return
        try:
            texts = [self.read_text(path, encoding) for path in paths]
            info = parse_headers_from_text(texts[0], skip)
            self.paths, self.texts = list(paths), texts
            self.encoding, self.skip = encoding, skip
            self.mapping.set_file_badge(f"{Path(paths[0]).name} · {len(paths)}개 파일")
            self.mapping.set_encoding_skip(encoding, skip)
            self.mapping.set_headers_and_mapping(info['headers'], info['mapping'], info['capMode'], info['rowCount'])
            self.mapping.set_error("")
            self.stack.setCurrentWidget(self.mapping)
        except Exception as exc:
            self.upload.set_error(f"파일을 읽지 못했습니다: {exc}")

    def reparse(self, encoding, skip):
        try:
            texts = [self.read_text(path, encoding) for path in self.paths]
            info = parse_headers_from_text(texts[0], skip)
            mapping = {key: self.mapping.mapping.get(key) if self.mapping.mapping.get(key) in info['headers'] else val
                       for key, val in info['mapping'].items()}
            self.texts, self.encoding, self.skip = texts, encoding, skip
            self.mapping.set_headers_and_mapping(info['headers'], mapping, info['capMode'], info['rowCount'])
            self.mapping.set_error("")
        except Exception as exc:
            self.mapping.set_error(f"다시 파싱하지 못했습니다: {exc}")

    def detect_pretreat(self, cycle, step):
        if not self.texts:
            return
        try:
            self.mapping.set_detected_pretreat(detect_pretreat_steps(self.texts[0], self.skip, cycle, step))
        except Exception as exc:
            self.mapping.set_error(f"전처리 Step을 확인하지 못했습니다: {exc}")

    def start_analysis(self, params):
        try:
            self.mapping.set_busy(True)
            dfs = []
            for path, text in zip(self.paths, self.texts):
                df = build_df_from_mapping(text, self.skip, params['mapping'], params['capMode'],
                                           set(params['excludeSteps']), set(params['excludeCycles']),
                                           params.get('unitOverrides'))
                if df.empty:
                    raise ValueError(f"{Path(path).name}: 유효한 Cycle·전압 데이터가 없습니다.")
                df['Source_File'] = Path(path).name
                dfs.append(df)
            self.upper_voltage = params.get('upperVoltage')
            if len(dfs) > 1:
                self.pending_dfs = dfs
                ranges = [(int(df.Cycle.min()), int(df.Cycle.max())) for df in dfs]
                offsets = suggest_cycle_offsets(ranges)
                self.merge.set_files([
                    dict(original_index=i, name=Path(self.paths[i]).name, row_count=len(df),
                         min_cycle=ranges[i][0], max_cycle=ranges[i][1], offset=offsets[i])
                    for i, df in enumerate(dfs)
                ])
                self.merge.set_error("")
                self.stack.setCurrentWidget(self.merge)
            else:
                self.set_data(dfs[0])
        except Exception as exc:
            self.mapping.set_error(f"분석 준비 중 오류: {exc}")
        finally:
            self.mapping.set_busy(False)

    def confirm_merge(self, order):
        try:
            dfs = []
            for item in order:
                df = self.pending_dfs[item['original_index']].copy()
                df['Cycle'] += int(item['offset'])
                dfs.append(df)
            self.set_data(combine_multi_file_dfs(dfs))
        except Exception as exc:
            self.merge.set_error(f"병합하지 못했습니다: {exc}")

    def set_data(self, df):
        self._clear_results()
        self.df = df
        available = sorted(df.Cycle.unique())
        self.file_info.setText(
            f"{', '.join(Path(path).name for path in self.paths)} · {len(df):,}행 · "
            f"Cycle {available[0]}–{available[-1]} ({len(available)}개)"
        )
        self.cycles.setText(str(available[0]))
        self.stack.setCurrentWidget(self.report)
        self.analyze()

    def analyze(self):
        if self.df is None:
            return
        self._clear_results()
        try:
            selected = parse_cycle_spec(self.cycles.text())
            if not selected:
                raise ValueError("분석할 사이클을 입력해주세요. 예: 1,5,10-50:5")
            n = self.points.value()
            window = self.window.value()
            degree = self.polyorder.value()
            smooth = self.smooth.isChecked()
            if smooth and (window % 2 == 0 or window >= n or degree >= window):
                raise ValueError("스무딩 윈도는 홀수이며 보간 포인트 수보다 작아야 합니다. 다항식 차수는 윈도보다 작아야 합니다.")
            directions = ('charge', 'discharge') if self.direction.currentData() == 'both' else (self.direction.currentData(),)
            available = set(self.df.Cycle.unique())
            skipped = []
            settings = dict(Interpolation_Points=n, Smoothing=smooth,
                            Window_Mode='auto' if self.auto_window.isChecked() else 'manual',
                            Smoothing_Window=window if smooth else None,
                            Polynomial_Order=degree if smooth else None,
                            Peak_Prominence_pct=self.prominence.value(), Upper_Voltage_V=self.upper_voltage)
            curves, peaks, plots = [], [], []
            for cycle in selected:
                if cycle not in available:
                    skipped.append(f"Cycle {cycle}: 데이터 없음")
                    continue
                for direction in directions:
                    result = compute_dqdv(
                        self.df, cycle, direction, n_points=n, smooth=smooth,
                        smooth_window=None if self.auto_window.isChecked() else window,
                        smooth_polyorder=degree, peak_prominence_pct=self.prominence.value()/100,
                        upper_voltage_v=self.upper_voltage,
                    )
                    name = f"Cycle {cycle} {'충전' if direction == 'charge' else '방전'}"
                    if result is None or not np.isfinite(result['dqdv']).all():
                        skipped.append(f"{name}: 유효 데이터 부족")
                        continue
                    source = ', '.join(self.df.loc[self.df.Cycle == cycle, 'Source_File'].unique()) if 'Source_File' in self.df else ''
                    meta = dict(Source_File=source, Cycle=cycle, Direction=direction, **settings)
                    curves.extend(dict(**meta, Voltage_V=float(v), Capacity_mAh=float(q), dQdV_mAh_per_V=float(d))
                                  for v, q, d in zip(result['voltage_grid'], result['capacity_on_vgrid'], result['dqdv']))
                    peaks.extend(dict(**meta, Peak_Voltage_V=float(v), Peak_dQdV_mAh_per_V=float(d))
                                 for v, d in zip(result['peak_voltage'], result['peak_dqdv']))
                    plots.append((name, get_qv_curve(self.df, cycle, direction), result))
            for index, (name, raw, result) in enumerate(plots):
                color = pg.intColor(index, hues=max(1, len(plots)))
                pen = pg.mkPen(color, width=2)
                self.vq_plot.plot(raw.Capacity_mAh.to_numpy(), raw.Voltage_V.to_numpy(), pen=pen, name=name)
                self.dqdv_plot.plot(result['voltage_grid'], result['dqdv'], pen=pen, name=name)
                self.dqdv_plot.plot(result['peak_voltage'], result['peak_dqdv'], pen=None,
                                    symbol='o', symbolSize=7, symbolBrush=color, symbolPen=color)
            self.curve_rows, self.peak_rows, self.result_settings = curves, peaks, settings
            self.table.setRowCount(len(peaks))
            for row, peak in enumerate(peaks):
                values = [peak['Cycle'], '충전' if peak['Direction']=='charge' else '방전',
                          f"{peak['Peak_Voltage_V']:.6g}", f"{peak['Peak_dQdV_mAh_per_V']:.6g}"]
                for col, value in enumerate(values):
                    self.table.setItem(row, col, QTableWidgetItem(str(value)))
            self.table.resizeColumnsToContents()
            self.export_curves.setEnabled(bool(curves))
            self.export_peaks.setEnabled(bool(peaks))
            message = f"{len(plots)}개 곡선 · {len(peaks)}개 피크 · 보간 {n}포인트 · 스무딩 "
            message += f"윈도 {window}, {degree}차" if smooth else "사용 안 함"
            if skipped:
                message += '\n제외: ' + '; '.join(skipped[:20])
                if len(skipped) > 20:
                    message += f' 외 {len(skipped)-20}개'
            self.status.setText(message)
        except Exception as exc:
            self._clear_results()
            self.status.setText(f"분석하지 못했습니다: {exc}")

    def export_csv(self, peaks=False):
        rows = self.peak_rows if peaks else self.curve_rows
        if not rows:
            return
        name = 'dqdv_peaks.csv' if peaks else 'dqdv_curves.csv'
        path, _ = QFileDialog.getSaveFileName(self, "CSV 저장", name, "CSV Files (*.csv)")
        if not path:
            return
        try:
            pd.DataFrame(rows).to_csv(path, index=False, encoding='utf-8-sig')
        except Exception as exc:
            self.status.setText(f"저장하지 못했습니다: {exc}")
