"""Study overview and checkpoint-first RPT comparison."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .project import default_output_dir
from .rpt_summary import METRICS
from .widgets import CurvePlot, DataTable, PALETTE, display_value


def management_label(channel):
    management_id = str(channel.metadata.get("management_id") or channel.name)
    return management_id if management_id == channel.name else f"{management_id} · {channel.name}"


def rpt_checkpoints(cell, channels):
    """Return linked RPTs and, where selected, the reference cell's BOL."""
    if cell is None:
        return []
    if cell.metadata.get("import_kind") == "rpt":
        return [cell]

    reference = cell.metadata.get("reference_bol_cell_id")
    included = []
    for channel in channels:
        if channel.metadata.get("import_kind") != "rpt":
            continue
        link = channel.metadata.get("rpt_link") or {}
        owner = link.get("cell_id")
        is_reference_bol = owner == reference and reference and link.get("stage") == "bol"
        if owner == cell.id or is_reference_bol:
            included.append(channel)
    return sorted(included, key=lambda c: (
        0 if (c.metadata.get("rpt_link") or {}).get("stage") == "bol" else 1,
        (c.metadata.get("rpt_link") or {}).get("cycle") or 0, c.name, c.id))


def checkpoint_label(channel, cell=None):
    link = channel.metadata.get("rpt_link") or {}
    if link.get("stage") == "bol":
        if cell and link.get("cell_id") != cell.id:
            reference = cell.metadata.get("reference_bol_label")
            return f"BOL · {reference}" if reference else "참조 BOL"
        return "BOL"
    cycle = link.get("cycle")
    return f"{cycle:,} Cycle" if cycle is not None else channel.name


def rpt_capacity_energy(rows):
    values = {}
    for key, label in METRICS.items():
        matches = [row.get("Value") for row in rows if row.get("Metric") == key]
        if not matches:
            matches = [row.get("Value") for row in rows if row.get("Item") == key]
        values[label] = matches[0] if len(matches) == 1 else None
    return values


def rpt_dcir_rows(checkpoint, rows):
    result = []
    for row in rows:
        if row.get("Item") != "방전 10초 DCIR" or row.get("Direction") not in (None, "DISCHARGE"):
            continue
        result.append({"RPT 시점": checkpoint, "SOC (%)": row.get("SOC_pct"), "펄스": row.get("Pulse"),
                       "10초 DCIR (mΩ)": row.get("Value"), "상태": row.get("Status") or ""})
    return result


def _tree(headers):
    tree = QTreeWidget()
    tree.setHeaderLabels(headers)
    tree.setRootIsDecorated(False)
    tree.setAlternatingRowColors(True)
    tree.setUniformRowHeights(True)
    return tree


class StudyOverview(QWidget):
    cellRequested = Signal(str)
    rptRequested = Signal()
    lifeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.title = QLabel("시험")
        self.title.setStyleSheet("font-size:21px;font-weight:600")
        layout.addWidget(self.title)
        self.cells = _tree(["관리 ID", "셀", "진행 Cycle", "최근 RPT", "기준 BOL"])
        self.cells.itemClicked.connect(self._select_cell)
        layout.addWidget(self.cells, 2)
        self.history_title = QLabel("선택 셀의 등록 이력")
        layout.addWidget(self.history_title)
        self.timeline = _tree(["시점", "등록 데이터"])
        layout.addWidget(self.timeline, 1)
        buttons = QHBoxLayout()
        self.open_life = QPushButton("수명 추이")
        self.open_life.clicked.connect(lambda: self.lifeRequested.emit())
        self.open_rpt = QPushButton("RPT 비교")
        self.open_rpt.clicked.connect(lambda: self.rptRequested.emit())
        buttons.addWidget(self.open_life)
        buttons.addWidget(self.open_rpt)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.set_context(None, [], None)

    def set_context(self, cell, channels, study=None):
        if study is None:
            self.title.setText("시험")
        else:
            self.title.setText(study.data.get("name") or "시험")
        layout_rows = [c for c in channels if c.metadata.get("input_type") == "study_cell"]
        rank = lambda c: (str(c.metadata.get("management_id") or c.name).casefold(), c.id)
        self.cells.clear()
        for source in sorted(layout_rows, key=rank):
            meta = source.metadata
            latest = meta.get("latest_rpt_cycle")
            latest = "BOL" if latest == 0 else display_value(latest)
            item = QTreeWidgetItem([str(meta.get("management_id") or source.name), source.name,
                                    display_value(meta.get("latest_cycle")), latest,
                                    str(meta.get("reference_bol_label") or "—")])
            item.setData(0, Qt.ItemDataRole.UserRole, source.id)
            self.cells.addTopLevelItem(item)
            if cell and cell.id == source.id:
                self.cells.setCurrentItem(item)
        for column in range(4):
            self.cells.resizeColumnToContents(column)
        self.timeline.clear()
        self.history_title.setText(f"{management_label(cell)} · 등록 이력" if cell else "선택 셀의 등록 이력")
        if cell and study:
            for event in study.timeline(cell.id):
                kind = {"bol": "BOL RPT", "rpt": "RPT", "life": "수명시험", "cycle": "수명시험"}
                item = QTreeWidgetItem([str(event.get("label", "")), kind.get(event.get("kind"), "시험 데이터")])
                source = event.get("source") or ""
                if source:
                    item.setToolTip(0, str(source))
                self.timeline.addTopLevelItem(item)
            self.timeline.resizeColumnToContents(0)
        self.open_life.setEnabled(bool(cell and cell.metadata.get("has_life", True)))
        self.open_rpt.setEnabled(bool(cell and rpt_checkpoints(cell, channels)))

    def _select_cell(self, item, _):
        self.cellRequested.emit(item.data(0, Qt.ItemDataRole.UserRole))


class RPTComparison(QWidget):
    compareRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.title = QLabel("RPT 비교")
        self.title.setStyleSheet("font-size:21px;font-weight:600")
        layout.addWidget(self.title)
        self.message = QLabel("관리 ID를 선택하세요.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        self.checkpoints = _tree(["RPT 시점", *METRICS.values()])
        self.checkpoints.setMaximumHeight(206)
        self.checkpoints.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.checkpoints)
        actions = QHBoxLayout()
        self.compare = QPushButton("체크한 시점 dQ/dV 비교")
        self.compare.setEnabled(False)
        self.compare.clicked.connect(self.compare_selected)
        self.save_summary = QPushButton("용량·에너지 CSV")
        self.save_summary.setEnabled(False)
        self.save_summary.clicked.connect(self._export_summary)
        actions.addWidget(self.compare)
        actions.addWidget(self.save_summary)
        actions.addStretch()
        layout.addLayout(actions)

        self.views = QTabWidget()
        self.graph = CurvePlot("SOC (%)", "방전 10초 DCIR (mΩ)")
        self.views.addTab(self.graph, "SOC별 DCIR")
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail = DataTable()
        detail_layout.addWidget(self.detail)
        self.save_dcir = QPushButton("펄스 표 CSV")
        self.save_dcir.clicked.connect(lambda: self._export(self.detail.model.frame, "RPT_DCIR.csv"))
        detail_layout.addWidget(self.save_dcir)
        self.views.addTab(detail, "펄스 상세")
        self.views.currentChanged.connect(self._render_dcir)
        layout.addWidget(self.views, 1)

        self._sources = []
        self._rows = {}
        self._labels = {}
        self._cell = None
        self._key = None
        self._needs_load = False

    def set_context(self, cell, channels):
        sources = rpt_checkpoints(cell, channels)
        key = (None if cell is None else (cell.id, management_label(cell), cell.metadata.get("reference_bol_label")),
               tuple((c.id, c.name, c.fingerprint, tuple(sorted((c.metadata.get("rpt_link") or {}).items())))
                     for c in sources))
        if key == self._key:
            return
        self._key, self._cell, self._sources = key, cell, sources
        self._needs_load = True
        if self.isVisible():
            self._load_context()

    def showEvent(self, event):
        super().showEvent(event)
        if self._needs_load:
            self._load_context()

    def _load_context(self):
        self._needs_load = False
        self._rows, failures = {}, []
        self.title.setText(f"{management_label(self._cell)} · RPT 비교" if self._cell else "RPT 비교")
        labels = [checkpoint_label(channel, self._cell) for channel in self._sources]
        counts = Counter(labels)
        self._labels = {channel.id: (f"{label} · {channel.name}" if counts[label] > 1 else label)
                        for channel, label in zip(self._sources, labels)}
        self.checkpoints.blockSignals(True)
        try:
            self.checkpoints.clear()
            for index, channel in enumerate(self._sources):
                try:
                    rows = self._rows[channel.id] = channel.frame.rpt_rows()
                except (OSError, ValueError, KeyError) as exc:
                    rows = self._rows[channel.id] = []
                    failures.append(f"{channel.name}: {exc}")
                values = rpt_capacity_energy(rows)
                item = QTreeWidgetItem([self._labels[channel.id],
                                        *(display_value(value) for value in values.values())])
                item.setToolTip(0, f"{channel.name} · {Path(channel.source_path).name}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setData(0, Qt.ItemDataRole.UserRole, channel.id)
                item.setCheckState(0, Qt.CheckState.Checked if index in (0, len(self._sources) - 1)
                                   else Qt.CheckState.Unchecked)
                self.checkpoints.addTopLevelItem(item)
        finally:
            self.checkpoints.blockSignals(False)
        for column in range(len(METRICS) + 1):
            self.checkpoints.resizeColumnToContents(column)
        reference = self._cell.metadata.get("reference_bol_label") if self._cell else None
        note = ("관리 ID를 선택하세요." if self._cell is None else
                "연결된 RPT가 없습니다." if not self._sources else
                f"기준 BOL: {reference}" if reference else "")
        if failures:
            note = "\n".join([note, *failures]).strip()
        self.message.setText(note)
        self.message.setVisible(bool(note))
        self.checkpoints.setVisible(bool(self._sources))
        self.views.setVisible(bool(self._sources))
        self.save_summary.setEnabled(bool(self._sources))
        self._selection_changed()

    def selected_checkpoints(self):
        checked = {self.checkpoints.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                   for i in range(self.checkpoints.topLevelItemCount())
                   if self.checkpoints.topLevelItem(i).checkState(0) == Qt.CheckState.Checked}
        return [channel for channel in self._sources if channel.id in checked]

    def compare_selected(self):
        channels = self.selected_checkpoints()
        if channels:
            self.compareRequested.emit(channels)

    def _selection_changed(self, *_):
        self.compare.setEnabled(bool(self.selected_checkpoints()))
        self._render_dcir()

    def _render_dcir(self, *_):
        checked = self.selected_checkpoints()
        rows = [(channel, rpt_dcir_rows(self._labels[channel.id], self._rows.get(channel.id, [])))
                for channel in checked]
        if self.views.currentIndex() == 1:
            self.detail.set_frame(pd.DataFrame([entry for _, entries in rows for entry in entries],
                                              columns=["RPT 시점", "SOC (%)", "펄스", "10초 DCIR (mΩ)", "상태"]))
            self.save_dcir.setEnabled(not self.detail.model.frame.empty)
            return
        curves = []
        for index, (channel, entries) in enumerate(rows):
            groups = {}
            for entry in entries:
                soc, value = entry["SOC (%)"], entry["10초 DCIR (mΩ)"]
                if soc is None or value is None or not (np.isfinite(soc) and np.isfinite(value)):
                    continue
                groups.setdefault(soc, []).append(value)
            for repeat in range(max(map(len, groups.values()), default=0)):
                pairs = [(soc, groups[soc][repeat]) for soc in sorted(groups) if len(groups[soc]) > repeat]
                x, y = zip(*pairs)
                label = self._labels[channel.id]
                suffix = f" · 펄스 {repeat + 1}" if repeat else ""
                curves.append(dict(name=label + suffix, source_id=channel.id,
                                   color=PALETTE[index % len(PALETTE)], point_kind="calculated",
                                   x=np.asarray(x), y=np.asarray(y)))
        self.graph.set_curves(curves)
        for item, curve in zip(self.graph.items, curves):
            item.setSymbol("o")
            item.setSymbolSize(7)
            item.setSymbolBrush(curve["color"])

    def _export_summary(self):
        records = [{"RPT 시점": self._labels[c.id],
                    **rpt_capacity_energy(self._rows.get(c.id, []))} for c in self._sources]
        self._export(pd.DataFrame(records), "RPT_Summary.csv")

    def _export(self, frame, filename):
        if frame.empty:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "RPT 표 저장", str(Path(default_output_dir()) / filename), "CSV (*.csv)")
        if path:
            try:
                frame.to_csv(path, index=False, encoding="utf-8-sig")
            except OSError as exc:
                QMessageBox.warning(self, "저장 실패", str(exc))
