"""Shared graph and virtual table controls; display state never alters analysis data."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
import pyqtgraph.exporters
from PySide6.QtCore import Qt, Signal, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QTimer, QItemSelectionModel
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableView, QSplitter, QListWidget, QListWidgetItem, QFileDialog, QMessageBox,
    QAbstractItemView,
)

# High-contrast colors selected for the dark plot background.  The former
# muted teal was too close to the grid/background on company monitors.
PALETTE = ['#00E5FF', '#FFD166', '#FF6B6B', '#A78BFA', '#7CFF6B', '#FF8AD8', '#4D9FFF', '#FF9F43']
CURVE_WIDTH = 1.8
HEADERS = {'Channel': '채널', 'Cycle': 'Cycle', 'OriginalCycle': '원본 Cycle',
    'Discharge_Capacity_mAh': '방전용량 (mAh)', 'Charge_Capacity_mAh': '충전용량 (mAh)',
    'Discharge_Capacity_Retention_pct': '용량 유지율 (%)', 'Discharge_Energy_mWh': '방전에너지 (mWh)',
    'Energy_Retention_pct': '에너지 유지율 (%)', 'Coulombic_Efficiency_pct': 'CE (%)',
    'Energy_Efficiency_pct': 'EE (%)', 'Avg_Temp_C': '평균 온도 (°C)', 'Max_Temp_C': '최대 온도 (°C)',
    'Energy_Baseline_Cycle': '에너지 기준 Cycle', 'Source_Cycle': '누적 Cycle',
    'Discharge_Energy_mWh': '방전에너지 (mWh)', 'Energy_Retention_pct': '에너지 유지율 (%)',
    'Baseline_Cycle': '기준 Cycle', 'Source_ID': '원본 ID', 'Source_File': '원본 파일',
    'MatchStatus': '매칭 상태', 'StepNo': 'StepNo', 'PhysicalStepID': '물리 Step ID',
    'Voltage_V': '전압 (V)', 'Current_mA': '전류 (mA)', 'Capacity_mAh': '용량 (mAh)',
    'StepTime_s': 'Step 시간 (s)', 'Warnings': '검토 사항', 'AnalysisError': '계산 오류'}


def cycle_gradient(cycles):
    """Spread selected Cycles across cyan and coral in numerical order."""
    order = sorted(set(cycles))
    first, last = QColor('#00E5FF'), QColor('#FF769D')
    start, end = np.array(first.getRgb()[:3]), np.array(last.getRgb()[:3])
    return {cycle: QColor(*np.rint(start + (end - start) * index / max(1, len(order) - 1))
                           .astype(int).tolist()).name()
            for index, cycle in enumerate(order)}


def display_value(value):
    if value is None:
        return '—'
    if isinstance(value, (float, np.floating)):
        return f'{value:.7g}' if math.isfinite(value) else '—'
    return str(value)


class FrameModel(QAbstractTableModel):
    def __init__(self, frame=None, parent=None):
        super().__init__(parent)
        self.frame = frame if frame is not None else pd.DataFrame()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value = self.frame.iat[index.row(), index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return display_value(value)
        if role == Qt.ItemDataRole.UserRole:
            return value.item() if isinstance(value, np.generic) else value
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(value, (float, int, np.number)):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            key = str(self.frame.columns[section]) if orientation == Qt.Orientation.Horizontal else str(section + 1)
            return HEADERS.get(key, key) if orientation == Qt.Orientation.Horizontal else key
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal:
            return str(self.frame.columns[section])
        return None

    def set_frame(self, frame):
        self.beginResetModel()
        self.frame = frame.copy(deep=False)
        self.endResetModel()


class FrameProxy(QSortFilterProxyModel):
    """Vectorized stable sort avoids millions of Python comparator callbacks."""
    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        super().sort(-1, order)
        model = self.sourceModel()
        if model is not None and 0 <= column < len(model.frame.columns):
            name = model.frame.columns[column]
            sorted_frame = model.frame.sort_values(name, ascending=order == Qt.SortOrder.AscendingOrder,
                                                    kind='stable', na_position='last').reset_index(drop=True)
            model.set_frame(sorted_frame)


class DataTable(QWidget):
    rowSelected = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.model = FrameModel(parent=self)
        self.proxy = FrameProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setAlternatingRowColors(True)
        self.view.horizontalHeader().setDefaultSectionSize(150)
        self.view.clicked.connect(self._clicked)
        QShortcut(QKeySequence.StandardKey.Copy, self.view, self.copy_selection)
        layout.addWidget(self.view)
        self.hint = QLabel('표 선택 후 Ctrl+C · 열 제목을 눌러 정렬')
        self.hint.setStyleSheet('color:#91a8b5;font-size:11px')
        layout.addWidget(self.hint)

    def set_frame(self, frame):
        self.view.setSortingEnabled(False)
        self.model.set_frame(frame)
        self.view.setSortingEnabled(True)
        self.hint.setText(f'{len(frame):,}행 · 표 선택 후 Ctrl+C · 열 제목을 눌러 정렬')

    def _clicked(self, index):
        source = self.proxy.mapToSource(index)
        self.rowSelected.emit(self.model.frame.iloc[source.row()].to_dict())

    def copy_selection(self):
        selected = self.view.selectionModel().selectedIndexes()
        if not selected:
            return
        minr, maxr = min(i.row() for i in selected), max(i.row() for i in selected)
        minc, maxc = min(i.column() for i in selected), max(i.column() for i in selected)
        cells = {(i.row(), i.column()): i.data() for i in selected}
        text = '\n'.join('\t'.join(str(cells.get((r, c), '')) for c in range(minc, maxc + 1))
                         for r in range(minr, maxr + 1))
        QApplication.clipboard().setText(text)


class CurvePlot(QWidget):
    curveSelected = Signal(dict)

    def __init__(self, x_label='', y_label='', parent=None):
        super().__init__(parent)
        self.curves = []
        self.items = []
        self.peak_items = []
        self._emphasized = set()
        self._legend_groups = []
        self._curve_to_legend = []
        self.right_view = None
        self.right_label = ''
        self._history = []
        self._restoring = False
        self._stale = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.mode = QPushButton('영역 확대')
        self.mode.setCheckable(True)
        self.mode.setChecked(True)
        self.mode.clicked.connect(self._mode)
        toolbar.addWidget(self.mode)
        for label, callback in [('전체 보기', self.auto_range), ('이전 범위', self.previous_range)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        self.clear_emphasis = QPushButton('강조 해제')
        self.clear_emphasis.setEnabled(False)
        toolbar.addWidget(self.clear_emphasis)
        toolbar.addStretch()
        self.png_btn = QPushButton('PNG 저장')
        self.csv_btn = QPushButton('곡선 CSV')
        self.origin_btn = QPushButton('PPT에 그래프 붙여넣기')
        self.origin_btn.setToolTip('PowerPoint의 선택한 슬라이드에 편집 가능한 Origin 그래프를 붙여넣습니다.')
        self.origin_btn.setEnabled(False)
        self.origin_btn.hide()
        self.png_btn.clicked.connect(self.export_png)
        self.csv_btn.clicked.connect(self.export_csv)
        toolbar.addWidget(self.png_btn)
        toolbar.addWidget(self.csv_btn)
        toolbar.addWidget(self.origin_btn)
        outer.addLayout(toolbar)
        axes = QHBoxLayout()
        self.axis_inputs = []
        for label in ['X 최소', 'X 최대', 'Y 최소', 'Y 최대']:
            axes.addWidget(QLabel(label))
            edit = QLineEdit()
            edit.setPlaceholderText('자동')
            edit.setMaximumWidth(95)
            edit.returnPressed.connect(self.apply_axes)
            self.axis_inputs.append(edit)
            axes.addWidget(edit)
        apply_button = QPushButton('축 적용')
        apply_button.clicked.connect(self.apply_axes)
        axes.addWidget(apply_button)
        axes.addStretch()
        outer.addLayout(axes)
        self.splitter = QSplitter()
        self.plot = pg.PlotWidget(background='#101b22')
        self.plot.showGrid(x=True, y=True, alpha=.18)
        self.plot.setLabel('bottom', x_label)
        self.plot.setLabel('left', y_label)
        self.plot.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        self.plot.getViewBox().sigRangeChangedManually.connect(self._remember_range)
        self._resolution_timer = QTimer(self)
        self._resolution_timer.setSingleShot(True)
        self._resolution_timer.timeout.connect(self._refresh_resolution)
        self.plot.getViewBox().sigXRangeChanged.connect(self._queue_resolution)
        self.list = QListWidget()
        self.list.setMinimumWidth(140)
        self.list.setMaximumWidth(260)
        self.list.itemChanged.connect(self._visibility)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.list.itemSelectionChanged.connect(self._update_emphasis)
        self.clear_emphasis.clicked.connect(self.list.clearSelection)
        legend = QWidget()
        legend_layout = QVBoxLayout(legend)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_search = QLineEdit()
        self.legend_search.setPlaceholderText('Cycle / 채널 검색')
        self.legend_search.textChanged.connect(self._filter_legend)
        self.legend_search.hide()
        legend_layout.addWidget(self.legend_search)
        legend_layout.addWidget(self.list)
        legend.setMaximumWidth(280)
        self.splitter.addWidget(self.plot)
        self.splitter.addWidget(legend)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setSizes([1000, 180])
        outer.addWidget(self.splitter, 1)
        self.readout = QLabel('마우스를 곡선 위에 올리면 값을 확인합니다. 목록에서 곡선을 선택하면 우선 조회합니다.')
        self.readout.setWordWrap(True)
        self.readout.setMinimumHeight(32)
        outer.addWidget(self.readout)
        self.proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=24, slot=self._hover)
        self._add_cursor()

    def _add_cursor(self):
        self.vline = pg.InfiniteLine(angle=90, pen=pg.mkPen('#91a8b5', style=Qt.PenStyle.DashLine))
        self.hline = pg.InfiniteLine(angle=0, pen=pg.mkPen('#91a8b5', style=Qt.PenStyle.DashLine))
        self.marker = pg.ScatterPlotItem(size=9, brush='#ffffff', pen=pg.mkPen('#45d8c0', width=2))
        for item in (self.vline, self.hline, self.marker):
            self.plot.addItem(item, ignoreBounds=True)
            item.setZValue(10)
            item.hide()

    def set_labels(self, x, y):
        self.plot.setLabel('bottom', x)
        self.plot.setLabel('left', y)

    def _resize_right_axis(self):
        main = self.plot.getViewBox()
        self.right_view.setGeometry(main.sceneBoundingRect())
        self.right_view.linkedViewChanged(main, pg.ViewBox.XAxis)

    def _configure_right_axis(self, label):
        plot = self.plot.plotItem
        if self.right_view is None and label:
            self.right_view = pg.ViewBox(enableMouse=False, enableMenu=False)
            self.plot.scene().addItem(self.right_view)
            self.right_view.setXLink(plot.vb)
            plot.getAxis('right').linkToView(self.right_view)
            plot.vb.sigResized.connect(self._resize_right_axis)
        if self.right_view is not None:
            self.right_view.clear()
            self.right_view.setVisible(bool(label))
            plot.showAxis('right', show=bool(label))
            if label:
                plot.setLabel('right', label)
                self._resize_right_axis()
        self.right_label = label

    def set_curves(self, curves, *, group_cycles=False, show_peaks=False, right_label=''):
        self.curves = [c for c in curves if len(c.get('x', [])) and len(c.get('x', [])) == len(c.get('y', []))]
        self.items = []
        self.peak_items = []
        self._emphasized = set()
        self._legend_groups = []
        self._curve_to_legend = []
        groups = {}
        multi_channel = len({c.get('source_id') for c in self.curves}) > 1
        self.list.blockSignals(True)
        self.list.clear()
        self.plot.clear()
        self._configure_right_axis(right_label if any(c.get('right_axis') for c in self.curves) else '')
        self._add_cursor()
        for i, curve in enumerate(self.curves):
            color = curve.get('color', PALETTE[i % len(PALETTE)])
            style = Qt.PenStyle.DashLine if curve.get('right_axis') or curve.get('direction') in ('discharge', 'DISCHARGE') else Qt.PenStyle.SolidLine
            x, y = np.asarray(curve['x'], dtype=float), np.asarray(curve['y'], dtype=float)
            curve['_x_sorted'] = bool(len(x) < 2 or np.all(np.diff(x) >= 0))
            indices = curve.get('plot_indices')
            plot_x, plot_y = (x[indices], y[indices]) if indices is not None else (x, y)
            item = pg.PlotDataItem(plot_x, plot_y, name=curve.get('name', str(i)),
                                   pen=pg.mkPen(color, width=CURVE_WIDTH, style=style), connect='finite',
                                   symbol='o' if curve.get('right_axis') else None, symbolSize=7)
            if curve.get('right_axis'):
                item.setSymbolBrush(color)
                item.setSymbolPen(pg.mkPen(color))
                self.right_view.addItem(item)
            else:
                self.plot.addItem(item)
            if curve['_x_sorted'] and not curve.get('right_axis'):
                item.setDownsampling(auto=True, method='peak')
                item.setClipToView(True)
            item.setCurveClickable(True, width=8)
            item.sigClicked.connect(lambda *_, index=i: self._toggle_curve_emphasis(index))
            self.items.append(item)
            peaks = curve.get('peaks', []) if show_peaks else []
            marker = None
            if peaks:
                marker = pg.ScatterPlotItem(x=[p['voltage'] for p in peaks],
                                            y=[p['value'] for p in peaks],
                                            symbol='o', size=9, pen=pg.mkPen('#eaf5fa', width=1.2))
                marker.setZValue(3)
                self.plot.addItem(marker, ignoreBounds=True)
            self.peak_items.append(marker)
            key = (curve.get('source_id'), curve['cycle']) if group_cycles else i
            legend_index = groups.get(key)
            if legend_index is not None:
                self._legend_groups[legend_index].append(i)
                self._curve_to_legend.append(legend_index)
                continue
            legend_index = len(self._legend_groups)
            groups[key] = legend_index
            self._legend_groups.append([i])
            self._curve_to_legend.append(legend_index)
            label = curve.get('name', f'곡선 {i + 1}')
            if group_cycles:
                label = curve.get('legend_label') or f"Cycle {curve['cycle']}"
                if multi_channel and not curve.get('legend_label'):
                    label += f" · {curve.get('channel', '')}"
            row = QListWidgetItem(label)
            row.setToolTip(label)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Checked)
            row.setForeground(QColor(color))
            self.list.addItem(row)
        self.list.blockSignals(False)
        self._update_emphasis()
        self.legend_search.setVisible(group_cycles and bool(groups))
        self._filter_legend(self.legend_search.text() if group_cycles else '')
        self.set_stale(False)
        self.auto_range()

    def _filter_legend(self, text):
        query = text.strip().casefold()
        for row in range(self.list.count()):
            item = self.list.item(row)
            label = item.text().casefold()
            match = label.split(' · ', 1)[0] == f'cycle {query}' if query.isdecimal() else query in label
            item.setHidden(not match)

    def update_colors(self):
        for i, (curve, item) in enumerate(zip(self.curves, self.items)):
            color = curve.get('color', PALETTE[i % len(PALETTE)])
            style = Qt.PenStyle.DashLine if curve.get('right_axis') or curve.get('direction') in ('discharge', 'DISCHARGE') else Qt.PenStyle.SolidLine
            emphasized = i in self._emphasized
            item.setPen(pg.mkPen(color, width=CURVE_WIDTH * (1.2 if emphasized else 1), style=style))
            opacity = 1.0 if emphasized or not self._emphasized else 0.32
            item.setOpacity(opacity)
            item.setZValue(1 if emphasized else 0)
            marker = self.peak_items[i]
            if marker is not None:
                marker.setBrush(pg.mkBrush(color))
                marker.setOpacity(opacity)
        previous = self.list.blockSignals(True)
        for row, group in enumerate(self._legend_groups):
            curve = self.curves[group[0]]
            self.list.item(row).setForeground(QColor(curve.get('color', PALETTE[group[0] % len(PALETTE)])))
        self.list.blockSignals(previous)

    def _queue_resolution(self, *_):
        if any('plot_indices' in curve for curve in self.curves):
            self._resolution_timer.start(110)

    def _refresh_resolution(self):
        from .analysis import _display_indices
        view = self.plot.getViewBox()
        lo, hi = view.viewRange()[0]
        if not np.isfinite([lo, hi]).all() or hi <= lo:
            return
        for curve, item in zip(self.curves, self.items):
            if 'plot_indices' not in curve:
                continue
            x, y = curve['x'], curve['y']
            indices = curve['plot_indices']
            if not view.autoRangeEnabled()[0]:
                axes = x if curve['_x_sorted'] else -x
                if curve['_x_sorted'] or np.all(axes[1:] >= axes[:-1]):
                    low, high = (lo, hi) if curve['_x_sorted'] else (-hi, -lo)
                    a = max(0, int(np.searchsorted(axes, low)) - 1)
                    b = min(len(x), int(np.searchsorted(axes, high, side='right')) + 1)
                    if 0 < b - a < len(x):
                        indices = (np.arange(a, b) if b - a <= 1600
                                   else a + _display_indices(x[a:b], y[a:b], 1600))
            item.setData(x[indices], y[indices], connect='finite')

    def set_stale(self, stale):
        self._stale = stale
        self.png_btn.setEnabled(not stale and bool(self.curves))
        self.csv_btn.setEnabled(not stale and bool(self.curves))
        self._update_origin_button()
        if stale:
            self.readout.setText('설정이 변경되었습니다. 재분석 후 새 조건의 결과를 저장하세요.')

    def _mode(self):
        zoom = self.mode.isChecked()
        self.mode.setText('영역 확대' if zoom else '그래프 이동')
        self.plot.getViewBox().setMouseMode(pg.ViewBox.RectMode if zoom else pg.ViewBox.PanMode)

    def _remember_range(self, *_):
        if not self._restoring:
            self._history.append(self.plot.viewRange())
            self._history = self._history[-30:]

    def auto_range(self):
        self.plot.enableAutoRange()
        for curve, item in zip(self.curves, self.items):
            if 'plot_indices' in curve:
                indices = curve['plot_indices']
                item.setData(curve['x'][indices], curve['y'][indices], connect='finite')
        self.plot.autoRange()
        if self.right_label:
            self.right_view.enableAutoRange(axis=pg.ViewBox.YAxis)
            self.right_view.updateAutoRange()
            bounds = self.plot.viewRange()[0]
            right_x = [c['x'] for c in self.curves if c.get('right_axis')]
            lo, hi = min(np.min(x) for x in right_x), max(np.max(x) for x in right_x)
            if lo < bounds[0] or hi > bounds[1]:
                self.plot.setXRange(min(lo, bounds[0]), max(hi, bounds[1]), padding=.02)
        self._history = [self.plot.viewRange()]
        for edit in self.axis_inputs:
            edit.clear()

    def previous_range(self):
        if len(self._history) > 1:
            self._history.pop()
            self._restoring = True
            previous = self._history[-1]
            self.plot.setRange(xRange=previous[0], yRange=previous[1], padding=0)
            self._restoring = False

    def apply_axes(self):
        try:
            vals = [float(edit.text()) if edit.text().strip() else None for edit in self.axis_inputs]
            current = self.plot.viewRange()
            for axis, lo, hi in [(0, vals[0], vals[1]), (1, vals[2], vals[3])]:
                if lo is None and hi is None:
                    self.plot.enableAutoRange(axis=axis)
                    continue
                lo = current[axis][0] if lo is None else lo
                hi = current[axis][1] if hi is None else hi
                if not math.isfinite(lo) or not math.isfinite(hi) or lo >= hi:
                    raise ValueError('최소값은 최대값보다 작아야 합니다.')
                (self.plot.setXRange if axis == 0 else self.plot.setYRange)(lo, hi, padding=0)
            self._remember_range()
        except ValueError as exc:
            QMessageBox.warning(self, '축 범위 확인', str(exc))

    def _visibility(self, *_):
        rows = [self.list.row(_[0])] if _ else range(len(self._legend_groups))
        for row in rows:
            if row < 0:
                continue
            visible = self.list.item(row).checkState() == Qt.CheckState.Checked
            for i in self._legend_groups[row]:
                self.items[i].setVisible(visible)
                if self.peak_items[i] is not None:
                    self.peak_items[i].setVisible(visible)
        self._update_origin_button()
        self._update_emphasis()

    def checked_curves(self):
        return [curve for i, curve in enumerate(self.curves)
                if self.list.item(self._curve_to_legend[i]).checkState() == Qt.CheckState.Checked]

    def _update_origin_button(self):
        self.origin_btn.setEnabled(not self._stale and any(
            self.list.item(row).checkState() == Qt.CheckState.Checked
            for row in range(self.list.count())))

    def _toggle_curve_emphasis(self, index):
        legend_item = self.list.item(self._curve_to_legend[index])
        legend_item.setSelected(not legend_item.isSelected())

    def _update_emphasis(self):
        previous = self._emphasized
        self._emphasized = {i for row in self.list.selectedItems()
                            if row.checkState() == Qt.CheckState.Checked
                            for i in self._legend_groups[self.list.row(row)]}
        self.clear_emphasis.setEnabled(bool(self.list.selectedItems()))
        self.update_colors()
        added = self._emphasized - previous
        if added:
            self.curveSelected.emit(self.curves[min(added)])

    def highlight_cycle(self, cycle):
        for i, curve in enumerate(self.curves):
            if curve.get('cycle') == cycle:
                self.list.setCurrentRow(self._curve_to_legend[i],
                                        QItemSelectionModel.SelectionFlag.ClearAndSelect)
                break

    def _hover(self, event):
        pos = event[0]
        view = self.plot.getViewBox()
        if not self.plot.sceneBoundingRect().contains(pos):
            return
        candidates = sorted(self._emphasized) if self._emphasized else range(len(self.curves))
        best = None
        for i in candidates:
            if not self.items[i].isVisible():
                continue
            curve = self.curves[i]
            axes = self.right_view if curve.get('right_axis') else view
            pt = axes.mapSceneToView(pos)
            ranges = axes.viewRange()
            sx = max(1e-12, ranges[0][1] - ranges[0][0])
            sy = max(1e-12, ranges[1][1] - ranges[1][0])
            x, y = np.asarray(curve['x']), np.asarray(curve['y'])
            if curve.get('_x_sorted'):
                j = int(np.searchsorted(x, pt.x()))
                indices = np.arange(max(0, j - 2), min(len(x), j + 3))
            else:
                # Search a coarse subset first; refine in original data, never in downsampled points.
                stride = max(1, len(x) // 4000)
                coarse = np.arange(0, len(x), stride)
                scores = ((x[coarse] - pt.x()) / sx) ** 2 + ((y[coarse] - pt.y()) / sy) ** 2
                if not np.isfinite(scores).any():
                    continue
                j = int(coarse[np.nanargmin(scores)])
                indices = np.arange(max(0, j - stride), min(len(x), j + stride + 1))
            if not len(indices):
                continue
            scores = ((x[indices] - pt.x()) / sx) ** 2 + ((y[indices] - pt.y()) / sy) ** 2
            if not np.isfinite(scores).any():
                continue
            k = int(indices[np.nanargmin(scores)])
            distance = float(np.nanmin(scores))
            if best is None or distance < best[0]:
                best = distance, i, k, float(x[k]), float(y[k])
        if best is None:
            return
        _, i, k, x0, y0 = best
        display_y = (view.mapSceneToView(self.right_view.mapViewToScene(pg.Point(x0, y0))).y()
                     if self.curves[i].get('right_axis') else y0)
        self.vline.setPos(x0)
        self.hline.setPos(display_y)
        self.marker.setData([x0], [display_y])
        for item in (self.vline, self.hline, self.marker):
            item.show()
        curve = self.curves[i]
        kind = '실측점' if curve.get('point_kind') == 'measured' else '계산점'
        self.readout.setText(f"{curve.get('name', '')}  ·  {kind}  ·  X {x0:.8g}  ·  Y {y0:.8g}  ·  점 {k + 1:,}")

    def graph_state(self):
        selected = [self.curves[self._legend_groups[self.list.row(row)][0]].get('name')
                    for row in self.list.selectedItems()]
        return {'range': self.plot.viewRange(), 'hidden': [c.get('name') for i, c in enumerate(self.curves)
                if self.list.item(self._curve_to_legend[i]).checkState() != Qt.CheckState.Checked],
                'selected': selected}

    def restore_graph_state(self, state):
        ranges = state.get('range')
        if ranges and len(ranges) == 2:
            self.plot.setRange(xRange=ranges[0], yRange=ranges[1], padding=0)
        hidden = set(state.get('hidden', []))
        selected = state.get('selected')
        emphasized = set(selected if isinstance(selected, list) else [selected])
        for row, group in enumerate(self._legend_groups):
            names = {self.curves[i].get('name') for i in group}
            if names <= hidden:
                self.list.item(row).setCheckState(Qt.CheckState.Unchecked)
            self.list.item(row).setSelected(bool(emphasized.intersection(names)))

    def export_png(self):
        from .project import default_output_dir
        path, _ = QFileDialog.getSaveFileName(self, '그래프 저장', str(Path(default_output_dir()) / 'graph.png'), 'PNG (*.png)')
        if path:
            try:
                pg.exporters.ImageExporter(self.plot.plotItem).export(path)
            except Exception as exc:
                QMessageBox.warning(self, '저장 실패', str(exc))

    def export_csv(self):
        from .project import default_output_dir
        path, _ = QFileDialog.getSaveFileName(self, '표시 중인 곡선 저장', str(Path(default_output_dir()) / 'curves.csv'), 'CSV (*.csv)')
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
                writer = csv.writer(handle)
                writer.writerow(['Source_ID', 'Curve', 'Cycle', 'Step', 'Direction', 'PointKind', 'X', 'Y', 'X_Axis', 'Y_Axis', 'Settings_JSON'])
                for i, c in enumerate(self.curves):
                    if not self.items[i].isVisible():
                        continue
                    x_label = self.plot.getAxis('bottom').labelText
                    y_label = self.right_label if c.get('right_axis') else self.plot.getAxis('left').labelText
                    for x, y in zip(c['x'], c['y']):
                        writer.writerow([c.get('source_id', ''), c.get('name', ''), c.get('cycle', ''),
                                         c.get('step', ''), c.get('direction', ''), c.get('point_kind', 'calculated'), x, y, x_label, y_label,
                                         json.dumps(c.get('settings', {}), ensure_ascii=False, default=str)])
        except Exception as exc:
            QMessageBox.warning(self, '저장 실패', str(exc))
