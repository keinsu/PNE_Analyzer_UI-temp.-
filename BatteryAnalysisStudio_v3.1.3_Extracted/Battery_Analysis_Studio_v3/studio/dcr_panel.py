"""DCR workspace over shared, read-only channel data.

The legacy Qt detection/calculation rules are retained; this adapter supplies
background jobs, stable manual edits, safe view changes, and result provenance.
"""
from __future__ import annotations

import copy
import csv
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTabWidget,
    QSplitter, QFileDialog, QMessageBox, QProgressBar,
)

from backend.dcr_analysis import detect_dcr_blocks, compute_dcr_table, compute_pulse_result
from ui.dcr_review_page import DCRReviewPage
from ui.dcr_report_page import DCRReportPage
from ui.report_page import configure_plot_interaction, HoverReadout


def _step_numbers(text):
    result = set()
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2:
                raise ValueError("Step 번호 형식: 2,10,20-22")
            start, end = map(int, bounds)
            if start < 0 or end < start or end - start > 10000:
                raise ValueError("Step 번호 범위를 확인하세요.")
            result.update(str(n) for n in range(start, end + 1))
        else:
            value = int(part)
            if value < 0:
                raise ValueError("Step 번호는 0 이상이어야 합니다.")
            result.add(str(value))
    return result


class _Job(QThread):
    completed = Signal(object)

    def __init__(self, function, generation, parent=None):
        super().__init__(parent)
        self.function = function
        self.generation = generation
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            value = self.function(self.cancel_event.is_set)
            if self.cancel_event.is_set():
                raise InterruptedError()
            self.completed.emit((self.generation, value, None))
        except InterruptedError:
            self.completed.emit((self.generation, None, "취소되었습니다."))
        except Exception as exc:
            self.completed.emit((self.generation, None, f"{type(exc).__name__}: {exc}"))


class DCRPanel(QWidget):
    statusMessage = Signal(str)
    busyChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.channels = {}
        self.active_id = None
        self.include_review = False
        self.result_rows = []
        self.result_curves = []
        self.detection = None
        self._df = None
        self._generation = 0
        self._jobs = set()
        self._callbacks = {}
        self._last_completion_thread = None
        self._saved = {}
        self._history = {}
        self._edit_snapshot = {}
        self._applying = False
        self._busy = False
        self._result_id = None
        self.result_valid = False
        self._detected_settings = None
        self.schedule = None
        self.cancel_scheduled = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.channelLabel = QLabel("채널을 선택하면 같은 데이터로 DCR을 분석합니다.")
        toolbar.addWidget(self.channelLabel, 1)
        self.detectButton = QPushButton("펄스 탐지")
        self.detectButton.clicked.connect(self.detect)
        toolbar.addWidget(self.detectButton)
        self.undoButton = QPushButton("수정 취소")
        self.undoButton.clicked.connect(self.undo_edit)
        toolbar.addWidget(self.undoButton)
        self.cancelButton = QPushButton("작업 취소")
        self.cancelButton.clicked.connect(self.cancel)
        self.cancelButton.setEnabled(False)
        toolbar.addWidget(self.cancelButton)
        layout.addLayout(toolbar)
        self.message = QLabel("SOC%는 자동 추정하지 않습니다. 검토표에서 지정할 수 있습니다.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.tabs = QTabWidget()
        self.review = DCRReviewPage()
        self.report = DCRReportPage(integrated=True)
        self.tabs.addTab(self.review, "1. Pulse / SOC 검토")
        self.tabs.addTab(self.report, "2. DCR 결과")
        self.tabs.setTabEnabled(1, False)

        previewWidget = QWidget()
        previewLayout = QVBoxLayout(previewWidget)
        previewLayout.setContentsMargins(0, 4, 0, 0)
        previewToolbar = QHBoxLayout()
        self.previewLabel = QLabel("검토표 또는 결과표의 Pulse를 선택하면 원본 전압·전류와 계산 기준을 표시합니다.")
        self.previewLabel.setWordWrap(True)
        previewToolbar.addWidget(self.previewLabel, 1)
        savePng = QPushButton("그래프 PNG")
        savePng.clicked.connect(self.export_png)
        previewToolbar.addWidget(savePng)
        previewLayout.addLayout(previewToolbar)
        self.preview = pg.PlotWidget()
        self.preview.setMinimumHeight(170)
        self.preview.setLabel("bottom", "Step 시간", units="s")
        self.preview.setLabel("left", "전압", units="V")
        self.preview.showGrid(x=True, y=True, alpha=0.2)
        configure_plot_interaction(self.preview)
        self.previewHover = HoverReadout(self.preview, y_label="전압(V)")
        self.previewHover.set_enabled(True)
        self.currentPreview = pg.PlotWidget()
        self.currentPreview.setMinimumHeight(120)
        self.currentPreview.setLabel("left", "전류", units="mA")
        self.currentPreview.setLabel("bottom", "Step 시간", units="s")
        self.currentPreview.setXLink(self.preview)
        self.currentPreview.showGrid(x=True, y=True, alpha=0.2)
        configure_plot_interaction(self.currentPreview)
        previewLayout.addWidget(self.preview, 3)
        previewLayout.addWidget(self.currentPreview, 2)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(previewWidget)
        splitter.setSizes([620, 260])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, True)
        layout.addWidget(splitter, 1)
        self.splitter = splitter

        self.review.redetectRequested.connect(lambda *_: self.detect())
        self.review.startAnalysisRequested.connect(self.calculate)
        self.review.cancelRequested.connect(self.reset_edits)
        self.review.table.itemChanged.connect(self._on_edit)
        self.review.table.cellClicked.connect(self._preview_review)
        self.review.pulseMaxDurationSpin.valueChanged.connect(self._settings_changed)
        self.review.cutoffTolSpin.valueChanged.connect(self._settings_changed)
        for entry in (self.review.socSettingStepsInp, self.review.timePointsInp, self.review.socOcvInp):
            entry.textEdited.connect(self._settings_changed)
        self.report.pulseSelected.connect(self._preview_result)
        self.report.exportTableRequested.connect(lambda: self.export_csv("raw"))
        self.report.exportChartRequested.connect(lambda: self.export_csv("chart"))
        self.report.exportSummaryRequested.connect(lambda tp: self.export_csv("summary", tp))
        self.report.exportAllRequested.connect(self.export_excel)
        self.detectButton.setEnabled(False)
        self.undoButton.setEnabled(False)

    def set_channels(self, channels):
        self._store_current()
        previous = self.channels.get(self.active_id)
        incoming = {str(channel.id): channel for channel in channels}
        changed = (previous is not None and self.active_id in incoming and
                   previous.fingerprint != incoming[self.active_id].fingerprint)
        self.channels = {str(channel.id): channel for channel in channels}
        if changed or self.active_id not in self.channels:
            if changed:
                self._saved.pop(self.active_id, None)
            self.cancel()
            self.active_id = None
            self.detection = None
            self._df = None
            self.result_rows, self.result_curves = [], []
            self._result_id = None
            self.result_valid = False
            self.review.set_detection({"segments": [], "blocks": []})
            self.report.set_results([], [])
        self.detectButton.setEnabled(bool(self.channels) and not self._busy)

    def set_active_channel(self, channel_id, *, auto_detect=True):
        channel_id = str(channel_id)
        if channel_id == self.active_id:
            if auto_detect and self.detection is None and not self._busy:
                self.detect()
            return
        self._store_current()
        self.cancel()
        self.active_id = channel_id if channel_id in self.channels else None
        self._df = None
        self.detection = None
        self._edit_snapshot = {}
        self.result_rows, self.result_curves = [], []
        self._result_id = None
        self.result_valid = False
        self.tabs.setTabEnabled(1, False)
        if self.active_id is None:
            self.channelLabel.setText("DCR 분석할 채널을 선택하세요.")
            return
        channel = self.channels[self.active_id]
        self.channelLabel.setText(f"DCR / SOC · {channel.name}")
        self._apply_settings(self._saved.get(self.active_id, {}).get("settings", {}))
        self.detectButton.setEnabled(True)
        self.review.set_detection({"segments": [], "blocks": []})
        self.report.set_results([], [])
        if auto_detect:
            self.detect()

    def set_include_review(self, value, *, auto_detect=True):
        value = bool(value)
        if self.include_review != value:
            self.include_review = value
            if self.active_id:
                if auto_detect:
                    self.detect()
                else:
                    self._store_current()
                    self.cancel()
                    self.detection = self._df = None
                    self.result_valid = False
                    self.tabs.setTabEnabled(1, False)
                    self.review.set_detection({"segments": [], "blocks": []})
                    self.report.set_results([], [])

    def _settings(self):
        return {"pulse_max_duration_s": self.review.pulseMaxDurationSpin.value(),
                "return_capacity_tolerance_pct": self.review.cutoffTolSpin.value(),
                "setting_steps": self.review.socSettingStepsInp.text(),
                "time_points_text": self.review.timePointsInp.text(),
                "soc_ocv_text": self.review.socOcvInp.text(),
                "x_mode": self.report.xModeCombo.currentData()}

    def _apply_settings(self, settings):
        self._applying = True
        try:
            self.review.pulseMaxDurationSpin.setValue(float(settings.get("pulse_max_duration_s", 90)))
            self.review.cutoffTolSpin.setValue(float(settings.get("return_capacity_tolerance_pct", 10)))
            self.review.socSettingStepsInp.setText(str(settings.get("setting_steps", "")))
            self.review.timePointsInp.setText(str(settings.get("time_points_text", "1,10,30")))
            self.review.socOcvInp.setText(str(settings.get("soc_ocv_text", "")))
        finally:
            self._applying = False

    def _settings_changed(self, *args):
        if self._applying:
            return
        self.result_valid = False
        self.tabs.setTabEnabled(1, False)
        self.message.setText("설정이 변경되었습니다. 탐지 조건은 다시 탐지한 뒤, 계산 조건은 다시 분석해 적용하세요.")

    def _store_current(self):
        if self.active_id is not None:
            entry = self._saved.setdefault(self.active_id, {})
            entry["settings"] = self._settings()
            if self.detection is not None:
                entry["edits"] = self.review.edits()

    def state(self):
        self._store_current()
        return {"active_channel": self.active_id, "include_review": self.include_review,
                "channels": copy.deepcopy(self._saved), "splitter": self.splitter.sizes()}

    def restore_state(self, state, *, auto_detect=True):
        self.cancel()
        self._saved = copy.deepcopy(state.get("channels", {}))
        self.include_review = bool(state.get("include_review", False))
        if state.get("splitter"):
            self.splitter.setSizes([int(x) for x in state["splitter"]])
        channel_id = state.get("active_channel")
        self.active_id = None
        if channel_id in self.channels:
            self.set_active_channel(channel_id, auto_detect=auto_detect)

    def _set_busy(self, value):
        self._busy = bool(value)
        self.review.setEnabled(not value)
        self.report.setEnabled(not value)
        self.detectButton.setEnabled(not value and bool(self.active_id))
        self.cancelButton.setEnabled(value)
        self.undoButton.setEnabled(not value and bool(self._history.get(self.active_id)))
        self.progress.setVisible(value)
        self.busyChanged.emit(bool(value))

    def _run(self, label, function, callback):
        self.cancel()
        generation = self._generation
        self._callbacks[generation] = callback
        self._set_busy(True)
        self.message.setText(label)
        self.statusMessage.emit(label)
        if self.schedule:
            def work(progress, cancel):
                try:
                    return generation, function(cancel), None
                except Exception as exc:
                    return generation, None, f'{type(exc).__name__}: {exc}'
            self.schedule(work, self._job_completed)
            return
        job = _Job(function, generation, self)
        self._jobs.add(job)

        # A bound QObject slot supplies an explicit GUI-thread receiver context.
        # Free Python closures do not reliably retain that context in PySide.
        job.completed.connect(self._job_completed, Qt.ConnectionType.QueuedConnection)
        job.finished.connect(self._job_finished, Qt.ConnectionType.QueuedConnection)
        job.finished.connect(job.deleteLater)
        job.start()

    @Slot(object)
    def _job_completed(self, payload):
        self._last_completion_thread = QThread.currentThread()
        generation, value, error = payload
        callback = self._callbacks.pop(generation, None)
        if generation != self._generation or callback is None:
            return
        self._set_busy(False)
        if error is not None:
            self.message.setText(error)
            self.review.set_error(error)
            self.statusMessage.emit(error)
            return
        try:
            callback(value)
        except Exception as exc:
            self.message.setText(str(exc))
            self.review.set_error(str(exc))
            self.statusMessage.emit(str(exc))

    @Slot()
    def _job_finished(self):
        job = self.sender()
        if job is not None:
            self._jobs.discard(job)

    def cancel(self):
        if self.cancel_scheduled:
            self.cancel_scheduled()
            self._callbacks.clear()
        self._generation += 1
        for job in list(self._jobs):
            job.cancel()
        if self._busy:
            self.message.setText("작업을 취소했습니다. 이전 작업의 결과는 적용하지 않습니다.")
        self._set_busy(False)

    def detect(self):
        if self.active_id not in self.channels:
            return
        self._store_current()
        channel = self.channels[self.active_id]
        settings = self._settings()
        try:
            steps = _step_numbers(settings["setting_steps"])
        except ValueError as exc:
            self.review.set_error(str(exc))
            return
        include_review = self.include_review

        def work(cancel):
            from .analysis import analysis_frame
            frame = analysis_frame(channel, include_review=include_review,
                                   columns=('Cycle', '_row', 'Voltage_V', 'Capacity_mAh', 'Current_mA',
                                            'TypeStd', 'MatchStatus', 'StepTime_s', 'StepNo', 'StepMode',
                                            'PhysicalStepID', 'Source_File'))
            if frame.empty:
                raise ValueError("분석 가능한 행이 없습니다. Step 매칭 상태를 확인하세요.")
            if "StepTime_s" not in frame or not pd.to_numeric(frame["StepTime_s"], errors="coerce").notna().any():
                raise ValueError("DCR 분석에는 실제 Step 시간 열이 필요합니다.")
            detection = detect_dcr_blocks(
                frame, pulse_max_duration_s=settings["pulse_max_duration_s"],
                return_capacity_tolerance_pct=settings["return_capacity_tolerance_pct"],
                soc_setting_step_nos=steps, cancel=cancel,
            )
            for block in detection["blocks"]:
                for group in block["pulse_groups"]:
                    for pulse in group["pulses"]:
                        if cancel():
                            raise InterruptedError()
                        result = compute_pulse_result(frame, detection["segments"], pulse, [], ordered=frame)
                        pulse["v0"] = result["v0"]
                        pulse["v0_source"] = result["v0_source"]
            return frame, detection

        def loaded(value):
            self._df, self.detection = value
            self._detected_settings = copy.deepcopy(settings)
            self._applying = True
            try:
                self.review.set_detection(self.detection)
                self.review.apply_edits(self._saved.get(self.active_id, {}).get("edits", {}))
                self._edit_snapshot = self.review.edits()
            finally:
                self._applying = False
            self.result_rows, self.result_curves = [], []
            self.result_valid = False
            self._result_id = None
            self.tabs.setTabEnabled(1, False)
            self.tabs.setCurrentIndex(0)
            count = len(self.review.pulse_rows)
            text = f"Pulse {count}개를 찾았습니다. SOC와 포함 여부를 검토한 뒤 분석하세요."
            if not count:
                text = "Pulse를 찾지 못했습니다. 휴지→펄스→반대방향 Return 구조와 탐지 설정을 확인하세요."
            self.message.setText(text)
            self.statusMessage.emit(text)
            self._store_current()
            if count:
                self._preview_review(0, 0)

        self._run("Pulse / SOC 블록을 탐지하고 있습니다…", work, loaded)

    def calculate(self, parameters):
        if self.detection is None or self._df is None:
            return
        self._store_current()
        current = self._settings()
        if self._detected_settings is not None and any(
            current[key] != self._detected_settings[key]
            for key in ("pulse_max_duration_s", "return_capacity_tolerance_pct")
        ) or self._detected_settings is not None and (
            _step_numbers(current["setting_steps"]) != _step_numbers(self._detected_settings["setting_steps"])
        ):
            self.review.set_error("탐지 설정이 바뀌었습니다. '다시 탐지' 후 Pulse를 검토하고 분석하세요.")
            return
        frame, detection = self._df, self.detection
        parameters = copy.deepcopy(parameters)
        channel_id = self.active_id
        channel_name = self.channels[channel_id].name
        settings_snapshot = copy.deepcopy(self._saved.get(channel_id, {}).get("settings", {}))

        def work(cancel):
            result = compute_dcr_table(
                frame, detection, parameters["time_points"],
                excluded_pulse_segs=parameters.get("excluded_segs"),
                soc_ocv_points=parameters.get("soc_ocv_points"),
                manual_block_by_seg=parameters.get("manual_block_by_seg"),
                manual_soc_by_seg=parameters.get("manual_soc_by_seg"), cancel=cancel,
            )
            for row in result["table"]:
                row["Source_ID"] = channel_id
                row["Channel"] = channel_name
                row["Settings"] = settings_snapshot
            return result

        def calculated(result):
            self.result_rows = result["table"]
            self.result_curves = result["raw_curves"]
            self._result_id = channel_id
            self.result_valid = True
            self.report.set_results(self.result_rows, parameters["time_points"])
            mode = self._saved.get(channel_id, {}).get("settings", {}).get("x_mode")
            if mode is not None:
                index = self.report.xModeCombo.findData(mode)
                if index >= 0:
                    self.report.xModeCombo.setCurrentIndex(index)
            self.tabs.setTabEnabled(1, True)
            self.tabs.setCurrentIndex(1)
            text = f"DCR 계산 완료 · {len(self.result_rows)} Pulse · 측정 범위 밖의 값은 빈칸으로 남겼습니다."
            self.message.setText(text)
            self.statusMessage.emit(text)
            if self.result_rows:
                self._preview_result(self.result_rows[0])

        self._run("선택한 Pulse의 시점별 DCR을 계산하고 있습니다…", work, calculated)

    def _on_edit(self, item):
        if self._applying or self.detection is None:
            return
        new = self.review.edits()
        if new != self._edit_snapshot:
            self._history.setdefault(self.active_id, []).append(copy.deepcopy(self._edit_snapshot))
            self._history[self.active_id] = self._history[self.active_id][-50:]
            self._edit_snapshot = new
            self.undoButton.setEnabled(True)
            self.tabs.setTabEnabled(1, False)
            self.result_valid = False
            self.message.setText("검토 값이 변경되었습니다. 재분석하면 결과에 반영됩니다.")
            self._store_current()

    def undo_edit(self):
        history = self._history.get(self.active_id, [])
        if not history:
            return
        edits = history.pop()
        self.review.apply_edits(edits)
        self._edit_snapshot = self.review.edits()
        self.undoButton.setEnabled(bool(history))
        self.tabs.setTabEnabled(1, False)
        self.result_valid = False
        self._store_current()

    def reset_edits(self):
        if self.active_id is None:
            return
        self._history.setdefault(self.active_id, []).append(self.review.edits())
        self._saved.setdefault(self.active_id, {})["edits"] = {}
        if self.detection is not None:
            self._applying = True
            try:
                self.review.set_detection(self.detection)
                self._edit_snapshot = self.review.edits()
            finally:
                self._applying = False
        self.tabs.setTabEnabled(1, False)
        self.result_valid = False
        self.undoButton.setEnabled(True)
        self.message.setText("수동 SOC·블록·포함 설정을 초기 탐지 상태로 복원했습니다.")

    def _preview_review(self, row, column):
        if self.detection is None or self._df is None or row >= len(self.review.pulse_rows):
            return
        seg_index = self.review.pulse_rows[row]["seg_index"]
        for block in self.detection["blocks"]:
            for group in block["pulse_groups"]:
                for pulse in group["pulses"]:
                    if pulse["seg"] == seg_index:
                        try:
                            result = compute_pulse_result(self._df, self.detection["segments"], pulse,
                                                          self.review._parse_time_points(), ordered=self._df)
                            self._show_preview(result)
                        except (ValueError, TypeError) as exc:
                            self.review.set_error(str(exc))
                        return

    def _preview_result(self, row):
        if not isinstance(row, dict):
            return
        seg = row.get("_seg")
        for curve in self.result_curves:
            if curve.get("seg_index") == seg:
                self._show_preview({"raw_time_s": curve["time_s"],
                                    "raw_voltage_v": curve["voltage_v"],
                                    "raw_current_mA": curve["current_series_mA"],
                                    "v0": curve["v0"], "v0_source": curve["v0_source"],
                                    "rest_current_mA": curve["rest_current_mA"],
                                    "v_by_t": curve.get("sampled_v_by_t", {}),
                                    "control_mode": row.get("Control_Mode", "")})
                return

    def _show_preview(self, values):
        self.preview.clear()
        self.previewHover.reattach()
        self.currentPreview.clear()
        t = values["raw_time_s"]
        self.preview.plot(t, values["raw_voltage_v"], pen=pg.mkPen("#39bdb3", width=1.5), name="Raw 전압")
        self.currentPreview.plot(t, values["raw_current_mA"], pen=pg.mkPen("#f4b942", width=1.5), name="Raw 전류")
        v0 = values.get("v0", np.nan)
        if np.isfinite(v0):
            self.preview.addItem(pg.InfiniteLine(v0, angle=0, pen=pg.mkPen("#e49c82", style=Qt.PenStyle.DashLine)))
        points = [(time, voltage) for time, voltage in values.get("v_by_t", {}).items() if np.isfinite(voltage)]
        if points:
            self.preview.plot([point[0] for point in points], [point[1] for point in points],
                              pen=None, symbol="o", symbolSize=8, symbolBrush="#e69d83", name="DCR 시점")
        source = {"pulse_t0": "Pulse 0초", "previous_rest": "직전 Rest", "first_valid_pulse_sample": "첫 유효 Pulse 대체"}.get(values.get("v0_source"), "미지정")
        self.previewLabel.setText(f"원본 Pulse · V0={v0:.6g} V ({source}) · 휴지전류={values.get('rest_current_mA', 0):.6g} mA · {values.get('control_mode', '')}")

    def _output_path(self, filename, file_filter):
        try:
            from .project import default_output_dir
            directory = str(default_output_dir())
        except Exception:
            directory = str(Path.home() / "Downloads")
        path, _ = QFileDialog.getSaveFileName(self, "DCR 결과 저장", str(Path(directory) / filename), file_filter)
        return path

    def export_csv(self, kind="raw", time_point=None):
        if not self.result_rows or not self.result_valid:
            return
        if kind == "summary":
            columns, rows = self.report.summary_export_rows(float(time_point))
        elif kind == "chart":
            columns, rows = self.report.chart_export_rows()
        else:
            frame = pd.DataFrame(self.result_rows).drop(columns=["Settings"], errors="ignore")
            columns, rows = frame.columns.tolist(), frame.values.tolist()
        path = self._output_path(f"DCR_{kind}.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                writer.writerows([["" if value is None or isinstance(value, (float, np.floating)) and not np.isfinite(value) else value for value in row] for row in rows])
            self.statusMessage.emit(f"저장했습니다: {path}")
        except OSError as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))

    def export_excel(self):
        if not self.result_rows or not self.result_valid:
            return
        path = self._output_path("DCR_results.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                pd.DataFrame(self.result_rows).drop(columns=["Settings"], errors="ignore").to_excel(writer, sheet_name="DCR_Raw", index=False)
                for tp in self.report.time_points:
                    columns, rows = self.report.summary_export_rows(tp)
                    pd.DataFrame(rows, columns=columns).to_excel(writer, sheet_name=f"DCR_{tp:g}s"[:31], index=False)
                self._export_pulse_rows(writer)
                settings = self._settings()
                pd.DataFrame(list(settings.items()), columns=["Setting", "Value"]).to_excel(writer, sheet_name="Settings", index=False)
            self.statusMessage.emit(f"저장했습니다: {path}")
        except Exception as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))

    def _export_pulse_rows(self, writer):
        rows = []
        sheet = 1
        for curve in self.result_curves:
            for time, voltage, current in zip(curve["time_s"], curve["voltage_v"], curve["current_series_mA"]):
                rows.append({"Source_File": curve["source_file"], "PhysicalStepID": curve.get("physical_step_id"),
                             "Pulse_Index": curve["pulse_index"], "Time_s": time, "Voltage_V": voltage, "Current_mA": current})
                if len(rows) >= 1_000_000:
                    pd.DataFrame(rows).to_excel(writer, sheet_name=f"Pulse_Raw_{sheet}", index=False)
                    rows.clear()
                    sheet += 1
        if rows:
            pd.DataFrame(rows).to_excel(writer, sheet_name=f"Pulse_Raw_{sheet}", index=False)

    def export_png(self):
        path = self._output_path("DCR_plot.png", "PNG (*.png)")
        if path:
            try:
                import pyqtgraph.exporters
                plot = self.report.plot if self.tabs.currentIndex() == 1 else self.preview
                pyqtgraph.exporters.ImageExporter(plot.plotItem).export(path)
                self.statusMessage.emit(f"저장했습니다: {path}")
            except Exception as exc:
                QMessageBox.warning(self, "저장 실패", str(exc))

    def shutdown(self, timeout_ms=10000):
        self.cancel()
        for job in list(self._jobs):
            if not job.wait(timeout_ms):
                return False
        return True

    def closeEvent(self, event):
        if self.shutdown():
            event.accept()
        else:
            event.ignore()
