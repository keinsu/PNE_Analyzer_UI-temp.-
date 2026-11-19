# -*- coding: utf-8 -*-
"""
compare_page.py
여러 셀(각각 단일 파일 또는 이어붙인 여러 파일로 만든 사이클 요약)을 한 화면에서
겹쳐보고 비교하는 화면.
"""
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton, QLineEdit,
    QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt, Signal

from . import theme
from .widgets import AxisRangeControl, section_title
from .report_page import configure_plot_interaction, HoverReadout, make_hover_checkbox, fmt, fmt_duration

pg.setConfigOption("background", theme.PANEL_INPUT)
pg.setConfigOption("foreground", theme.TEXT)


class ComparePage(QWidget):
    """여러 셀의 사이클 요약을 겹쳐서 비교하는 화면. 셀 데이터(name, summary)는 이 페이지가
    직접 보관한다 (MainWindow는 add_cell()만 호출해주면 됨)."""

    addCellRequested = Signal()
    addBatchCellsRequested = Signal()
    cellsChanged = Signal()
    exportCapacityRequested = Signal()
    exportRetentionRequested = Signal()
    exportCERequested = Signal()
    exportResistanceRequested = Signal()
    exportTempRequested = Signal()
    exportTableRequested = Signal()

    PALETTE = [theme.TEAL, theme.AMBER, theme.CORAL, theme.PURPLE, theme.BLUE,
               theme.STEEL, theme.GOLD, theme.GREEN, theme.ORANGE, theme.PURPLE_DARK]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cells = []  # [{"name": str, "summary": list[dict]}, ...]

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        content = QWidget()
        self.root = QVBoxLayout(content)
        self.root.setSpacing(18)
        scroll.setWidget(content)

        self._build_cell_list_panel()
        self._build_chart_panel("사이클별 방전 용량 비교", "Discharge_Capacity_mAh", "Capacity (mAh)", "capacity")
        self._build_chart_panel("사이클별 용량유지율(%) 비교", "Discharge_Capacity_Retention_pct", "Retention (%)", "retention")
        self._build_chart_panel("사이클별 쿨롱효율(%) 비교", "Coulombic_Efficiency_pct", "CE (%)", "ce")
        self._build_chart_panel("사이클별 방전 온셋저항(mΩ) 비교", "Discharge_Onset_R_mOhm", "Onset R (mΩ)", "resistance")
        self._build_chart_panel("사이클별 평균 온도(℃) 비교", "Avg_Temp_C", "Temp (℃)", "temp")
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

    def _build_cell_list_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("비교 중인 셀 목록"))
        header.addStretch(1)
        btnBatch = QPushButton("+ 여러 셀 한번에 추가 (자동 매핑)")
        btnBatch.clicked.connect(self.addBatchCellsRequested.emit)
        header.addWidget(btnBatch)
        btnAdd = QPushButton("+ 새 셀 추가")
        btnAdd.setProperty("variant", "primary")
        btnAdd.clicked.connect(self.addCellRequested.emit)
        header.addWidget(btnAdd)
        lay.addLayout(header)

        hint = QLabel(
            "'여러 셀 한번에 추가'는 파일당 컬럼을 자동으로 인식해 바로 추가합니다 (파일 여러 개를"
            " 이어붙여야 하는 셀은 '+ 새 셀 추가'로 하나씩 진행해주세요)."
        )
        hint.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:10.5px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.cellListLayout = QVBoxLayout()
        self.cellListLayout.setSpacing(6)
        lay.addLayout(self.cellListLayout)

        self.emptyHint = QLabel("아직 추가된 셀이 없습니다. '+ 새 셀 추가'를 눌러 CSV를 업로드해보세요.")
        self.emptyHint.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:12px;")
        lay.addWidget(self.emptyHint)

    def _build_chart_panel(self, title, field, ylabel, key):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title(title))
        header.addStretch(1)
        btn = QPushButton("데이터 다운로드")
        header.addWidget(btn)
        lay.addLayout(header)

        axis_row = QHBoxLayout()
        axis_y = AxisRangeControl(f"{ylabel} 축")
        axis_row.addWidget(axis_y)
        axis_row.addStretch(1)

        plot = pg.PlotWidget()
        plot.setMinimumHeight(320)
        plot.addLegend(offset=(-10, 10))
        plot.showGrid(x=True, y=True, alpha=0.15)
        plot.setLabel("bottom", "Cycle")
        plot.setLabel("left", ylabel)
        configure_plot_interaction(plot)
        hover = HoverReadout(plot, y_label=ylabel)
        axis_row.addWidget(make_hover_checkbox([hover]))
        lay.addLayout(axis_row)
        lay.addWidget(plot)

        setattr(self, f"plot_{key}", plot)
        setattr(self, f"hover_{key}", hover)
        setattr(self, f"axis_{key}", axis_y)
        setattr(self, f"field_{key}", field)
        axis_y.changed.connect(lambda: self._update_chart(key))
        if key == "capacity":
            btn.clicked.connect(self.exportCapacityRequested.emit)
        elif key == "retention":
            btn.clicked.connect(self.exportRetentionRequested.emit)
        elif key == "ce":
            btn.clicked.connect(self.exportCERequested.emit)
        elif key == "resistance":
            btn.clicked.connect(self.exportResistanceRequested.emit)
        elif key == "temp":
            btn.clicked.connect(self.exportTempRequested.emit)

    def _build_table_panel(self):
        lay = self._panel()
        header = QHBoxLayout()
        header.addWidget(section_title("셀 비교 요약 테이블"))
        header.addStretch(1)
        btnCsv = QPushButton("테이블 CSV 다운로드")
        btnCsv.clicked.connect(self.exportTableRequested.emit)
        header.addWidget(btnCsv)
        lay.addLayout(header)

        self.table = QTableWidget()
        self.table.setMinimumHeight(200)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        lay.addWidget(self.table)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def add_cell(self, name: str, summary_records: list):
        self.cells.append({"name": name, "summary": summary_records})
        self._refresh_cell_list()
        self._update_all_charts()
        self._render_table()
        self.cellsChanged.emit()

    def remove_cell(self, index: int):
        if 0 <= index < len(self.cells):
            del self.cells[index]
            self._refresh_cell_list()
            self._update_all_charts()
            self._render_table()
            self.cellsChanged.emit()

    def rename_cell(self, index: int, new_name: str):
        if 0 <= index < len(self.cells) and new_name.strip():
            self.cells[index]["name"] = new_name.strip()
            self._refresh_cell_list()
            self._update_all_charts()
            self._render_table()
            self.cellsChanged.emit()

    def cell_color(self, index: int) -> str:
        return self.PALETTE[index % len(self.PALETTE)]

    # ------------------------------------------------------------------
    def _refresh_cell_list(self):
        while self.cellListLayout.count():
            item = self.cellListLayout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        self.emptyHint.setVisible(len(self.cells) == 0)

        for i, cell in enumerate(self.cells):
            row = QFrame()
            row.setObjectName("hairline")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 6, 10, 6)

            swatch = QLabel("●")
            swatch.setStyleSheet(f"color:{self.cell_color(i)}; font-size:16px;")
            rl.addWidget(swatch)

            name_inp = QLineEdit(cell["name"])
            name_inp.setStyleSheet("font-weight:600;")
            name_inp.editingFinished.connect(lambda idx=i, inp=name_inp: self.rename_cell(idx, inp.text()))
            rl.addWidget(name_inp, 1)

            n_cycles = len(cell["summary"])
            info = QLabel(f"{n_cycles} 사이클")
            info.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
            rl.addWidget(info)

            btnDel = QPushButton("삭제")
            btnDel.setStyleSheet("padding:4px 10px;")
            btnDel.clicked.connect(lambda checked=False, idx=i: self.remove_cell(idx))
            rl.addWidget(btnDel)

            self.cellListLayout.addWidget(row)

    def _update_all_charts(self):
        for key in ("capacity", "retention", "ce", "resistance", "temp"):
            self._update_chart(key)

    def _update_chart(self, key):
        plot = getattr(self, f"plot_{key}")
        hover = getattr(self, f"hover_{key}")
        axis = getattr(self, f"axis_{key}")
        field = getattr(self, f"field_{key}")

        plot.clear()
        hover.reattach()
        for i, cell in enumerate(self.cells):
            rows = cell["summary"]
            cyc = [r["Cycle"] for r in rows]
            val = [r.get(field) for r in rows]
            color = self.cell_color(i)
            plot.plot(cyc, val, pen=pg.mkPen(color, width=2), symbol="o", symbolSize=5,
                      symbolBrush=color, symbolPen=None, name=cell["name"])

        rng = axis.value_range()
        if rng:
            plot.setYRange(rng[0], rng[1], padding=0)
        else:
            plot.enableAutoRange(axis=pg.ViewBox.YAxis)

    def _render_table(self):
        cols = [
            ("셀", None), ("총 사이클", "n"), ("초기 방전용량", "init_cap"), ("최종 방전용량", "final_cap"),
            ("용량유지율(%)", "retention"), ("평균CE(%)", "avg_ce"),
            ("평균 충전OnsetR(mΩ)", "avg_chg_r"), ("평균 방전OnsetR(mΩ)", "avg_dis_r"),
            ("평균온도(℃)", "avg_temp"), ("최대온도(℃)", "max_temp"),
        ]
        self.table.setSortingEnabled(False)
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels([c[0] for c in cols])
        self.table.setRowCount(len(self.cells))

        for i, cell in enumerate(self.cells):
            rows = cell["summary"]
            stats = self._compute_stats(rows)
            values = [cell["name"], stats["n"], fmt(stats["init_cap"], 2), fmt(stats["final_cap"], 2),
                      fmt(stats["retention"], 1), fmt(stats["avg_ce"], 2),
                      fmt(stats["avg_chg_r"], 2), fmt(stats["avg_dis_r"], 2),
                      fmt(stats["avg_temp"], 2), fmt(stats["max_temp"], 2)]
            for j, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if j > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, j, item)
        self.table.setSortingEnabled(True)

    @staticmethod
    def _compute_stats(rows: list) -> dict:
        if not rows:
            return dict(n=0, init_cap=None, final_cap=None, retention=None, avg_ce=None, avg_chg_r=None,
                        avg_dis_r=None, avg_temp=None, max_temp=None)

        def valid(v):
            if v is None:
                return False
            try:
                return not np.isnan(v)
            except TypeError:
                return True

        valid_rows = [r for r in rows if valid(r.get("Discharge_Capacity_mAh"))]
        first = rows[0]
        last = valid_rows[-1] if valid_rows else rows[-1]
        ce_vals = [r["Coulombic_Efficiency_pct"] for r in rows if valid(r.get("Coulombic_Efficiency_pct"))]
        chg_r_vals = [r["Charge_Onset_R_mOhm"] for r in rows if valid(r.get("Charge_Onset_R_mOhm"))]
        dis_r_vals = [r["Discharge_Onset_R_mOhm"] for r in rows if valid(r.get("Discharge_Onset_R_mOhm"))]
        avg_temp_vals = [r["Avg_Temp_C"] for r in rows if valid(r.get("Avg_Temp_C"))]
        max_temp_vals = [r["Max_Temp_C"] for r in rows if valid(r.get("Max_Temp_C"))]
        return dict(
            n=len(valid_rows) if valid_rows else len(rows),
            init_cap=first.get("Discharge_Capacity_mAh"),
            final_cap=last.get("Discharge_Capacity_mAh"),
            retention=last.get("Discharge_Capacity_Retention_pct"),
            avg_ce=(sum(ce_vals) / len(ce_vals)) if ce_vals else None,
            avg_chg_r=(sum(chg_r_vals) / len(chg_r_vals)) if chg_r_vals else None,
            avg_dis_r=(sum(dis_r_vals) / len(dis_r_vals)) if dis_r_vals else None,
            avg_temp=(sum(avg_temp_vals) / len(avg_temp_vals)) if avg_temp_vals else None,
            max_temp=max(max_temp_vals) if max_temp_vals else None,
        )

    def table_export_rows(self):
        cols = ["cell", "n_cycles", "init_capacity_mAh", "final_capacity_mAh", "retention_pct",
                "avg_ce_pct", "avg_charge_onset_r_mOhm", "avg_discharge_onset_r_mOhm",
                "avg_temp_C", "max_temp_C"]
        rows = []
        for cell in self.cells:
            stats = self._compute_stats(cell["summary"])
            rows.append([cell["name"], stats["n"], stats["init_cap"], stats["final_cap"],
                         stats["retention"], stats["avg_ce"], stats["avg_chg_r"], stats["avg_dis_r"],
                         stats["avg_temp"], stats["max_temp"]])
        return cols, rows

    def chart_export_rows(self, field: str):
        rows = []
        for cell in self.cells:
            for r in cell["summary"]:
                rows.append([cell["name"], r.get("Cycle"), r.get(field)])
        return ["cell", "cycle", field], rows
