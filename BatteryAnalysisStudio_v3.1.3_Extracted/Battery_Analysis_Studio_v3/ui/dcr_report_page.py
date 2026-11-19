# -*- coding: utf-8 -*-
"""
dcr_report_page.py
DCR 분석 최종 리포트: SOC(또는 기준전압)에 따른 DCIR 변화를 시간시점/방향/전류별로 보여주고,
전체 결과 테이블과 raw 펄스 곡선을 내보낼 수 있게 한다.
"""
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton, QComboBox,
    QScrollArea, QTableWidgetItem, QHeaderView, QCheckBox, QTabWidget, QFileDialog
)
from PySide6.QtCore import Qt, Signal

from . import theme
from .widgets import StatCard, AxisRangeControl, section_title, CopyableTableWidget
from .report_page import configure_plot_interaction, HoverReadout, make_hover_checkbox, fmt


class DCRFileDropZone(QFrame):
    """결과 화면 좌측에서 추가 DCR 파일을 바로 받는 작은 드롭 영역."""
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(58)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        label = QLabel("여기로 DCR CSV를 드래그해 추가")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:10px;")
        layout.addWidget(label)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()


class DCRReportPage(QWidget):
    exportTableRequested = Signal()
    exportChartRequested = Signal()
    exportSummaryRequested = Signal(float)
    addFilesRequested = Signal(list)
    exportAllRequested = Signal()
    pulseSelected = Signal(object)

    PALETTE = [theme.TEAL, theme.AMBER, theme.CORAL, theme.PURPLE, theme.BLUE,
               theme.STEEL, theme.GOLD, theme.GREEN, theme.ORANGE, theme.PURPLE_DARK]

    def __init__(self, parent=None, integrated=False):
        super().__init__(parent)
        self.integrated = integrated
        self.rows = []           # 전체 결과 행 (dict)
        self.time_points = []    # [1, 10, 30, ...]
        self.has_soc = False
        self.source_files = []
        self.active_sources = set()

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._build_file_sidebar(outer)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, 1)

        content = QWidget()
        self.root = QVBoxLayout(content)
        self.root.setSpacing(18)
        scroll.setWidget(content)

        self._build_stat_cards()
        self._build_chart_panel()
        self._build_summary_table_panel()
        self._build_table_panel()
        self.root.addStretch(1)

    # ------------------------------------------------------------------
    def _panel(self):
        f = QFrame()
        f.setObjectName("panel")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        self.root.addWidget(f)
        return lay

    def _build_file_sidebar(self, outer):
        sidebar = QFrame()
        sidebar.setObjectName("panel")
        sidebar.setFixedWidth(218)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.addWidget(section_title("표시 파일"))
        hint = QLabel("체크한 파일만 차트와 정리표에 표시됩니다.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:10px;")
        layout.addWidget(hint)
        buttons = QHBoxLayout()
        btnAll = QPushButton("전체")
        btnAll.setStyleSheet("padding:4px 8px;")
        btnAll.clicked.connect(lambda: self._set_all_sources_checked(True))
        btnNone = QPushButton("해제")
        btnNone.setStyleSheet("padding:4px 8px;")
        btnNone.clicked.connect(lambda: self._set_all_sources_checked(False))
        buttons.addWidget(btnAll)
        buttons.addWidget(btnNone)
        layout.addLayout(buttons)

        btnAdd = QPushButton("+ 파일 추가 분석")
        btnAdd.setProperty("variant", "primary")
        btnAdd.clicked.connect(self._browse_add_files)
        layout.addWidget(btnAdd)
        self.fileDropZone = DCRFileDropZone()
        self.fileDropZone.filesDropped.connect(self.addFilesRequested.emit)
        layout.addWidget(self.fileDropZone)
        if self.integrated:
            btnAdd.hide()
            self.fileDropZone.hide()

        self.sourceScroll = QScrollArea()
        self.sourceScroll.setWidgetResizable(False)
        self.sourceListWidget = QWidget()
        self.sourceCheckLayout = QVBoxLayout()
        self.sourceCheckLayout.setSpacing(6)
        self.sourceCheckLayout.setContentsMargins(2, 2, 2, 2)
        self.sourceListWidget.setLayout(self.sourceCheckLayout)
        self.sourceScroll.setWidget(self.sourceListWidget)
        layout.addWidget(self.sourceScroll, 1)
        self.sourceEmptyLabel = QLabel("아직 결과가 없습니다.")
        self.sourceEmptyLabel.setWordWrap(True)
        self.sourceEmptyLabel.setStyleSheet(f"color:{theme.SUBTLE}; font-size:10px;")
        layout.addWidget(self.sourceEmptyLabel)
        self.sourceChecks = {}
        outer.addWidget(sidebar)

    def _browse_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "추가 분석할 DCR CSV 파일 선택", "", "CSV Files (*.csv);;All Files (*.*)")
        if paths:
            self.addFilesRequested.emit(paths)

    def _refresh_source_sidebar(self):
        while self.sourceCheckLayout.count():
            item = self.sourceCheckLayout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.sourceChecks = {}
        self.sourceEmptyLabel.setVisible(not self.source_files)
        required_width = 190
        for source in self.source_files:
            check = QCheckBox(source)
            check.setChecked(source in self.active_sources)
            check.setToolTip(source)
            check.toggled.connect(lambda checked, name=source: self._on_source_toggled(name, checked))
            self.sourceCheckLayout.addWidget(check)
            self.sourceChecks[source] = check
            required_width = max(required_width, check.fontMetrics().horizontalAdvance(source) + 30)
        # QScrollArea(widgetResizable=False)에서는 콘텐츠 높이를 명시하지 않으면 레이아웃이
        # 0~수 px로 축소되어 체크박스가 보이지 않을 수 있다.
        self.sourceCheckLayout.activate()
        required_height = max(28, self.sourceCheckLayout.sizeHint().height() + 4)
        self.sourceListWidget.setMinimumSize(required_width, required_height)
        self.sourceListWidget.resize(required_width, required_height)

    def _set_all_sources_checked(self, checked: bool):
        for check in self.sourceChecks.values():
            check.setChecked(checked)

    def _on_source_toggled(self, source: str, checked: bool):
        if checked:
            self.active_sources.add(source)
        else:
            self.active_sources.discard(source)
        self._refresh_visible_results()

    def _visible_rows(self):
        return [row for row in self.rows if row.get("Source_File", "입력 파일") in self.active_sources]

    def selected_source_files(self):
        """현재 화면/내보내기에 포함할 파일 이름 집합을 반환한다."""
        return set(self.active_sources)

    def _build_stat_cards(self):
        row = QHBoxLayout()
        row.setSpacing(12)
        self.cardBlocks = StatCard("SOC 블록 수")
        self.cardPulses = StatCard("총 펄스 수")
        self.cardSocRange = StatCard("SOC/기준전압 범위")
        self.cardTimePoints = StatCard("확인 시간 시점")
        for c in (self.cardBlocks, self.cardPulses, self.cardSocRange, self.cardTimePoints):
            row.addWidget(c)
        self.root.addLayout(row)

    def _build_chart_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("SOC별 DCIR"))
        header.addStretch(1)
        self.xModeCombo = QComboBox()
        self.xModeCombo.addItem("SOC (%)", "soc")
        self.xModeCombo.addItem("기준전압 (V)", "voltage")
        self.xModeCombo.addItem("SOC 블록 번호", "block")
        self.xModeCombo.currentIndexChanged.connect(self._refresh_visible_results)
        header.addWidget(self.xModeCombo)
        header.addWidget(QLabel("방향"))
        self.directionCombo = QComboBox()
        self.directionCombo.addItem("방전", "discharge")
        self.directionCombo.addItem("충전", "charge")
        self.directionCombo.currentIndexChanged.connect(self._update_chart)
        header.addWidget(self.directionCombo)
        header.addSpacing(10)
        header.addWidget(QLabel("시간 시점"))
        self.timePointCombo = QComboBox()
        self.timePointCombo.currentIndexChanged.connect(self._update_chart)
        header.addWidget(self.timePointCombo)
        btnExp = QPushButton("이 차트 데이터 다운로드")
        btnExp.clicked.connect(self.exportChartRequested.emit)
        header.addWidget(btnExp)
        lay.addLayout(header)

        axis_row = QHBoxLayout()
        self.axisX = AxisRangeControl("X축(SOC%/전압)")
        self.axisY = AxisRangeControl("DCIR(mΩ) 축")
        self.axisX.changed.connect(self._update_chart)
        self.axisY.changed.connect(self._update_chart)
        axis_row.addWidget(self.axisX)
        axis_row.addSpacing(20)
        axis_row.addWidget(self.axisY)
        axis_row.addStretch(1)

        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(360)
        self.plot.addLegend(offset=(-10, 10))
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("bottom", "SOC (%)")
        self.plot.setLabel("left", "DCIR (mΩ)")
        configure_plot_interaction(self.plot)
        self.hover = HoverReadout(self.plot, y_label="DCIR(mΩ)")
        axis_row.addWidget(make_hover_checkbox([self.hover]))
        lay.addLayout(axis_row)
        lay.addWidget(self.plot)
        self.chartNote = QLabel("")
        self.chartNote.setWordWrap(True)
        lay.addWidget(self.chartNote)

    def _build_table_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("원본 결과 테이블 (Raw)"))
        self.rawTableToggle = QCheckBox("원본 결과 펼치기")
        self.rawTableToggle.setChecked(False)
        self.rawTableToggle.toggled.connect(self._set_raw_table_visible)
        header.addWidget(self.rawTableToggle)
        self.rawColumnLabel = QLabel("보조 열")
        header.addWidget(self.rawColumnLabel)
        optional_columns = [
            ("매칭 OCV", "matched_ocv"), ("매칭 오차", "match_error"),
            ("V0 기준", "v0_source"), ("펄스#", "pulse_index"), ("지속시간", "duration"),
        ]
        self.optionalColumnChecks = {}
        self.rawColumnControls = [self.rawColumnLabel]
        for label, key in optional_columns:
            check = QCheckBox(label)
            check.toggled.connect(self._render_table)
            self.optionalColumnChecks[key] = check
            header.addWidget(check)
            self.rawColumnControls.append(check)
        header.addStretch(1)
        btnCsv = QPushButton("Raw 테이블 CSV 다운로드")
        btnCsv.clicked.connect(self.exportTableRequested.emit)
        header.addWidget(btnCsv)
        btnAll = QPushButton("펄스 원자료 포함 전체 내보내기 (Excel)")
        btnAll.clicked.connect(self.exportAllRequested.emit)
        header.addWidget(btnAll)
        lay.addLayout(header)

        self.table = CopyableTableWidget()
        self.table.setMinimumHeight(360)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.cellClicked.connect(self._select_pulse_row)
        self.table.setVisible(False)
        lay.addWidget(self.table)
        self._set_raw_table_visible(False)

    def _build_summary_table_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("정리 결과 테이블"))
        header.addStretch(1)
        header.addWidget(QLabel("방향·전류별 DCIR(mΩ) / SOC(기준전압)"))
        btnSummary = QPushButton("현재 탭 CSV 다운로드")
        btnSummary.clicked.connect(self._emit_summary_export)
        header.addWidget(btnSummary)
        lay.addLayout(header)

        desc = QLabel(
            "각 탭은 제목에 적힌 시간 시점의 저항값입니다. 행은 방전·충전 전류, 열은 SOC(또는 기준전압)이며, "
            "동일 조건의 반복 측정은 별도 반복 행으로 보존합니다."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        lay.addWidget(desc)

        self.summaryTabs = QTabWidget()
        self.summaryTabs.setMinimumHeight(320)
        self.summaryTables = {}
        lay.addWidget(self.summaryTabs)

    def _set_raw_table_visible(self, visible: bool):
        self.table.setVisible(visible)
        for control in self.rawColumnControls:
            control.setVisible(visible)

    # ------------------------------------------------------------------
    def set_results(self, rows: list, time_points: list):
        self.rows = rows
        self.time_points = time_points
        self.has_soc = any(r.get("SOC_pct") is not None and np.isfinite(r["SOC_pct"]) for r in rows)
        self.source_files = list(dict.fromkeys(r.get("Source_File", "입력 파일") for r in rows))
        self.active_sources = set(self.source_files)
        self._refresh_source_sidebar()

        self.timePointCombo.blockSignals(True)
        self.timePointCombo.clear()
        for tp in time_points:
            self.timePointCombo.addItem(f"{tp:g}s", tp)
        self.timePointCombo.blockSignals(False)

        self.xModeCombo.blockSignals(True)
        self.xModeCombo.setCurrentIndex(0 if self.has_soc else 2)
        self.xModeCombo.blockSignals(False)

        self._refresh_visible_results()

    def _refresh_visible_results(self):
        rows = self._visible_rows()
        n_blocks = len(set((r.get("Source_File"), r["Block"]) for r in rows))
        socs = [r.get("SOC_pct") for r in rows if r.get("SOC_pct") is not None and np.isfinite(r.get("SOC_pct"))]
        vrefs = [r.get("Reference_Voltage_V") for r in rows if r.get("Reference_Voltage_V") is not None and np.isfinite(r.get("Reference_Voltage_V"))]
        if socs:
            soc_range = f"{min(socs):.1f}% ~ {max(socs):.1f}%"
        elif vrefs:
            soc_range = f"{min(vrefs):.3f}V ~ {max(vrefs):.3f}V"
        else:
            soc_range = "-"

        self.cardBlocks.set_value(str(n_blocks))
        self.cardPulses.set_value(str(len(rows)))
        self.cardSocRange.set_value(soc_range)
        self.cardTimePoints.set_value(", ".join(f"{tp:g}s" for tp in self.time_points))

        self._update_chart()
        self._render_summary_tables()
        self._render_table()

    def _x_value(self, row: dict):
        mode = self.xModeCombo.currentData()
        key = "SOC_pct" if mode == "soc" else "Reference_Voltage_V" if mode == "voltage" else "Block"
        value = row.get(key)
        return float(value) if value is not None and np.isfinite(value) else None

    @staticmethod
    def _current_text(value):
        """1 A 이상은 보기 쉬운 A 단위로, 그 외는 mA 단위로 표시한다."""
        if value is None or not np.isfinite(value):
            return "-"
        if abs(value) >= 1000.0:
            return f"{value / 1000.0:.2f} A"
        return f"{value:.2f} mA"

    def _update_chart(self):
        self.plot.clear()
        self.hover.reattach()
        if not self.rows or self.timePointCombo.count() == 0:
            return
        direction = self.directionCombo.currentData()
        tp = self.timePointCombo.currentData()
        if tp is None:
            return
        col = f"R_{tp:g}s_mOhm"
        self.plot.setLabel("bottom", {"soc": "SOC (%)", "voltage": "기준전압 (V)", "block": "SOC 블록 번호"}[self.xModeCombo.currentData()])
        filtered = [r for r in self._visible_rows() if r["Direction"] == direction]
        omitted = sum(self._x_value(r) is None for r in filtered)
        self.chartNote.setText(
            f"현재 축 값 미지정 {omitted}개는 그래프에서 제외했습니다. 원본 결과표에서는 확인할 수 있습니다."
            if omitted else "파일·채널·반복별 곡선을 구분하며, 반복값을 평균으로 합치지 않습니다."
        )
        groups = {}
        for row in filtered:
            current = row.get("Current_mA")
            if current is None or not np.isfinite(current) or self._x_value(row) is None:
                continue
            key = (str(row.get("Source_ID", row.get("Source_File", ""))),
                   str(row.get("Channel", "")), round(float(current), 2))
            groups.setdefault(key, []).append(row)
        for i, ((source, channel, cur), points) in enumerate(sorted(groups.items())):
            by_x = {}
            for row in sorted(points, key=lambda r: r.get("_seg", r.get("Pulse_Index", 0))):
                by_x.setdefault(self._x_value(row), []).append(row)
            repeats = max(map(len, by_x.values()), default=0)
            for repeat in range(repeats):
                pts = [(x, rows[repeat]) for x, rows in sorted(by_x.items()) if repeat < len(rows)]
                xs = np.array([x for x, row in pts], dtype=float)
                ys = np.array([row.get(col, np.nan) for x, row in pts], dtype=float)
                color = self.PALETTE[i % len(self.PALETTE)]
                label_source = points[0].get("Source_File", source)
                label = f"{label_source} {channel} · {self._current_text(cur)}"
                if repeats > 1:
                    label += f" · 반복 {repeat + 1}"
                self.plot.plot(xs, ys, pen=pg.mkPen(color, width=1.5, style=Qt.PenStyle.SolidLine if repeat == 0 else Qt.PenStyle.DashLine),
                               symbol="o", symbolSize=6, symbolBrush=color, symbolPen=None, name=label)

        xr = self.axisX.value_range()
        if xr:
            self.plot.setXRange(xr[0], xr[1], padding=0)
        else:
            self.plot.enableAutoRange(axis=pg.ViewBox.XAxis)
        yr = self.axisY.value_range()
        if yr:
            self.plot.setYRange(yr[0], yr[1], padding=0)
        else:
            self.plot.enableAutoRange(axis=pg.ViewBox.YAxis)

    def _table_columns(self):
        """기본 열과 선택된 보조 열을 반환한다."""
        base_cols = [("파일", "Source_File"), ("SOC블록", "Block"), ("SOC(%)", "SOC_pct"), ("기준전압(V)", "Reference_Voltage_V"),
                     ("방향", "Direction"), ("전류", "Current_mA"),
                     ("Cycle", "Cycle"), ("Step", "StepNo"), ("V0(V)", "V0_V"), ("I0", "I0_mA"),
                     ("휴지전류", "Rest_Current_mA"), ("제어", "Control_Mode")]
        optional = self.optionalColumnChecks
        if optional["matched_ocv"].isChecked():
            base_cols.insert(3, ("매칭 OCV(V)", "SOC_Matched_OCV_V"))
        if optional["match_error"].isChecked():
            insert_at = 4 if optional["matched_ocv"].isChecked() else 3
            base_cols.insert(insert_at, ("매칭오차(mV)", "SOC_Match_Delta_mV"))
        if optional["v0_source"].isChecked():
            base_cols.append(("V0 기준", "V0_Source"))
        if optional["pulse_index"].isChecked():
            base_cols.append(("펄스#", "Pulse_Index"))
        if optional["duration"].isChecked():
            base_cols.append(("지속시간(s)", "Pulse_Duration_s"))
        for tp in self.time_points:
            base_cols.extend([(f"R_{tp:g}s(mΩ)", f"R_{tp:g}s_mOhm"),
                              (f"ΔI_{tp:g}s(mA)", f"Delta_I_{tp:g}s_mA"),
                              (f"{tp:g}s 상태", f"Status_{tp:g}s")])
        return base_cols

    def _summary_soc_columns(self):
        """정리표의 SOC/기준전압 열 정의를 원본 행에서 구성한다."""
        columns = {}
        for row in self._visible_rows():
            key = self._summary_key(row)
            mode = self.xModeCombo.currentData()
            value = self._x_value(row)
            if value is not None:
                label = f"{value:g}%" if mode == "soc" else f"{value:.3f} V" if mode == "voltage" else f"Block {value:g}"
                sort_value = (0, float(value))
            else:
                label = f"미지정\nBlock {row.get('Block', '-')}"
                sort_value = (1, float(row.get("Block") or 0))
            columns.setdefault(key, (sort_value, label))
        return [(key, label) for key, (_, label) in sorted(columns.items(), key=lambda item: item[1][0])]

    def _summary_key(self, row: dict):
        value = self._x_value(row)
        if value is not None:
            return self.xModeCombo.currentData(), value
        return "unknown", row.get("Block")

    def _summary_matrix(self, tp: float):
        """한 시간 시점의 방향·전류 × SOC 매트릭스를 반환한다.

        중복 측정은 평균내지 않고 Pulse_Index 순서의 반복 행으로 유지한다.
        """
        soc_columns = self._summary_soc_columns()
        headers = ["파일/채널", "방향", "전류", "반복"] + [label for _, label in soc_columns]
        value_column = f"R_{tp:g}s_mOhm"
        groups = {}
        for row in self._visible_rows():
            direction = row.get("Direction")
            current = row.get("Current_mA")
            if direction not in ("discharge", "charge") or current is None or not np.isfinite(current):
                continue
            key = (str(row.get("Source_ID", row.get("Source_File", ""))),
                   str(row.get("Source_File", "")), str(row.get("Channel", "")),
                   direction, round(float(current), 2))
            groups.setdefault(key, []).append(row)

        matrix_rows = []
        direction_order = {"discharge": 0, "charge": 1}
        for (source_id, source, channel, direction, current), points in sorted(
            groups.items(), key=lambda item: (item[0][0], direction_order[item[0][3]], abs(item[0][4]), item[0][4])
        ):
            by_soc = {}
            for point in sorted(points, key=lambda r: (r.get("Pulse_Index") or 0, r.get("Block") or 0)):
                by_soc.setdefault(self._summary_key(point), []).append(point)
            repeat_count = max((len(values) for values in by_soc.values()), default=1)
            for repeat_index in range(repeat_count):
                cells = []
                for soc_key, _ in soc_columns:
                    values = by_soc.get(soc_key, [])
                    value = values[repeat_index].get(value_column) if repeat_index < len(values) else None
                    cells.append(value)
                matrix_rows.append([
                    f"{source} {channel}".strip(),
                    "방전" if direction == "discharge" else "충전",
                    self._current_text(current),
                    f"반복 {repeat_index + 1}" if repeat_count > 1 else "",
                    *cells,
                ])
        return headers, matrix_rows

    def _render_summary_tables(self):
        self.summaryTabs.clear()
        self.summaryTables = {}
        for tp in self.time_points:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(8, 8, 8, 8)
            title = QLabel(f"DCIR ({tp:g} s) · 단위: mΩ")
            title.setStyleSheet(f"font-family:'{theme.FONT_DISPLAY}'; font-size:15px; font-weight:700; color:{theme.TEAL};")
            layout.addWidget(title)

            table = CopyableTableWidget()
            headers, matrix_rows = self._summary_matrix(tp)
            table.setColumnCount(len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.setRowCount(len(matrix_rows))
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            table.verticalHeader().setVisible(False)
            for row_index, values in enumerate(matrix_rows):
                for column_index, value in enumerate(values):
                    text = fmt(value, 3) if isinstance(value, (float, np.floating)) else str(value)
                    item = QTableWidgetItem(text if text != "None" else "-")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    table.setItem(row_index, column_index, item)
            table.resizeColumnsToContents()
            layout.addWidget(table)
            self.summaryTabs.addTab(page, f"DCIR ({tp:g} s)")
            self.summaryTables[tp] = table

    def summary_export_rows(self, tp: float):
        return self._summary_matrix(tp)

    def _emit_summary_export(self):
        index = self.summaryTabs.currentIndex()
        if 0 <= index < len(self.time_points):
            self.exportSummaryRequested.emit(float(self.time_points[index]))

    def _render_table(self):
        cols = self._table_columns()

        self.table.setSortingEnabled(False)
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels([c[0] for c in cols])
        visible_rows = self._visible_rows()
        self.table.setRowCount(len(visible_rows))
        for i, r in enumerate(visible_rows):
            for j, (_, key) in enumerate(cols):
                v = r.get(key)
                if key == "Direction":
                    text = "방전" if v == "discharge" else "충전"
                elif key in ("Current_mA", "I0_mA"):
                    text = self._current_text(v)
                elif key == "V0_Source":
                    text = {"pulse_t0": "펄스 0.0s", "previous_rest": "직전 REST 마지막",
                            "first_valid_pulse_sample": "첫 유효 펄스값"}.get(v, "-")
                elif isinstance(v, float):
                    text = fmt(v, 3)
                else:
                    text = str(v) if v is not None else "-"
                item = QTableWidgetItem(text)
                if j == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, j, item)
        self.table.setSortingEnabled(True)

    def _select_pulse_row(self, row, column):
        item = self.table.item(row, 0)
        if item is not None:
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict):
                self.pulseSelected.emit(data)

    def table_export_rows(self):
        cols = [key for _, key in self._table_columns()]
        rows = [[r.get(c) for c in cols] for r in self._visible_rows()]
        return cols, rows

    def chart_export_rows(self):
        direction = self.directionCombo.currentData()
        tp = self.timePointCombo.currentData()
        col = f"R_{tp:g}s_mOhm" if tp is not None else None
        cols = ["Source_File", "Channel", "PhysicalStepID", "Pulse_Index", "Block", "SOC_pct", "Reference_Voltage_V", "Current_mA", col or "R_mOhm"]
        rows = []
        for r in self._visible_rows():
            if r["Direction"] != direction:
                continue
            if self._x_value(r) is None:
                continue
            rows.append([r.get(c) for c in cols])
        return cols, rows
