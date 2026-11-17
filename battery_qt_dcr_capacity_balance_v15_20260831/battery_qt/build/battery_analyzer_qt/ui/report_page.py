# -*- coding: utf-8 -*-
"""
report_page.py
3단계: 분석 리포트 화면. 전부 네이티브 Qt 위젯 + PyQtGraph 차트 (브라우저 엔진 없음).
"""
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel, QPushButton,
    QCheckBox, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QScrollArea, QFileDialog, QMessageBox, QLineEdit, QComboBox
)
from PySide6.QtCore import Qt, Signal

from . import theme
from .widgets import StatCard, AxisRangeControl, section_title, parse_cycle_spec

pg.setConfigOption("background", theme.PANEL_INPUT)
pg.setConfigOption("foreground", theme.TEXT)
pg.setConfigOptions(antialias=True)


def configure_plot_interaction(plot_widget: pg.PlotWidget):
    """마우스 휠 확대/축소는 끄고, 드래그로 원하는 영역을 확대(rect-zoom)하도록 설정.
    (축소/리셋은 각 차트의 '자동' 축 버튼을 누르거나 우클릭 메뉴의 'View All'을 사용)

    휠 이벤트는 확대/축소에 쓰지 않고 그대로 무시(ignore)해서, 차트 위에서 휠을 굴려도
    화면(스크롤 영역)이 계속 스크롤되도록 한다 (차트에서 스크롤이 먹통되는 문제 방지)."""
    vb = plot_widget.getPlotItem().vb
    vb.setMouseMode(pg.ViewBox.RectMode)

    def _no_wheel(ev, axis=None):
        ev.ignore()

    vb.wheelEvent = _no_wheel

    def _widget_wheel(event):
        event.ignore()  # 부모(QScrollArea)로 이벤트를 넘겨 페이지 스크롤이 계속되게 함

    plot_widget.wheelEvent = _widget_wheel


class HoverReadout:
    """차트 위에 마우스를 올리면 실제 데이터 포인트에 스냅되어 좌표값을 보여주는 십자선 + 텍스트.
    on/off 토글 가능. (마우스 위치의 임의 좌표가 아니라, 가장 가까운 실제 데이터 점의 값을 표시)"""

    def __init__(self, plot_widget: pg.PlotWidget, secondary_vb=None, y_label="y", y2_label="y2"):
        self.plot_widget = plot_widget
        self.secondary_vb = secondary_vb
        self.y_label = y_label
        self.y2_label = y2_label
        self.enabled = False

        pen = pg.mkPen(theme.SUBTLE, width=1, style=Qt.PenStyle.DashLine)
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.hLine = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self.label = pg.TextItem(anchor=(0, 1), color=theme.TEXT, fill=pg.mkBrush("#0d1316cc"))
        for item in (self.vLine, self.hLine, self.label):
            item.setVisible(False)
            plot_widget.addItem(item, ignoreBounds=True)

        self._proxy = pg.SignalProxy(plot_widget.scene().sigMouseMoved, rateLimit=60, slot=self._on_move)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        for item in (self.vLine, self.hLine, self.label):
            item.setVisible(enabled)

    def reattach(self):
        """plot_widget.clear() 호출 후에도 십자선/라벨이 사라지지 않도록 다시 추가한다."""
        for item in (self.vLine, self.hLine, self.label):
            try:
                self.plot_widget.addItem(item, ignoreBounds=True)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _nearest_point(items, x_query):
        """items(PlotDataItem 목록) 중 x_query에 x가 가장 가까운 실제 데이터 포인트를 찾는다.
        반환: (x, y, curve_name) 또는 None"""
        best = None
        best_dx = float("inf")
        for it in items:
            try:
                x_arr = it.xData
                y_arr = it.yData
            except Exception:  # noqa: BLE001
                continue
            if x_arr is None or y_arr is None or len(x_arr) == 0:
                continue
            idx = int(np.argmin(np.abs(x_arr - x_query)))
            dx = abs(x_arr[idx] - x_query)
            if dx < best_dx:
                best_dx = dx
                name = None
                try:
                    name = it.name()
                except Exception:  # noqa: BLE001
                    pass
                best = (float(x_arr[idx]), float(y_arr[idx]), name)
        return best

    def _on_move(self, evt):
        if not self.enabled:
            return
        pos = evt[0]
        plot_item = self.plot_widget.getPlotItem()
        vb = plot_item.vb
        if not plot_item.sceneBoundingRect().contains(pos):
            return
        pt = vb.mapSceneToView(pos)

        primary_items = plot_item.listDataItems()
        best = self._nearest_point(primary_items, pt.x())
        if best is None:
            # 이 차트에 아직 그려진 데이터가 없으면 표시하지 않는다
            for item in (self.vLine, self.hLine, self.label):
                item.setVisible(False)
            return
        for item in (self.vLine, self.hLine, self.label):
            item.setVisible(True)

        x0, y0, name0 = best
        label0 = f"{name0}: " if name0 else ""
        text = f"x={x0:.4g}\n{label0}{self.y_label}={y0:.4g}"

        if self.secondary_vb is not None:
            sec_items = list(getattr(self.secondary_vb, "addedItems", []))
            best2 = self._nearest_point(sec_items, x0)
            if best2 is not None:
                x2, y2v, name2 = best2
                label2 = f"{name2}: " if name2 else ""
                text += f"\n{label2}{self.y2_label}={y2v:.4g}"

        self.vLine.setPos(x0)
        self.hLine.setPos(y0)
        self.label.setText(text)
        self.label.setPos(x0, y0)


def make_hover_checkbox(target_readouts: list):
    chk = QCheckBox("커서 값 표시")
    chk.setStyleSheet(f"color:{theme.SUBTLE};")

    def _toggle(checked):
        for r in target_readouts:
            r.set_enabled(checked)

    chk.toggled.connect(_toggle)
    return chk


def fmt(v, digits=3):
    if v is None:
        return "-"
    try:
        if isinstance(v, float) and np.isnan(v):
            return "-"
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def fmt_duration(sec):
    if sec is None:
        return "-"
    try:
        if np.isnan(sec):
            return "-"
    except TypeError:
        return "-"
    total = int(round(sec))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _is_valid_num(v):
    if v is None:
        return False
    try:
        return not np.isnan(v)
    except TypeError:
        return True


def add_secondary_axis(plot_widget: pg.PlotWidget, right_label: str):
    """오른쪽에 독립된 y축(ViewBox)을 하나 추가해 반환한다 (dual-axis 차트용 표준 recipe)."""
    p1 = plot_widget.getPlotItem()
    p2 = pg.ViewBox()
    p1.showAxis("right")
    p1.scene().addItem(p2)
    p1.getAxis("right").linkToView(p2)
    p2.setXLink(p1)
    p1.getAxis("right").setLabel(right_label, color=theme.SUBTLE)

    def update_views():
        p2.setGeometry(p1.vb.sceneBoundingRect())
        p2.linkedViewChanged(p1.vb, p2.XAxis)

    p1.vb.sigResized.connect(update_views)
    update_views()
    return p2


class ReportPage(QWidget):
    cycleChanged = Signal(int)
    peakSensitivityChanged = Signal(float)
    exportVQRequested = Signal()
    exportDQDVRequested = Signal()
    exportTableRequested = Signal()
    exportExcelRequested = Signal()
    exportAllRequested = Signal()
    exportCapacityRequested = Signal()
    exportTempRequested = Signal()
    exportEnergyRequested = Signal()
    exportCCCVRequested = Signal()
    exportVTrendRequested = Signal()
    exportPeaksTrendRequested = Signal()
    exportDVDQRequested = Signal()
    exportRestDvdtRequested = Signal()
    exportMultiDqdvRequested = Signal()
    multiDqdvApplyRequested = Signal(str, str)  # cycle_spec_text, direction
    exportMultiRestRequested = Signal()
    multiRestApplyRequested = Signal(str, str)  # cycle_spec_text, after('charge'/'discharge')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.summary_all = []          # list[dict], 전체 사이클 요약
        self.min_cycle = 1
        self.max_cycle = 1
        self.selected_cycle = 1
        self.voltage_domain = (0, 5)
        self.capacity_domain = (0, 1000)
        self.current_detail = None
        self.cycle_from = None
        self.cycle_to = None
        self.last_peaks = []
        self.multi_curves = []
        self.multi_direction = "discharge"
        self.multi_rest_curves = []
        self.multi_rest_after = "discharge"
        self.source_files = []
        self.active_sources = set()

        self.show_chg = True
        self.show_dchg = True
        self.show_ce = True

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
        self._build_range_filter()
        self._build_capacity_energy_panel()
        self._build_temperature_panel()
        self._build_cccv_vtrend_panel()
        self._build_detail_panel()
        self._build_multi_dqdv_panel()
        self._build_multi_rest_panel()
        self._build_peaks_trend_panel()
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
        hint = QLabel("체크한 파일의 사이클만 그래프와 표에 표시됩니다.")
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
        self.sourceCheckLayout = QVBoxLayout()
        self.sourceCheckLayout.setSpacing(6)
        layout.addLayout(self.sourceCheckLayout)
        self.sourceEmptyLabel = QLabel("아직 분석된 파일이 없습니다.")
        self.sourceEmptyLabel.setWordWrap(True)
        self.sourceEmptyLabel.setStyleSheet(f"color:{theme.SUBTLE}; font-size:10px;")
        layout.addWidget(self.sourceEmptyLabel)
        layout.addStretch(1)
        self.sourceChecks = {}
        outer.addWidget(sidebar)

    def _refresh_source_sidebar(self):
        while self.sourceCheckLayout.count():
            item = self.sourceCheckLayout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.sourceChecks = {}
        self.sourceEmptyLabel.setVisible(not self.source_files)
        for source in self.source_files:
            check = QCheckBox(source)
            check.setChecked(source in self.active_sources)
            check.setToolTip(source)
            check.toggled.connect(lambda checked, name=source: self._on_source_toggled(name, checked))
            self.sourceCheckLayout.addWidget(check)
            self.sourceChecks[source] = check

    def _set_all_sources_checked(self, checked: bool):
        for check in self.sourceChecks.values():
            check.setChecked(checked)

    def _on_source_toggled(self, source: str, checked: bool):
        if checked:
            self.active_sources.add(source)
        else:
            self.active_sources.discard(source)
        self._update_stat_cards()
        self._update_capacity_chart()
        self._update_energy_chart()
        self._update_temp_chart()
        self._update_cccv_chart()
        self._update_vtrend_chart()
        self._render_table()

    def _build_stat_cards(self):
        grid = QGridLayout()
        grid.setSpacing(12)
        self.cardTotalCycles = StatCard("총 사이클")
        self.cardInitCap = StatCard("초기 방전용량", color=theme.TEAL)
        self.cardFinalCap = StatCard("최종 방전용량", color=theme.TEAL)
        self.cardRetention = StatCard("용량 유지율")
        self.cardAvgCE = StatCard("평균 쿨롱효율", color=theme.AMBER)
        self.cardFinalWh = StatCard("최종 방전에너지(Wh)", color=theme.PURPLE)
        cards = [self.cardTotalCycles, self.cardInitCap, self.cardFinalCap,
                 self.cardRetention, self.cardAvgCE, self.cardFinalWh]
        for i, c in enumerate(cards):
            grid.addWidget(c, 0, i)
        self.root.addLayout(grid)

    def _build_range_filter(self):
        lay = self._panel()
        row = QHBoxLayout()
        lbl = section_title("표시할 사이클 범위")
        lbl.setStyleSheet(f"font-family:'{theme.FONT_DISPLAY}'; font-size:13px; font-weight:600;")
        self.fromSpin = QSpinBox()
        self.fromSpin.setRange(1, 999999)
        self.toSpin = QSpinBox()
        self.toSpin.setRange(1, 999999)
        self.fromSpin.valueChanged.connect(self._on_range_changed)
        self.toSpin.valueChanged.connect(self._on_range_changed)
        btnAll = QPushButton("전체보기")
        btnAll.clicked.connect(self._reset_range)
        tilde = QLabel("~")
        hint = QLabel("아래 그래프와 요약 테이블에 모두 적용됩니다")
        hint.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        row.addWidget(lbl)
        row.addWidget(self.fromSpin)
        row.addWidget(tilde)
        row.addWidget(self.toSpin)
        row.addWidget(btnAll)
        row.addStretch(1)
        row.addWidget(hint)
        lay.addLayout(row)

    def _build_capacity_energy_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("사이클별 용량 & 쿨롱효율"))
        header.addStretch(1)
        self.chkChg = QCheckBox("충전")
        self.chkChg.setChecked(True)
        self.chkChg.setStyleSheet(f"color:{theme.AMBER};")
        self.chkDchg = QCheckBox("방전")
        self.chkDchg.setChecked(True)
        self.chkDchg.setStyleSheet(f"color:{theme.TEAL};")
        self.chkCE = QCheckBox("쿨롱효율")
        self.chkCE.setChecked(True)
        self.chkCE.setStyleSheet(f"color:{theme.CORAL};")
        for c in (self.chkChg, self.chkDchg, self.chkCE):
            c.toggled.connect(self._update_capacity_chart)
            header.addWidget(c)
        btnExpCap = QPushButton("데이터 다운로드")
        btnExpCap.clicked.connect(self.exportCapacityRequested.emit)
        header.addWidget(btnExpCap)
        lay.addLayout(header)

        axis_row = QHBoxLayout()
        self.axisCapY = AxisRangeControl("용량 축")
        self.axisCeY = AxisRangeControl("쿨롱효율 축", "0", "110")
        self.axisCapY.changed.connect(self._update_capacity_chart)
        self.axisCeY.changed.connect(self._update_capacity_chart)
        axis_row.addWidget(self.axisCapY)
        axis_row.addSpacing(20)
        axis_row.addWidget(self.axisCeY)
        axis_row.addStretch(1)

        self.capacityPlot = pg.PlotWidget()
        self.capacityPlot.setMinimumHeight(300)
        self.capacityPlot.showGrid(x=True, y=True, alpha=0.15)
        self.capacityView2 = add_secondary_axis(self.capacityPlot, "CE (%)")
        configure_plot_interaction(self.capacityPlot)
        self.capacityHover = HoverReadout(self.capacityPlot, self.capacityView2, "Capacity", "CE%")
        axis_row.addWidget(make_hover_checkbox([self.capacityHover]))
        lay.addLayout(axis_row)
        lay.addWidget(self.capacityPlot)

        # energy sub-chart (charge/discharge Wh) kept simple as its own small panel
        header2 = QHBoxLayout()
        header2.addWidget(section_title("사이클별 에너지"))
        header2.addStretch(1)
        self.chkChgWh = QCheckBox("충전에너지")
        self.chkChgWh.setStyleSheet(f"color:{theme.BLUE};")
        self.chkDchgWh = QCheckBox("방전에너지")
        self.chkDchgWh.setChecked(True)
        self.chkDchgWh.setStyleSheet(f"color:{theme.PURPLE};")
        for c in (self.chkChgWh, self.chkDchgWh):
            c.toggled.connect(self._update_energy_chart)
            header2.addWidget(c)
        btnExpEnergy = QPushButton("데이터 다운로드")
        btnExpEnergy.clicked.connect(self.exportEnergyRequested.emit)
        header2.addWidget(btnExpEnergy)
        lay.addLayout(header2)
        self.energyPlot = pg.PlotWidget()
        self.energyPlot.setMinimumHeight(220)
        self.energyPlot.showGrid(x=True, y=True, alpha=0.15)
        self.energyPlot.setLabel("left", "Energy (Wh)")
        self.energyPlot.setLabel("bottom", "Cycle")
        configure_plot_interaction(self.energyPlot)
        self.energyHover = HoverReadout(self.energyPlot, y_label="Energy(Wh)")
        header2.addWidget(make_hover_checkbox([self.energyHover]))
        lay.addWidget(self.energyPlot)

    def _build_temperature_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("사이클별 온도 추이"))
        header.addStretch(1)
        self.chkTempMinMax = QCheckBox("최소/최대 온도 표시")
        self.chkTempMinMax.toggled.connect(self._update_temp_chart)
        header.addWidget(self.chkTempMinMax)
        btnExpTemp = QPushButton("데이터 다운로드")
        btnExpTemp.clicked.connect(self.exportTempRequested.emit)
        header.addWidget(btnExpTemp)
        lay.addLayout(header)

        axis_row = QHBoxLayout()
        self.axisTempY = AxisRangeControl("온도 축")
        self.axisTempY.changed.connect(self._update_temp_chart)
        axis_row.addWidget(self.axisTempY)
        axis_row.addStretch(1)

        self.tempPlot = pg.PlotWidget()
        self.tempPlot.setMinimumHeight(280)
        self.tempPlot.addLegend(offset=(-10, 10))
        self.tempPlot.showGrid(x=True, y=True, alpha=0.15)
        self.tempPlot.setLabel("bottom", "Cycle")
        self.tempPlot.setLabel("left", "Temperature (℃)")
        configure_plot_interaction(self.tempPlot)
        self.tempHover = HoverReadout(self.tempPlot, y_label="Temp(℃)")
        axis_row.addWidget(make_hover_checkbox([self.tempHover]))
        lay.addLayout(axis_row)
        lay.addWidget(self.tempPlot)

    def _build_cccv_vtrend_panel(self):
        lay = self._panel()
        lay.addWidget(section_title("충전 CC/CV 구간 & 충/방전 시작전압·온셋저항(DCIR) 추이"))

        row = QHBoxLayout()
        col1 = QVBoxLayout()
        top1 = QHBoxLayout()
        top1.addWidget(QLabel("CC/CV 구간 분리"))
        top1.addStretch(1)
        btnExpCCCV = QPushButton("데이터 다운로드")
        btnExpCCCV.clicked.connect(self.exportCCCVRequested.emit)
        top1.addWidget(btnExpCCCV)
        self.cccvPlot = pg.PlotWidget()
        self.cccvPlot.setMinimumHeight(300)
        self.cccvPlot.addLegend(offset=(-10, 10))
        self.cccvPlot.showGrid(x=True, y=True, alpha=0.15)
        self.cccvPlot.setLabel("bottom", "Cycle")
        self.cccvPlot.setLabel("left", "충전 용량 (mAh)")
        self.cccvView2 = add_secondary_axis(self.cccvPlot, "CC 비율(%)")
        configure_plot_interaction(self.cccvPlot)
        self.cccvHover = HoverReadout(self.cccvPlot, self.cccvView2, "용량(mAh)", "CC비율%")
        top1.addWidget(make_hover_checkbox([self.cccvHover]))
        col1.addLayout(top1)
        col1.addWidget(self.cccvPlot)

        col2 = QVBoxLayout()
        top2 = QHBoxLayout()
        top2.addWidget(QLabel("시작전압 & 온셋저항"))
        top2.addStretch(1)
        self.chkR = QCheckBox("온셋저항(DCIR) 표시")
        self.chkR.toggled.connect(self._update_vtrend_chart)
        top2.addWidget(self.chkR)
        btnExpVTrend = QPushButton("데이터 다운로드")
        btnExpVTrend.clicked.connect(self.exportVTrendRequested.emit)
        top2.addWidget(btnExpVTrend)
        self.vtrendPlot = pg.PlotWidget()
        self.vtrendPlot.setMinimumHeight(300)
        self.vtrendPlot.addLegend(offset=(-10, 10))
        self.vtrendPlot.showGrid(x=True, y=True, alpha=0.15)
        self.vtrendPlot.setLabel("bottom", "Cycle")
        self.vtrendPlot.setLabel("left", "Voltage (V)")
        self.vtrendView2 = add_secondary_axis(self.vtrendPlot, "Onset R (mΩ)")
        configure_plot_interaction(self.vtrendPlot)
        self.vtrendHover = HoverReadout(self.vtrendPlot, self.vtrendView2, "Voltage(V)", "OnsetR(mΩ)")
        top2.addWidget(make_hover_checkbox([self.vtrendHover]))
        col2.addLayout(top2)
        col2.addWidget(self.vtrendPlot)

        row.addLayout(col1)
        row.addLayout(col2)
        lay.addLayout(row)

    def _build_detail_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("전압-용량 프로파일 & dQ/dV"))
        header.addStretch(1)
        self.btnPrev = QPushButton("◀ 이전")
        self.btnPrev.clicked.connect(lambda: self._step_cycle(-1))
        self.cycleLabel = QLabel("CYCLE -")
        self.cycleLabel.setStyleSheet(f"color:{theme.TEAL}; font-family:'{theme.FONT_MONO}'; font-size:13px; min-width:90px;")
        self.cycleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btnNext = QPushButton("다음 ▶")
        self.btnNext.clicked.connect(lambda: self._step_cycle(1))
        header.addWidget(self.btnPrev)
        header.addWidget(self.cycleLabel)
        header.addWidget(self.btnNext)
        lay.addLayout(header)

        self.cycleSlider = QSlider(Qt.Orientation.Horizontal)
        self.cycleSlider.valueChanged.connect(self._on_slider_changed)
        lay.addWidget(self.cycleSlider)

        axis_row = QHBoxLayout()
        self.axisVolt = AxisRangeControl("전압 축")
        self.axisCap2 = AxisRangeControl("용량 축")
        self.axisDqdv = AxisRangeControl("dQ/dV 축")
        for a in (self.axisVolt, self.axisCap2, self.axisDqdv):
            a.changed.connect(self._update_detail_charts)
            axis_row.addWidget(a)
        lay.addLayout(axis_row)

        peak_row = QHBoxLayout()
        peak_lbl = QLabel("dQ/dV 피크 민감도")
        peak_lbl.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11.5px;")
        self.peakSlider = QSlider(Qt.Orientation.Horizontal)
        self.peakSlider.setRange(1, 30)   # 1~30% of signal amplitude as prominence threshold
        self.peakSlider.setValue(5)
        self.peakSlider.setFixedWidth(160)
        self.peakSlider.valueChanged.connect(self._on_peak_sensitivity_changed)
        self.peakValueLabel = QLabel("5%")
        self.peakValueLabel.setStyleSheet(f"color:{theme.TEAL}; font-family:'{theme.FONT_MONO}'; font-size:11.5px; min-width:32px;")
        peak_hint = QLabel("(값이 작을수록 약한/많은 피크까지 검출됩니다)")
        peak_hint.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:10.5px;")
        peak_row.addWidget(peak_lbl)
        peak_row.addWidget(self.peakSlider)
        peak_row.addWidget(self.peakValueLabel)
        peak_row.addWidget(peak_hint)
        peak_row.addStretch(1)
        lay.addLayout(peak_row)

        self.ccCvCardsLayout = QGridLayout()
        self.ccCvCardsLayout.setSpacing(10)
        self.cardCC = StatCard("CC 용량", small=True, color=theme.AMBER)
        self.cardCV = StatCard("CV 용량", small=True, color=theme.TEAL)
        self.cardCCTime = StatCard("CC 시간", small=True)
        self.cardCVTime = StatCard("CV 시간", small=True)
        self.cardTransV = StatCard("전환 전압 (CC→CV)", small=True)
        for i, c in enumerate([self.cardCC, self.cardCV, self.cardCCTime, self.cardCVTime, self.cardTransV]):
            self.ccCvCardsLayout.addWidget(c, 0, i)
        lay.addLayout(self.ccCvCardsLayout)

        row = QHBoxLayout()
        col1 = QVBoxLayout()
        top1 = QHBoxLayout()
        top1.addWidget(QLabel("VOLTAGE vs CAPACITY"))
        top1.addStretch(1)
        btnExpVQ = QPushButton("데이터 다운로드")
        btnExpVQ.clicked.connect(self.exportVQRequested.emit)
        top1.addWidget(btnExpVQ)
        col1.addLayout(top1)
        self.vqPlot = pg.PlotWidget()
        self.vqPlot.setMinimumHeight(260)
        self.vqPlot.addLegend(offset=(-10, 10))
        self.vqPlot.showGrid(x=True, y=True, alpha=0.15)
        self.vqPlot.setLabel("bottom", "Capacity (mAh)")
        self.vqPlot.setLabel("left", "Voltage (V)")
        configure_plot_interaction(self.vqPlot)
        self.vqHover = HoverReadout(self.vqPlot, y_label="V")
        top1.addWidget(make_hover_checkbox([self.vqHover]))
        col1.addWidget(self.vqPlot)

        col2 = QVBoxLayout()
        top2 = QHBoxLayout()
        top2.addWidget(QLabel("dQ/dV (Voltage 500pt 보간)"))
        top2.addStretch(1)
        btnExpDQDV = QPushButton("데이터 다운로드")
        btnExpDQDV.clicked.connect(self.exportDQDVRequested.emit)
        top2.addWidget(btnExpDQDV)
        col2.addLayout(top2)
        self.dqdvPlot = pg.PlotWidget()
        self.dqdvPlot.setMinimumHeight(260)
        self.dqdvPlot.addLegend(offset=(-10, 10))
        self.dqdvPlot.showGrid(x=True, y=True, alpha=0.15)
        self.dqdvPlot.setLabel("bottom", "Voltage (V)")
        self.dqdvPlot.setLabel("left", "dQ/dV (mAh/V)")
        configure_plot_interaction(self.dqdvPlot)
        self.dqdvHover = HoverReadout(self.dqdvPlot, y_label="dQ/dV")
        top2.addWidget(make_hover_checkbox([self.dqdvHover]))
        col2.addWidget(self.dqdvPlot)

        row.addLayout(col1)
        row.addLayout(col2)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        col3 = QVBoxLayout()
        top3 = QHBoxLayout()
        top3.addWidget(QLabel("dV/dQ (Capacity 500pt 보간)"))
        top3.addStretch(1)
        btnExpDVDQ = QPushButton("데이터 다운로드")
        btnExpDVDQ.clicked.connect(self.exportDVDQRequested.emit)
        top3.addWidget(btnExpDVDQ)
        col3.addLayout(top3)
        axis_row3 = QHBoxLayout()
        self.axisDvdq = AxisRangeControl("dV/dQ 축")
        self.axisDvdq.changed.connect(self._update_detail_charts)
        axis_row3.addWidget(self.axisDvdq)
        axis_row3.addStretch(1)
        self.dvdqPlot = pg.PlotWidget()
        self.dvdqPlot.setMinimumHeight(260)
        self.dvdqPlot.addLegend(offset=(-10, 10))
        self.dvdqPlot.showGrid(x=True, y=True, alpha=0.15)
        self.dvdqPlot.setLabel("bottom", "Capacity (mAh)")
        self.dvdqPlot.setLabel("left", "dV/dQ (V/mAh)")
        configure_plot_interaction(self.dvdqPlot)
        self.dvdqHover = HoverReadout(self.dvdqPlot, y_label="dV/dQ")
        axis_row3.addWidget(make_hover_checkbox([self.dvdqHover]))
        col3.addLayout(axis_row3)
        col3.addWidget(self.dvdqPlot)

        col4 = QVBoxLayout()
        top4 = QHBoxLayout()
        top4.addWidget(QLabel("휴지 구간 전압 이완 dV/dt (mV/min)"))
        top4.addStretch(1)
        btnExpRest = QPushButton("데이터 다운로드")
        btnExpRest.clicked.connect(self.exportRestDvdtRequested.emit)
        top4.addWidget(btnExpRest)
        col4.addLayout(top4)
        axis_row4 = QHBoxLayout()
        self.axisRestTime = AxisRangeControl("시간(min) 축")
        self.axisRestDvdt = AxisRangeControl("dV/dt 축")
        for a in (self.axisRestTime, self.axisRestDvdt):
            a.changed.connect(self._update_detail_charts)
            axis_row4.addWidget(a)
        self.restPlot = pg.PlotWidget()
        self.restPlot.setMinimumHeight(260)
        self.restPlot.addLegend(offset=(-10, 10))
        self.restPlot.showGrid(x=True, y=True, alpha=0.15)
        self.restPlot.setLabel("bottom", "Time since rest start (min)")
        self.restPlot.setLabel("left", "dV/dt (mV/min)")
        configure_plot_interaction(self.restPlot)
        self.restHover = HoverReadout(self.restPlot, y_label="dV/dt")
        axis_row4.addWidget(make_hover_checkbox([self.restHover]))
        col4.addLayout(axis_row4)
        col4.addWidget(self.restPlot)

        row2.addLayout(col3)
        row2.addLayout(col4)
        lay.addLayout(row2)

        self.restCardsLayout = QGridLayout()
        self.restCardsLayout.setSpacing(10)
        self.cardChgRestTime = StatCard("충전 후 휴지시간", small=True)
        self.cardChgRestDV = StatCard("충전 후 ΔV (휴지중)", small=True)
        self.cardDisRestTime = StatCard("방전 후 휴지시간", small=True)
        self.cardDisRestDV = StatCard("방전 후 ΔV (휴지중)", small=True)
        for i, c in enumerate([self.cardChgRestTime, self.cardChgRestDV, self.cardDisRestTime, self.cardDisRestDV]):
            self.restCardsLayout.addWidget(c, 0, i)
        lay.addLayout(self.restCardsLayout)

    def _build_multi_dqdv_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("여러 사이클 dQ/dV 비교 (한 그래프에 겹쳐보기)"))
        header.addStretch(1)
        self.btnExpMultiDqdv = QPushButton("데이터 다운로드")
        self.btnExpMultiDqdv.clicked.connect(self.exportMultiDqdvRequested.emit)
        header.addWidget(self.btnExpMultiDqdv)
        lay.addLayout(header)

        desc = QLabel(
            "비교할 사이클을 지정하세요. 형식: '10-20' (10~20 전체), '5,10,15' (콤마로 나열),"
            " '10-50:5' (10~50을 5간격으로 = 10,15,...,50). 콤마로 여러 형식을 섞어 쓸 수도 있습니다."
        )
        desc.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        ctrl_row = QHBoxLayout()
        self.multiDirCombo = QComboBox()
        self.multiDirCombo.addItem("방전 dQ/dV", "discharge")
        self.multiDirCombo.addItem("충전 dQ/dV", "charge")
        self.multiCycleSpecInp = QLineEdit()
        self.multiCycleSpecInp.setPlaceholderText("예: 10-50:5")
        self.multiCycleSpecInp.returnPressed.connect(self._emit_multi_apply)
        btnApply = QPushButton("적용")
        btnApply.setProperty("variant", "primary")
        btnApply.clicked.connect(self._emit_multi_apply)
        ctrl_row.addWidget(self.multiDirCombo)
        ctrl_row.addWidget(self.multiCycleSpecInp, 1)
        ctrl_row.addWidget(btnApply)
        lay.addLayout(ctrl_row)

        self.multiAppliedLabel = QLabel("아직 적용된 사이클이 없습니다.")
        self.multiAppliedLabel.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        self.multiAppliedLabel.setWordWrap(True)
        lay.addWidget(self.multiAppliedLabel)

        axis_row = QHBoxLayout()
        self.axisMultiVolt = AxisRangeControl("전압 축")
        self.axisMultiDqdv = AxisRangeControl("dQ/dV 축")
        for a in (self.axisMultiVolt, self.axisMultiDqdv):
            a.changed.connect(self._update_multi_dqdv_chart)
            axis_row.addWidget(a)
        self.multiDqdvPlot = pg.PlotWidget()
        self.multiDqdvPlot.setMinimumHeight(340)
        self.multiDqdvPlot.addLegend(offset=(-10, 10))
        self.multiDqdvPlot.showGrid(x=True, y=True, alpha=0.15)
        self.multiDqdvPlot.setLabel("bottom", "Voltage (V)")
        self.multiDqdvPlot.setLabel("left", "dQ/dV (mAh/V)")
        configure_plot_interaction(self.multiDqdvPlot)
        self.multiDqdvHover = HoverReadout(self.multiDqdvPlot, y_label="dQ/dV")
        axis_row.addWidget(make_hover_checkbox([self.multiDqdvHover]))
        lay.addLayout(axis_row)
        lay.addWidget(self.multiDqdvPlot)

    def _emit_multi_apply(self):
        direction = self.multiDirCombo.currentData()
        self.multiDqdvApplyRequested.emit(self.multiCycleSpecInp.text(), direction)

    def _build_multi_rest_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("여러 사이클 휴지 dV/dt 비교 (한 그래프에 겹쳐보기)"))
        header.addStretch(1)
        self.btnExpMultiRest = QPushButton("데이터 다운로드")
        self.btnExpMultiRest.clicked.connect(self.exportMultiRestRequested.emit)
        header.addWidget(self.btnExpMultiRest)
        lay.addLayout(header)

        desc = QLabel(
            "충전 후 또는 방전 후 휴지 구간의 전압 이완 dV/dt를 여러 사이클에 걸쳐 겹쳐봅니다. "
            "사이클 지정 형식은 위 dQ/dV 비교와 동일합니다 (예: '10-50:5')."
        )
        desc.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        ctrl_row = QHBoxLayout()
        self.multiRestAfterCombo = QComboBox()
        self.multiRestAfterCombo.addItem("방전 후 휴지", "discharge")
        self.multiRestAfterCombo.addItem("충전 후 휴지", "charge")
        self.multiRestCycleSpecInp = QLineEdit()
        self.multiRestCycleSpecInp.setPlaceholderText("예: 10-50:5")
        self.multiRestCycleSpecInp.returnPressed.connect(self._emit_multi_rest_apply)
        btnApplyRest = QPushButton("적용")
        btnApplyRest.setProperty("variant", "primary")
        btnApplyRest.clicked.connect(self._emit_multi_rest_apply)
        ctrl_row.addWidget(self.multiRestAfterCombo)
        ctrl_row.addWidget(self.multiRestCycleSpecInp, 1)
        ctrl_row.addWidget(btnApplyRest)
        lay.addLayout(ctrl_row)

        self.multiRestAppliedLabel = QLabel("아직 적용된 사이클이 없습니다.")
        self.multiRestAppliedLabel.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        self.multiRestAppliedLabel.setWordWrap(True)
        lay.addWidget(self.multiRestAppliedLabel)

        axis_row = QHBoxLayout()
        self.axisMultiRestTime = AxisRangeControl("시간(min) 축")
        self.axisMultiRestDvdt = AxisRangeControl("dV/dt 축")
        for a in (self.axisMultiRestTime, self.axisMultiRestDvdt):
            a.changed.connect(self._update_multi_rest_chart)
            axis_row.addWidget(a)
        self.multiRestPlot = pg.PlotWidget()
        self.multiRestPlot.setMinimumHeight(340)
        self.multiRestPlot.addLegend(offset=(-10, 10))
        self.multiRestPlot.showGrid(x=True, y=True, alpha=0.15)
        self.multiRestPlot.setLabel("bottom", "Time since rest start (min)")
        self.multiRestPlot.setLabel("left", "dV/dt (mV/min)")
        configure_plot_interaction(self.multiRestPlot)
        self.multiRestHover = HoverReadout(self.multiRestPlot, y_label="dV/dt")
        axis_row.addWidget(make_hover_checkbox([self.multiRestHover]))
        lay.addLayout(axis_row)
        lay.addWidget(self.multiRestPlot)

    def _emit_multi_rest_apply(self):
        after = self.multiRestAfterCombo.currentData()
        self.multiRestApplyRequested.emit(self.multiRestCycleSpecInp.text(), after)

    def _build_peaks_trend_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("사이클별 dQ/dV 피크 전압 추이 (열화 시 피크 이동 확인용)"))
        header.addStretch(1)
        btnExportAll = QPushButton("전체 사이클 dQ/dV + 피크 데이터 일괄 내보내기 (Excel)")
        btnExportAll.clicked.connect(self.exportAllRequested.emit)
        header.addWidget(btnExportAll)
        lay.addLayout(header)

        desc = QLabel("사이클마다 검출된 모든 피크의 전압 위치를 점으로 표시합니다. 위 'dQ/dV 피크 민감도' 슬라이더 값이 그대로 적용됩니다.")
        desc.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        self.peaksTrendPlot = pg.PlotWidget()
        self.peaksTrendPlot.setMinimumHeight(280)
        self.peaksTrendPlot.addLegend(offset=(-10, 10))
        self.peaksTrendPlot.showGrid(x=True, y=True, alpha=0.15)
        self.peaksTrendPlot.setLabel("bottom", "Cycle")
        self.peaksTrendPlot.setLabel("left", "Peak Voltage (V)")
        configure_plot_interaction(self.peaksTrendPlot)
        self.peaksTrendHover = HoverReadout(self.peaksTrendPlot, y_label="Peak V")
        hover_row = QHBoxLayout()
        hover_row.addWidget(make_hover_checkbox([self.peaksTrendHover]))
        hover_row.addStretch(1)
        btnExpPeaksTrend = QPushButton("이 차트 데이터 다운로드")
        btnExpPeaksTrend.clicked.connect(self.exportPeaksTrendRequested.emit)
        hover_row.addWidget(btnExpPeaksTrend)
        lay.addLayout(hover_row)
        lay.addWidget(self.peaksTrendPlot)

    def _build_table_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("사이클별 요약 테이블"))
        header.addStretch(1)
        btnCsv = QPushButton("테이블 CSV 다운로드")
        btnCsv.clicked.connect(self.exportTableRequested.emit)
        btnXlsx = QPushButton("엑셀로 다운로드")
        btnXlsx.clicked.connect(self.exportExcelRequested.emit)
        header.addWidget(btnCsv)
        header.addWidget(btnXlsx)
        lay.addLayout(header)

        self.table = QTableWidget()
        self.table.setMinimumHeight(320)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        lay.addWidget(self.table)

    TABLE_COLUMNS = [
        ("Cycle", "Cycle", 0),
        ("충전용량", "Charge_Capacity_mAh", 3),
        ("방전용량", "Discharge_Capacity_mAh", 3),
        ("CE(%)", "Coulombic_Efficiency_pct", 2),
        ("충전E(Wh)", "_ChargeWh", 3),
        ("방전E(Wh)", "_DischargeWh", 3),
        ("CC용량", "CC_Capacity_mAh", 3),
        ("CV용량", "CV_Capacity_mAh", 3),
        ("CC시간", "_CCTimeStr", -1),
        ("CV시간", "_CVTimeStr", -1),
        ("전환전압", "Transition_Voltage_V", 4),
        ("충전시작V", "Charge_Start_Voltage_V", 4),
        ("방전시작V", "Discharge_Start_Voltage_V", 4),
        ("충전OnsetR(mΩ)", "Charge_Onset_R_mOhm", 2),
        ("방전OnsetR(mΩ)", "Discharge_Onset_R_mOhm", 2),
        ("평균온도(℃)", "Avg_Temp_C", 2),
        ("최소온도(℃)", "Min_Temp_C", 2),
        ("최대온도(℃)", "Max_Temp_C", 2),
        ("온도상승폭(℃)", "Temp_Rise_C", 2),
        ("용량유지율(%)", "Discharge_Capacity_Retention_pct", 2),
    ]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def set_analysis(self, summary_rows, min_cycle, max_cycle, voltage_domain, capacity_domain, source_files=None):
        self.summary_all = summary_rows
        self.source_files = list(source_files or dict.fromkeys(r.get("Source_File", "입력 파일") for r in summary_rows))
        self.active_sources = set(self.source_files)
        self._refresh_source_sidebar()
        self.min_cycle = min_cycle
        self.max_cycle = max_cycle
        self.voltage_domain = voltage_domain
        self.capacity_domain = capacity_domain
        self.cycle_from = None
        self.cycle_to = None
        self.selected_cycle = min_cycle

        self.fromSpin.blockSignals(True)
        self.toSpin.blockSignals(True)
        self.fromSpin.setRange(min_cycle, max_cycle)
        self.toSpin.setRange(min_cycle, max_cycle)
        self.fromSpin.setValue(min_cycle)
        self.toSpin.setValue(max_cycle)
        self.fromSpin.blockSignals(False)
        self.toSpin.blockSignals(False)

        self.cycleSlider.blockSignals(True)
        self.cycleSlider.setRange(min_cycle, max_cycle)
        self.cycleSlider.setValue(min_cycle)
        self.cycleSlider.blockSignals(False)
        self.cycleLabel.setText(f"CYCLE {min_cycle}")

        self._update_stat_cards()
        self._update_capacity_chart()
        self._update_energy_chart()
        self._update_temp_chart()
        self._update_cccv_chart()
        self._update_vtrend_chart()
        self._render_table()

    def set_cycle_detail(self, detail: dict):
        self.current_detail = detail
        self._update_detail_charts()
        self._update_cc_cv_cards()
        self._update_rest_cards()

    def set_multi_dqdv(self, curves: list, direction: str, requested_spec: str = ""):
        """curves: [{'cycle':int, 'voltage_grid':ndarray, 'dqdv':ndarray}, ...] (요청한 사이클 순서 그대로)"""
        self.multi_curves = curves or []
        self.multi_direction = direction
        found = [c["cycle"] for c in self.multi_curves]
        if not found:
            self.multiAppliedLabel.setText(
                f"'{requested_spec}' 에 해당하는 사이클에서 dQ/dV를 계산하지 못했습니다. "
                "사이클 번호 범위를 확인해주세요."
            )
        else:
            dir_label = "충전" if direction == "charge" else "방전"
            self.multiAppliedLabel.setText(
                f"적용된 사이클 ({dir_label}, {len(found)}개): {', '.join(str(c) for c in found)}"
            )
        self._update_multi_dqdv_chart()

    @staticmethod
    def _gradient_color(i: int, n: int) -> str:
        """사이클 순서에 따라 파란색 -> 붉은색으로 이어지는 그라디언트 색상 (진행 상황을 한눈에)."""
        t = 0.0 if n <= 1 else i / (n - 1)
        c0 = (69, 216, 192)   # teal (theme.TEAL)
        c1 = (242, 102, 90)   # coral (theme.CORAL)
        r = int(c0[0] + (c1[0] - c0[0]) * t)
        g = int(c0[1] + (c1[1] - c0[1]) * t)
        b = int(c0[2] + (c1[2] - c0[2]) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _update_multi_dqdv_chart(self):
        self.multiDqdvPlot.clear()
        self.multiDqdvHover.reattach()
        curves = self.multi_curves
        if not curves:
            return
        n = len(curves)
        for i, c in enumerate(curves):
            color = self._gradient_color(i, n)
            self.multiDqdvPlot.plot(c["voltage_grid"], c["dqdv"], pen=pg.mkPen(color, width=2),
                                     name=f"Cycle {c['cycle']}")

        volt_rng = self.axisMultiVolt.value_range()
        if volt_rng:
            self.multiDqdvPlot.setXRange(volt_rng[0], volt_rng[1], padding=0)
        else:
            self.multiDqdvPlot.enableAutoRange(axis=pg.ViewBox.XAxis)
        dqdv_rng = self.axisMultiDqdv.value_range()
        if dqdv_rng:
            self.multiDqdvPlot.setYRange(dqdv_rng[0], dqdv_rng[1], padding=0)
        else:
            self.multiDqdvPlot.enableAutoRange(axis=pg.ViewBox.YAxis)

    def set_multi_rest(self, curves: list, after: str, requested_spec: str = ""):
        """curves: [{'cycle':int, 'time_min':ndarray, 'dvdt_mV_per_min':ndarray, ...}, ...]"""
        self.multi_rest_curves = curves or []
        self.multi_rest_after = after
        found = [c["cycle"] for c in self.multi_rest_curves]
        if not found:
            self.multiRestAppliedLabel.setText(
                f"'{requested_spec}' 에 해당하는 사이클에서 휴지 dV/dt를 계산하지 못했습니다. "
                "그 방향의 휴지 구간이 없거나 사이클 번호를 확인해주세요."
            )
        else:
            after_label = "충전 후" if after == "charge" else "방전 후"
            self.multiRestAppliedLabel.setText(
                f"적용된 사이클 ({after_label} 휴지, {len(found)}개): {', '.join(str(c) for c in found)}"
            )
        self._update_multi_rest_chart()

    def _update_multi_rest_chart(self):
        self.multiRestPlot.clear()
        self.multiRestHover.reattach()
        curves = self.multi_rest_curves
        if not curves:
            return
        n = len(curves)
        for i, c in enumerate(curves):
            color = self._gradient_color(i, n)
            self.multiRestPlot.plot(c["time_min"], c["dvdt_mV_per_min"], pen=pg.mkPen(color, width=2),
                                     name=f"Cycle {c['cycle']}")

        time_rng = self.axisMultiRestTime.value_range()
        if time_rng:
            self.multiRestPlot.setXRange(time_rng[0], time_rng[1], padding=0)
        else:
            self.multiRestPlot.enableAutoRange(axis=pg.ViewBox.XAxis)
        dvdt_rng = self.axisMultiRestDvdt.value_range()
        if dvdt_rng:
            self.multiRestPlot.setYRange(dvdt_rng[0], dvdt_rng[1], padding=0)
        else:
            self.multiRestPlot.enableAutoRange(axis=pg.ViewBox.YAxis)

    def set_peaks_trend(self, peaks: list):
        """peaks: [{'cycle':int, 'phase':'charge'/'discharge', 'peak_voltage':float, 'peak_dqdv':float}, ...]"""
        self.last_peaks = peaks
        self.peaksTrendPlot.clear()
        self.peaksTrendHover.reattach()
        if not peaks:
            return
        chg = [p for p in peaks if p["phase"] == "charge"]
        dis = [p for p in peaks if p["phase"] == "discharge"]
        if chg:
            self.peaksTrendPlot.plot([p["cycle"] for p in chg], [p["peak_voltage"] for p in chg],
                                      pen=None, symbol="o", symbolSize=6, symbolBrush=theme.AMBER,
                                      symbolPen=None, name="충전 피크")
        if dis:
            self.peaksTrendPlot.plot([p["cycle"] for p in dis], [p["peak_voltage"] for p in dis],
                                      pen=None, symbol="o", symbolSize=6, symbolBrush=theme.TEAL,
                                      symbolPen=None, name="방전 피크")

    def _on_peak_sensitivity_changed(self, value: int):
        self.peakValueLabel.setText(f"{value}%")
        self.peakSensitivityChanged.emit(value / 100.0)

    def peak_sensitivity_pct(self) -> float:
        return self.peakSlider.value() / 100.0

    # ------------------------------------------------------------------
    def _filtered(self):
        lo = self.cycle_from if self.cycle_from is not None else self.min_cycle
        hi = self.cycle_to if self.cycle_to is not None else self.max_cycle
        return [
            r for r in self.summary_all
            if lo <= r["Cycle"] <= hi and r.get("Source_File", "입력 파일") in self.active_sources
        ]

    def _on_range_changed(self):
        self.cycle_from = self.fromSpin.value()
        self.cycle_to = self.toSpin.value()
        self._update_capacity_chart()
        self._update_energy_chart()
        self._update_temp_chart()
        self._update_cccv_chart()
        self._update_vtrend_chart()
        self._render_table()

    def _reset_range(self):
        self.fromSpin.blockSignals(True)
        self.toSpin.blockSignals(True)
        self.fromSpin.setValue(self.min_cycle)
        self.toSpin.setValue(self.max_cycle)
        self.fromSpin.blockSignals(False)
        self.toSpin.blockSignals(False)
        self.cycle_from = None
        self.cycle_to = None
        self._update_capacity_chart()
        self._update_energy_chart()
        self._update_temp_chart()
        self._update_cccv_chart()
        self._update_vtrend_chart()
        self._render_table()

    def _step_cycle(self, delta):
        new_val = max(self.min_cycle, min(self.max_cycle, self.selected_cycle + delta))
        self.cycleSlider.setValue(new_val)  # triggers _on_slider_changed -> cycleChanged

    def _on_slider_changed(self, value):
        self.selected_cycle = value
        self.cycleLabel.setText(f"CYCLE {value}")
        self.cycleChanged.emit(value)

    # ------------------------------------------------------------------
    def _update_stat_cards(self):
        rows = self._filtered()
        if not rows:
            return
        first = rows[0]
        # 마지막 사이클이 도중에 끊겨(방전 미기록) NaN일 수 있으므로, 유효한 방전용량을 가진
        # 마지막 행을 "최종" 통계 기준으로 사용한다.
        valid_rows = [r for r in rows if _is_valid_num(r.get("Discharge_Capacity_mAh"))]
        last = valid_rows[-1] if valid_rows else rows[-1]

        retention = last.get("Discharge_Capacity_Retention_pct")
        ce_vals = [r["Coulombic_Efficiency_pct"] for r in rows if _is_valid_num(r.get("Coulombic_Efficiency_pct"))]
        avg_ce = (sum(ce_vals) / len(ce_vals)) if ce_vals else None
        final_wh = last.get("Discharge_Energy_mWh")
        final_wh = (final_wh / 1000.0) if _is_valid_num(final_wh) else None

        self.cardTotalCycles.set_value(str(len(valid_rows) if valid_rows else len(rows)))
        self.cardInitCap.set_value(fmt(first.get("Discharge_Capacity_mAh"), 2))
        self.cardFinalCap.set_value(fmt(last.get("Discharge_Capacity_mAh"), 2))
        color = theme.TEAL if _is_valid_num(retention) and retention >= 80 else theme.CORAL
        self.cardRetention.set_value(fmt(retention, 1) + "%", color=color)
        self.cardAvgCE.set_value(fmt(avg_ce, 2) + "%")
        self.cardFinalWh.set_value(fmt(final_wh, 3))

    def _axis_apply(self, plot: pg.PlotWidget, control: AxisRangeControl, axis="y", view=None):
        rng = control.value_range()
        target = view if view is not None else plot.getPlotItem().vb
        if rng is None:
            target.enableAutoRange(axis=pg.ViewBox.YAxis if axis == "y" else pg.ViewBox.XAxis)
        else:
            if axis == "y":
                target.setYRange(rng[0], rng[1], padding=0)
            else:
                target.setXRange(rng[0], rng[1], padding=0)

    def _update_capacity_chart(self):
        rows = self._filtered()
        self.capacityPlot.clear()
        self.capacityView2.clear()
        self.capacityHover.reattach()
        if not rows:
            return
        cyc = np.array([r["Cycle"] for r in rows], dtype=float)

        if self.chkChg.isChecked():
            y = np.array([r.get("Charge_Capacity_mAh") or 0 for r in rows], dtype=float)
            self.capacityPlot.plot(cyc, y, pen=pg.mkPen(theme.AMBER, width=2), symbol="o", symbolSize=5,
                                    symbolBrush=theme.AMBER, symbolPen=None, name="충전용량")
        if self.chkDchg.isChecked():
            y = np.array([r.get("Discharge_Capacity_mAh") or 0 for r in rows], dtype=float)
            self.capacityPlot.plot(cyc, y, pen=pg.mkPen(theme.TEAL, width=2), symbol="o", symbolSize=5,
                                    symbolBrush=theme.TEAL, symbolPen=None, name="방전용량")
        if self.chkCE.isChecked():
            y = np.array([r.get("Coulombic_Efficiency_pct") if r.get("Coulombic_Efficiency_pct") is not None else np.nan for r in rows])
            curve = pg.PlotDataItem(cyc, y, pen=pg.mkPen(theme.CORAL, width=2), symbol="o", symbolSize=5,
                                     symbolBrush=theme.CORAL, name="쿨롱효율(%)")
            self.capacityView2.addItem(curve)

        self.capacityPlot.setLabel("bottom", "Cycle")
        self.capacityPlot.setLabel("left", "Capacity (mAh)")
        self._axis_apply(self.capacityPlot, self.axisCapY, axis="y")
        self._axis_apply(self.capacityPlot, self.axisCeY, axis="y", view=self.capacityView2)

    def _update_energy_chart(self):
        rows = self._filtered()
        self.energyPlot.clear()
        self.energyHover.reattach()
        if not rows:
            return
        cyc = np.array([r["Cycle"] for r in rows], dtype=float)
        if self.chkChgWh.isChecked():
            y = np.array([(r.get("Charge_Energy_mWh") or 0) / 1000.0 for r in rows])
            self.energyPlot.plot(cyc, y, pen=pg.mkPen(theme.BLUE, width=2), symbol="o", symbolSize=4,
                                  symbolBrush=theme.BLUE, name="충전에너지(Wh)")
        if self.chkDchgWh.isChecked():
            y = np.array([(r.get("Discharge_Energy_mWh") or 0) / 1000.0 for r in rows])
            self.energyPlot.plot(cyc, y, pen=pg.mkPen(theme.PURPLE, width=2), symbol="o", symbolSize=4,
                                  symbolBrush=theme.PURPLE, name="방전에너지(Wh)")

    def _update_temp_chart(self):
        rows = self._filtered()
        self.tempPlot.clear()
        self.tempHover.reattach()
        if not rows:
            return
        cyc = np.array([r["Cycle"] for r in rows], dtype=float)
        avg_t = np.array([r.get("Avg_Temp_C") if r.get("Avg_Temp_C") is not None else np.nan for r in rows])
        self.tempPlot.plot(cyc, avg_t, pen=pg.mkPen(theme.AMBER, width=2), symbol="o", symbolSize=5,
                            symbolBrush=theme.AMBER, symbolPen=None, name="평균 온도")
        if self.chkTempMinMax.isChecked():
            min_t = np.array([r.get("Min_Temp_C") if r.get("Min_Temp_C") is not None else np.nan for r in rows])
            max_t = np.array([r.get("Max_Temp_C") if r.get("Max_Temp_C") is not None else np.nan for r in rows])
            self.tempPlot.plot(cyc, min_t, pen=pg.mkPen(theme.STEEL, width=1.5, style=Qt.PenStyle.DashLine),
                                name="최소 온도")
            self.tempPlot.plot(cyc, max_t, pen=pg.mkPen(theme.CORAL, width=1.5, style=Qt.PenStyle.DashLine),
                                name="최대 온도")

        rng = self.axisTempY.value_range()
        if rng:
            self.tempPlot.setYRange(rng[0], rng[1], padding=0)
        else:
            self.tempPlot.enableAutoRange(axis=pg.ViewBox.YAxis)

    def _update_cccv_chart(self):
        rows = self._filtered()
        self.cccvPlot.clear()
        self.cccvView2.clear()
        self.cccvHover.reattach()
        if not rows:
            return
        cyc = np.array([r["Cycle"] for r in rows], dtype=float)
        cc = np.array([r.get("CC_Capacity_mAh") or 0 for r in rows], dtype=float)
        cv = np.array([r.get("CV_Capacity_mAh") or 0 for r in rows], dtype=float)

        self.cccvPlot.plot(cyc, cc, pen=pg.mkPen(theme.GOLD, width=2), symbol="o", symbolSize=5,
                            symbolBrush=theme.GOLD, symbolPen=None, name="CC 용량")
        self.cccvPlot.plot(cyc, cv, pen=pg.mkPen(theme.PURPLE_DARK, width=2), symbol="o", symbolSize=5,
                            symbolBrush=theme.PURPLE_DARK, symbolPen=None, name="CV 용량")

        ratio = np.array([r.get("CC_Capacity_Ratio_pct") if r.get("CC_Capacity_Ratio_pct") is not None else np.nan for r in rows])
        curve = pg.PlotDataItem(cyc, ratio, pen=pg.mkPen(theme.GREEN, width=2), symbol="o", symbolSize=5,
                                 symbolBrush=theme.GREEN, name="CC 비율(%)")
        self.cccvView2.addItem(curve)
        self.cccvView2.setYRange(0, 105, padding=0)

    def _update_vtrend_chart(self):
        rows = self._filtered()
        self.vtrendPlot.clear()
        self.vtrendView2.clear()
        self.vtrendHover.reattach()
        if not rows:
            return
        cyc = np.array([r["Cycle"] for r in rows], dtype=float)
        chg_v = np.array([r.get("Charge_Start_Voltage_V") if r.get("Charge_Start_Voltage_V") is not None else np.nan for r in rows])
        dchg_v = np.array([r.get("Discharge_Start_Voltage_V") if r.get("Discharge_Start_Voltage_V") is not None else np.nan for r in rows])
        self.vtrendPlot.plot(cyc, chg_v, pen=pg.mkPen("#e74c3c", width=2), symbol="o", symbolSize=5,
                              symbolBrush="#e74c3c", name="충전 시작전압")
        self.vtrendPlot.plot(cyc, dchg_v, pen=pg.mkPen("#3498db", width=2), symbol="o", symbolSize=5,
                              symbolBrush="#3498db", name="방전 시작전압")

        if self.chkR.isChecked():
            r_chg = np.array([r.get("Charge_Onset_R_mOhm") if r.get("Charge_Onset_R_mOhm") is not None else np.nan for r in rows])
            r_dchg = np.array([r.get("Discharge_Onset_R_mOhm") if r.get("Discharge_Onset_R_mOhm") is not None else np.nan for r in rows])
            self.vtrendView2.addItem(pg.PlotDataItem(cyc, r_chg, pen=pg.mkPen(theme.ORANGE, width=2),
                                                       symbol="o", symbolSize=5, symbolBrush=theme.ORANGE, name="충전 온셋R(mΩ)"))
            self.vtrendView2.addItem(pg.PlotDataItem(cyc, r_dchg, pen=pg.mkPen(theme.STEEL, width=2),
                                                       symbol="o", symbolSize=5, symbolBrush=theme.STEEL, name="방전 온셋R(mΩ)"))

    def _update_detail_charts(self):
        d = self.current_detail
        self.vqPlot.clear()
        self.dqdvPlot.clear()
        self.dvdqPlot.clear()
        self.restPlot.clear()
        self.vqHover.reattach()
        self.dqdvHover.reattach()
        self.dvdqHover.reattach()
        self.restHover.reattach()
        if not d:
            return

        chg_pts = d.get("chargePts") or []
        dis_pts = d.get("dischargePts") or []
        if chg_pts:
            self.vqPlot.plot([p["q"] for p in chg_pts], [p["v"] for p in chg_pts],
                              pen=pg.mkPen(theme.AMBER, width=2), name="충전")
        if dis_pts:
            self.vqPlot.plot([p["q"] for p in dis_pts], [p["v"] for p in dis_pts],
                              pen=pg.mkPen(theme.TEAL, width=2), name="방전")

        volt_rng = self.axisVolt.value_range() or self.voltage_domain
        cap_rng = self.axisCap2.value_range() or self.capacity_domain
        self.vqPlot.setXRange(cap_rng[0], cap_rng[1], padding=0)
        self.vqPlot.setYRange(volt_rng[0], volt_rng[1], padding=0)

        rc = d.get("dqdvCharge")
        rd = d.get("dqdvDischarge")
        if rc:
            self.dqdvPlot.plot(rc["voltageGrid"], rc["dqdv"], pen=pg.mkPen(theme.AMBER, width=2), name="충전 dQ/dV")
            if rc.get("peakVoltage"):
                self.dqdvPlot.addItem(pg.ScatterPlotItem(rc["peakVoltage"], rc["peakDqdv"], symbol="x", size=12,
                                                          pen=pg.mkPen("#c0392b", width=2), brush=None, name="충전 피크"))
        if rd:
            self.dqdvPlot.plot(rd["voltageGrid"], rd["dqdv"], pen=pg.mkPen(theme.TEAL, width=2), name="방전 dQ/dV")
            if rd.get("peakVoltage"):
                self.dqdvPlot.addItem(pg.ScatterPlotItem(rd["peakVoltage"], rd["peakDqdv"], symbol="x", size=12,
                                                          pen=pg.mkPen(theme.STEEL, width=2), brush=None, name="방전 피크"))

        self.dqdvPlot.setXRange(volt_rng[0], volt_rng[1], padding=0)
        dqdv_rng = self.axisDqdv.value_range()
        if dqdv_rng:
            self.dqdvPlot.setYRange(dqdv_rng[0], dqdv_rng[1], padding=0)
        else:
            self.dqdvPlot.enableAutoRange(axis=pg.ViewBox.YAxis)

        # ---- dV/dQ ----
        if rc:
            self.dvdqPlot.plot(rc["capacityGrid"], rc["dvdq"], pen=pg.mkPen(theme.AMBER, width=2), name="충전 dV/dQ")
            if rc.get("peakCapacity"):
                self.dvdqPlot.addItem(pg.ScatterPlotItem(rc["peakCapacity"], rc["peakDvdq"], symbol="x", size=12,
                                                          pen=pg.mkPen("#c0392b", width=2), brush=None, name="충전 피크"))
        if rd:
            self.dvdqPlot.plot(rd["capacityGrid"], rd["dvdq"], pen=pg.mkPen(theme.TEAL, width=2), name="방전 dV/dQ")
            if rd.get("peakCapacity"):
                self.dvdqPlot.addItem(pg.ScatterPlotItem(rd["peakCapacity"], rd["peakDvdq"], symbol="x", size=12,
                                                          pen=pg.mkPen(theme.STEEL, width=2), brush=None, name="방전 피크"))
        self.dvdqPlot.setXRange(cap_rng[0], cap_rng[1], padding=0)
        dvdq_rng = self.axisDvdq.value_range()
        if dvdq_rng:
            self.dvdqPlot.setYRange(dvdq_rng[0], dvdq_rng[1], padding=0)
        else:
            self.dvdqPlot.enableAutoRange(axis=pg.ViewBox.YAxis)

        # ---- Rest dV/dt ----
        chg_rest = d.get("chargeRest")
        dis_rest = d.get("dischargeRest")
        if chg_rest:
            self.restPlot.plot(chg_rest["timeMin"], chg_rest["dvdtMvPerMin"],
                                pen=pg.mkPen("#e74c3c", width=2), symbol="o", symbolSize=5,
                                symbolBrush="#e74c3c", symbolPen=None, name="충전 후 휴지")
        if dis_rest:
            self.restPlot.plot(dis_rest["timeMin"], dis_rest["dvdtMvPerMin"],
                                pen=pg.mkPen("#3498db", width=2), symbol="o", symbolSize=5,
                                symbolBrush="#3498db", symbolPen=None, name="방전 후 휴지")

        rest_time_rng = self.axisRestTime.value_range()
        if rest_time_rng:
            self.restPlot.setXRange(rest_time_rng[0], rest_time_rng[1], padding=0)
        else:
            self.restPlot.enableAutoRange(axis=pg.ViewBox.XAxis)
        rest_dvdt_rng = self.axisRestDvdt.value_range()
        if rest_dvdt_rng:
            self.restPlot.setYRange(rest_dvdt_rng[0], rest_dvdt_rng[1], padding=0)
        else:
            self.restPlot.enableAutoRange(axis=pg.ViewBox.YAxis)

    def _update_cc_cv_cards(self):
        d = self.current_detail
        row = (d or {}).get("summaryRow")
        if not row:
            self.cardCC.set_value("-")
            self.cardCV.set_value("-")
            self.cardCCTime.set_value("-")
            self.cardCVTime.set_value("-")
            self.cardTransV.set_value("-")
            return
        self.cardCC.set_value(fmt(row.get("CC_Capacity_mAh"), 3))
        self.cardCV.set_value(fmt(row.get("CV_Capacity_mAh"), 3))
        self.cardCCTime.set_value(fmt_duration(row.get("CC_Time_s")))
        self.cardCVTime.set_value(fmt_duration(row.get("CV_Time_s")))
        self.cardTransV.set_value(fmt(row.get("Transition_Voltage_V"), 4) + " V")

    def _update_rest_cards(self):
        d = self.current_detail or {}
        chg_rest = d.get("chargeRest")
        dis_rest = d.get("dischargeRest")
        if chg_rest:
            self.cardChgRestTime.set_value(fmt_duration(chg_rest.get("restDurationS")))
            self.cardChgRestDV.set_value(fmt(chg_rest.get("deltaVV"), 5) + " V")
        else:
            self.cardChgRestTime.set_value("-")
            self.cardChgRestDV.set_value("-")
        if dis_rest:
            self.cardDisRestTime.set_value(fmt_duration(dis_rest.get("restDurationS")))
            self.cardDisRestDV.set_value(fmt(dis_rest.get("deltaVV"), 5) + " V")
        else:
            self.cardDisRestTime.set_value("-")
            self.cardDisRestDV.set_value("-")

    def _render_table(self):
        rows = self._filtered()
        self.table.setSortingEnabled(False)
        self.table.setColumnCount(len(self.TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels([c[0] for c in self.TABLE_COLUMNS])
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            values = dict(r)
            values["_ChargeWh"] = (r.get("Charge_Energy_mWh") or 0) / 1000.0 if r.get("Charge_Energy_mWh") is not None else None
            values["_DischargeWh"] = (r.get("Discharge_Energy_mWh") or 0) / 1000.0 if r.get("Discharge_Energy_mWh") is not None else None
            values["_CCTimeStr"] = fmt_duration(r.get("CC_Time_s"))
            values["_CVTimeStr"] = fmt_duration(r.get("CV_Time_s"))
            for j, (_, key, digits) in enumerate(self.TABLE_COLUMNS):
                v = values.get(key)
                if digits == -1:
                    text = v if v is not None else "-"
                elif digits == 0:
                    text = str(int(v)) if v is not None else "-"
                else:
                    text = fmt(v, digits)
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, j, item)
        self.table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    def table_export_rows(self):
        """CSV/Excel 내보내기용 원본 dict 리스트 (표시용 반올림 없이)."""
        rows = self._filtered()
        cols = ["Cycle", "Charge_Capacity_mAh", "Discharge_Capacity_mAh", "Coulombic_Efficiency_pct",
                "Charge_Energy_mWh", "Discharge_Energy_mWh", "CC_Capacity_mAh", "CV_Capacity_mAh",
                "CC_Time_s", "CV_Time_s", "Transition_Voltage_V", "Charge_Start_Voltage_V",
                "Discharge_Start_Voltage_V", "Charge_Onset_R_mOhm", "Discharge_Onset_R_mOhm",
                "Avg_Temp_C", "Min_Temp_C", "Max_Temp_C", "Temp_Rise_C",
                "Discharge_Capacity_Retention_pct"]
        return rows, cols
