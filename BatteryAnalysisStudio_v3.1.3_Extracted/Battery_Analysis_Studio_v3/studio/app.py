"""Offline Qt analysis workspace sharing immutable channel data between tools."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import traceback
import zlib

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSplitter, QStackedWidget, QListWidget, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox, QFormLayout,
    QDockWidget, QFileDialog, QMessageBox, QProgressBar, QTabWidget, QGroupBox,
    QInputDialog, QDialog, QDialogButtonBox, QScrollArea,
)

from . import APP_VERSION
from .widgets import CurvePlot, DataTable, PALETTE, cycle_color, display_value
from .ingestion import load_inputs
from .analysis import apply_cycle_baseline, prepare_cycle_tables, raw_curves, track_peaks
from .derivative_cache import cached_derivatives
from .project import save_project, load_project, project_channels, default_output_dir
from .compact_data import CompactStore, prepare_channel
from .import_dialog import classify_channels
from .dcr_panel import DCRPanel
from .raw_store import PREVIEW_POINTS
from .study_ui import StudyWorkspace
from ui.widgets import parse_cycle_spec


METRICS = {
    '방전용량 유지율 (%)': 'Discharge_Capacity_Retention_pct',
    '충전용량 (mAh)': 'Charge_Capacity_mAh', '방전용량 (mAh)': 'Discharge_Capacity_mAh',
    '방전에너지 (mWh)': 'Discharge_Energy_mWh', '에너지 유지율 (%)': 'Energy_Retention_pct',
    'CE (%)': 'Coulombic_Efficiency_pct', 'EE (%)': 'Energy_Efficiency_pct',
    '평균 온도 (°C)': 'Avg_Temp_C', '최대 온도 (°C)': 'Max_Temp_C',
    'CC 용량 (mAh)': 'CC_Capacity_mAh', 'CV 용량 (mAh)': 'CV_Capacity_mAh',
    'CC 시간 (s)': 'CC_Time_s', 'CV 시간 (s)': 'CV_Time_s',
    '방전 시작 저항 (mΩ)': 'Discharge_Onset_R_mOhm',
}
AXES = {'Step 시간 (s)': 'StepTime_s', '누적 시간 (s)': 'TotalTime_s',
        '용량 (mAh)': 'Capacity_mAh', '전압 (V)': 'Voltage_V',
        '전류 (mA)': 'Current_mA', '에너지 (mWh)': 'Energy_mWh', '온도 (°C)': 'Temp_C'}


class Task(QObject):
    finished = Signal(object)
    failed = Signal(object)
    progress = Signal(int, int, str)
    stopped = Signal()

    def __init__(self, fn, key, token):
        super().__init__()
        self.fn = fn
        self.key, self.token = key, token
        self.cancel = threading.Event()

    @Slot()
    def run(self):
        try:
            result = self.fn(self.progress.emit, self.cancel.is_set)
            if self.cancel.is_set():
                raise InterruptedError('작업을 취소했습니다.')
            self.finished.emit((self.key, self.token, result))
        except Exception as exc:
            self.failed.emit((self.key, self.token, str(exc)))
        finally:
            self.stopped.emit()


class AnalysisPage(QWidget):
    def __init__(self, title, description, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        label = QLabel(title)
        label.setStyleSheet('font-size:21px;font-weight:600;color:#edf5f6')
        heading.addWidget(label)
        heading.addStretch()
        self.table_toggle = QPushButton('상세 표 접기')
        self.table_toggle.setCheckable(True)
        heading.addWidget(self.table_toggle)
        layout.addLayout(heading)
        text = QLabel(description)
        text.setWordWrap(True)
        text.setStyleSheet('color:#91a8b5')
        layout.addWidget(text)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.graph = CurvePlot()
        self.table = DataTable()
        self.splitter.addWidget(self.graph)
        self.splitter.addWidget(self.table)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([550, 170])
        self.table_toggle.toggled.connect(lambda checked: (self.table.setVisible(not checked),
            self.table_toggle.setText('상세 표 펼치기' if checked else '상세 표 접기')))
        self.table.rowSelected.connect(lambda row: self.graph.highlight_cycle(row.get('Cycle')))
        layout.addWidget(self.splitter, 1)


class StudioWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'Battery Analysis Studio {APP_VERSION}')
        self.resize(1480, 940)
        self.setMinimumSize(1000, 640)
        self.channels = []
        self._jobs = {}
        self._queued_jobs = {}
        self._threads = set()
        self._workers = {}
        self._tokens = {}
        self._callbacks = {}
        self._summaries = {}
        self._summary_cache = {}
        self._summary_pending = None
        self._last_raw_result = None
        self._last_derivative_result = None
        self._derivatives = []
        self._pending_project = None
        self._project_path = None
        self._last_input_paths = []
        self._last_analysis_page = 0
        self._raw_table_sources = {}
        self._restoring = False
        self._dirty_pages = set()
        self._pages = {}
        self.study_workspace = StudyWorkspace(self)
        self.setAcceptDrops(True)
        self._build_menu()
        self._build_workspace()
        self._build_settings()
        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.timeout.connect(self.refresh_current)
        self.nav.setCurrentRow(0)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu('파일')
        for title, shortcut, callback in [
            ('시험 목록…', '', self.study_workspace.choose),
            ('데이터 추가…', 'Ctrl+O', self.add_files), ('폴더 추가…', '', self.add_folder),
            ('CSV 컬럼·단위 지정하여 열기…', '', self.add_mapped_csv),
            ('프로젝트 열기…', 'Ctrl+Shift+O', self.open_project),
            ('프로젝트 저장…', 'Ctrl+S', self.save_current_project),
            ('분석 프리셋 저장…', '', self.save_preset), ('분석 프리셋 불러오기…', '', self.open_preset),
            ('RPT 결과 보기…', '', self.show_rpt_results),
            ('분석 Excel 저장…', 'Ctrl+E', self.export_excel),
        ]:
            action = QAction(title, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            file_menu.addAction(action)
        help_menu = self.menuBar().addMenu('도움말')
        rules = QAction('계산 및 검증 안내', self)
        rules.triggered.connect(lambda: QMessageBox.information(self, '계산과 검증',
            'PNE CYC는 72B 헤더 / 176B 레코드 가설을 검증하며 읽습니다.\n'
            '기본 정량분석: MATCHED 및 CSV 구간. 검토 구간 포함은 별도 선택입니다.\n'
            '미분은 선택된 물리 Step 기준, DCR은 Qt 계산 규칙을 사용합니다.\n'
            '장비 저항 필드와 Raw 재계산 DCR은 서로 다른 지표입니다.\n'
            '합성 데이터 검증과 실제 PNE 장비 데이터 검증은 구분합니다.\n'
            '이 프로그램은 오프라인으로 동작하고 외부로 데이터를 전송하지 않습니다.'))
        help_menu.addAction(rules)

    def _build_workspace(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 10, 12, 6)
        top = QHBoxLayout()
        brand = QLabel('BATTERY  /  ANALYSIS STUDIO')
        brand.setStyleSheet('font-size:16px;font-weight:700;color:#45d8c0;letter-spacing:1px')
        top.addWidget(brand)
        top.addStretch()
        self.study_button = QPushButton('시험 목록')
        self.study_button.clicked.connect(self.study_workspace.choose)
        top.addWidget(self.study_button)
        self.info = QLabel('ZIP·폴더·CSV를 추가해 시작하세요')
        top.addWidget(self.info)
        self.settings_toggle = QPushButton('분석 설정')
        self.settings_toggle.clicked.connect(lambda: self.settings_dock.setVisible(not self.settings_dock.isVisible()))
        top.addWidget(self.settings_toggle)
        outer.addLayout(top)
        self.main_splitter = QSplitter()
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 6, 0)
        buttons = QHBoxLayout()
        for text, callback in [('＋ 데이터', self.add_files), ('폴더', self.add_folder)]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            buttons.addWidget(b)
        side.addLayout(buttons)
        self.search = QLineEdit()
        self.search.setPlaceholderText('시험·채널 검색')
        self.search.textChanged.connect(self.filter_tree)
        side.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['시험 / 채널'])
        self.tree.setMinimumWidth(190)
        self.tree.itemChanged.connect(self._selection_changed)
        self.tree.currentItemChanged.connect(self._active_changed)
        side.addWidget(self.tree, 1)
        checks = QHBoxLayout()
        for label, checked in [('전체', True), ('해제', False)]:
            b = QPushButton(label)
            b.clicked.connect(lambda _, state=checked: self.check_all(state))
            checks.addWidget(b)
        side.addLayout(checks)
        self.merge_button = QPushButton('같은 셀의 분할시험 연결…')
        self.merge_button.clicked.connect(self.merge_channels)
        side.addWidget(self.merge_button)
        self.study_details_button = QPushButton('시험 구간 · 이력')
        self.study_details_button.clicked.connect(self.study_workspace.details)
        self.study_details_button.hide()
        side.addWidget(self.study_details_button)
        self.nav = QListWidget()
        self.nav.addItems(['Summary', 'Cycle Life', 'Raw Curve', 'dQ/dV · dV/dQ',
                           'DCR / SOC', 'Channel / Cell Compare', 'Export'])
        self.nav.setMaximumHeight(245)
        self.nav.currentRowChanged.connect(self.change_page)
        side.addWidget(self.nav)
        self.main_splitter.addWidget(sidebar)
        self.stack = QStackedWidget()
        self.summary = AnalysisPage('Summary', '선택 채널의 수명 추이와 마지막 측정값입니다. 카드는 현재 활성 채널 기준입니다.')
        cardrow = QHBoxLayout()
        self.cards = []
        for label in ['방전용량', '용량 유지율', '최대 온도', '기준 Cycle']:
            card = QLabel(label + '\n—')
            card.setStyleSheet('background:#182a32;border:1px solid #29414a;border-radius:7px;padding:12px;font-size:15px')
            card.setMinimumHeight(62)
            self.cards.append(card)
            cardrow.addWidget(card)
        self.summary.layout().insertLayout(2, cardrow)
        self.life = AnalysisPage('Cycle Life', '기준 Cycle과 지표는 독립적으로 유지됩니다. 표의 Cycle을 선택하면 곡선을 강조합니다.')
        self.raw = AnalysisPage('Raw Curve', '범례에서 Cycle을 클릭해 강조하거나 체크를 해제해 숨길 수 있습니다.')
        self.raw_tables = QTabWidget()
        self.raw.splitter.replaceWidget(1, self.raw_tables)
        self.raw_tables.addTab(self.raw.table, '표시 곡선')
        self.match_table, self.source_table = DataTable(), DataTable()
        self.raw_tables.addTab(self.match_table, 'Step 매칭')
        raw_detail = QWidget()
        raw_detail_layout = QVBoxLayout(raw_detail)
        raw_detail_layout.setContentsMargins(0, 0, 0, 0)
        pages = QHBoxLayout()
        pages.addWidget(QLabel('곡선 데이터 페이지'))
        self.raw_detail_page = QSpinBox()
        self.raw_detail_page.setRange(1, 1)
        pages.addWidget(self.raw_detail_page)
        pages.addStretch()
        raw_detail_layout.addLayout(pages)
        raw_detail_layout.addWidget(self.source_table)
        self.raw_tables.addTab(raw_detail, '곡선 데이터')
        self.raw_detail_page.valueChanged.connect(self._load_raw_detail)
        self.raw.table_toggle.toggled.disconnect()
        self.raw.table_toggle.toggled.connect(self._toggle_raw_details)
        self.raw_tables.currentChanged.connect(self._load_raw_detail)
        self.raw.table_toggle.setChecked(True)
        self.compare = AnalysisPage('Channel / Cell Compare', '여러 시험·셀은 개별 곡선으로 비교합니다. 선택만으로 서로 이어붙이지 않습니다.')
        self.derivative = QWidget()
        dq_layout = QVBoxLayout(self.derivative)
        title = QLabel('dQ/dV · dV/dQ')
        title.setStyleSheet('font-size:21px;font-weight:600')
        dq_layout.addWidget(title)
        self.dq_note = QLabel('오른쪽 설정에서 Cycle·방향·보간·스무딩을 선택한 뒤 분석하세요.')
        self.dq_note.setWordWrap(True)
        dq_layout.addWidget(self.dq_note)
        self.dq_tabs = QTabWidget()
        self.dq_plot = CurvePlot('전압 (V)', 'dQ/dV (mAh/V)')
        self.vq_plot = CurvePlot('용량 (mAh)', '전압 (V)')
        self.dvdq_plot = CurvePlot('용량 (mAh)', 'dV/dQ (V/mAh)')
        for graph, graph_name in ((self.raw.graph, 'Raw Curve'), (self.dq_plot, 'dQ/dV'),
                                  (self.vq_plot, 'V–Q'), (self.dvdq_plot, 'dV/dQ')):
            graph.origin_btn.show()
            graph.origin_btn.clicked.connect(
                lambda _, selected_graph=graph, title=graph_name: self.export_origin(selected_graph, title))
        self.peak_table = DataTable()
        self.peak_trend = CurvePlot('Cycle', '피크 전압 (V)')
        for text, widget in [('dQ/dV', self.dq_plot), ('V–Q', self.vq_plot), ('dV/dQ', self.dvdq_plot),
                             ('피크 결과', self.peak_table), ('피크 추이', self.peak_trend)]:
            self.dq_tabs.addTab(widget, text)
        dq_layout.addWidget(self.dq_tabs, 1)
        self.dcr = DCRPanel()
        self.dcr.schedule = lambda work, ready: self._start('dcr', work, ready)
        self.dcr.cancel_scheduled = lambda: self._cancel_task('dcr')
        self.dcr.statusMessage.connect(self.statusBar().showMessage)
        self.export_page = self._build_export_page()
        self.pages = [self.summary, self.life, self.raw, self.derivative, self.dcr, self.compare, self.export_page]
        for page in self.pages:
            self.stack.addWidget(page)
        self.main_splitter.addWidget(self.stack)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([245, 1050])
        outer.addWidget(self.main_splitter, 1)
        self.notice = QLabel('원본 읽기 전용 · 오프라인 분석 · 검토 필요 구간은 기본 정량분석에서 제외')
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet('color:#91a8b5;padding:4px')
        outer.addWidget(self.notice)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(210)
        self.progress.hide()
        self.cancel_button = QPushButton('작업 취소')
        self.cancel_button.clicked.connect(self.cancel_jobs)
        self.cancel_button.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_button)
        self.setCentralWidget(central)

    def _build_export_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel('Export\n선택한 채널과 현재 계산 조건을 함께 저장합니다.')
        label.setStyleSheet('font-size:20px')
        layout.addWidget(label)
        for title, fn in [('전체 선택 채널 분석 Excel', self.export_excel),
                          ('현재 화면 표 CSV', self.export_current_table),
                          ('선택 채널 Raw CSV (요청 시에만 생성)', self.export_raw),
                          ('프로젝트 저장 · 선택과 수동 수정 포함', self.save_current_project),
                          ('다른 시험에 쓸 분석 프리셋 저장', self.save_preset)]:
            button = QPushButton(title)
            button.setMinimumHeight(44)
            button.clicked.connect(fn)
            layout.addWidget(button)
        note = QLabel('그래프 PNG와 곡선 CSV는 각 그래프 위의 저장 버튼을 사용하세요.\n'
                      'Excel에는 계산 설정·원본 정보·입력 상태·선택 구간을 함께 기록합니다.\n'
                      'Raw CSV는 자동 생성하지 않습니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return page

    def _build_settings(self):
        self.settings_dock = QDockWidget('분석 설정', self)
        self.settings_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.settings_dock.setMinimumWidth(240)
        host = QWidget()
        layout = QVBoxLayout(host)
        common = QGroupBox('공통')
        form = QFormLayout(common)
        self._common_form = form
        self.include_review = QCheckBox('검토 필요 구간도 포함')
        self.include_review.toggled.connect(self._review_changed)
        form.addRow(self.include_review)
        self.baseline = QLineEdit()
        self.baseline.setPlaceholderText('자동 · 2부터 첫 유효 Cycle')
        self.baseline.returnPressed.connect(self.refresh_current)
        self.baseline.textChanged.connect(self._summary_dirty)
        form.addRow('유지율 기준 Cycle', self.baseline)
        self.aggregate = QComboBox()
        self.aggregate.addItem('대표값 (기존 Qt 기준)', 'representative')
        self.aggregate.addItem('물리 Step 용량 합계', 'step_sum')
        self.aggregate.currentIndexChanged.connect(self._summary_dirty)
        form.addRow('집계 방식', self.aggregate)
        self.color_mode = QComboBox()
        for label, key in [('자동 (다중 셀은 채널 색)', 'auto'), ('채널 색상', 'channel'), ('Cycle 그라데이션', 'cycle')]:
            self.color_mode.addItem(label, key)
        self.color_mode.currentIndexChanged.connect(self._recolor_graphs)
        form.addRow('곡선 색상 기준', self.color_mode)
        layout.addWidget(common)
        self.settings_stack = QStackedWidget()
        self.metric_widgets = {}
        for index in [0, 1, 5]:
            widget = QWidget()
            form = QFormLayout(widget)
            combo = QComboBox()
            for label, field in METRICS.items():
                combo.addItem(label, field)
            combo.currentIndexChanged.connect(self.render_summaries)
            self.metric_widgets[index] = combo
            form.addRow('表示 지표'.replace('表示', '분석'), combo)
            if index == 0:
                self.summary_start = QSpinBox()
                self.summary_start.setRange(0, 10000000)
                self.summary_start.setValue(2)
                self.summary_start.valueChanged.connect(self.render_summaries)
                form.addRow('표시 시작 Cycle', self.summary_start)
            self._pages[index] = self.settings_stack.addWidget(widget)
        raw_settings = QWidget()
        form = QFormLayout(raw_settings)
        self.raw_cycles = QLineEdit('1')
        self.raw_cycles.setPlaceholderText('1,5,10-50:5')
        self.raw_direction = QComboBox()
        for text, key in [('전체', 'ALL'), ('충전', 'CHARGE'), ('방전', 'DISCHARGE'), ('Rest', 'REST')]:
            self.raw_direction.addItem(text, key)
        self.raw_step = QLineEdit()
        self.raw_step.setPlaceholderText('전체 Step')
        self.raw_x, self.raw_y = QComboBox(), QComboBox()
        for label, key in AXES.items():
            self.raw_x.addItem(label, key)
            self.raw_y.addItem(label, key)
        self.raw_y.setCurrentIndex(3)
        for label, widget in [('Cycle', self.raw_cycles), ('방향', self.raw_direction), ('StepNo', self.raw_step),
                              ('X축', self.raw_x), ('Y축', self.raw_y)]:
            form.addRow(label, widget)
        self._pages[2] = self.settings_stack.addWidget(raw_settings)
        dq_settings = QWidget()
        form = QFormLayout(dq_settings)
        self.dq_cycles = QLineEdit('1')
        self.dq_cycles.setPlaceholderText('1,5,10-50:5')
        self.dq_direction = QComboBox()
        for label, key in [('충전+방전', 'both'), ('충전', 'charge'), ('방전', 'discharge')]:
            self.dq_direction.addItem(label, key)
        self.dq_step = QLineEdit()
        self.dq_step.setPlaceholderText('자동: 가장 긴 유효 Step')
        self.dq_points = QSpinBox()
        self.dq_points.setRange(0, 20000)
        self.dq_points.setSpecialValueText('자동 (약 1 mV)')
        self.dq_points.setValue(0)
        self.dq_smooth = QCheckBox('Savitzky–Golay 사용')
        self.dq_smooth.setChecked(True)
        self.dq_window = QSpinBox()
        self.dq_window.setRange(0, 19999)
        self.dq_window.setSingleStep(2)
        self.dq_window.setSpecialValueText('자동')
        self.dq_order = QSpinBox()
        self.dq_order.setRange(1, 5)
        self.dq_order.setValue(3)
        self.dq_peak = QDoubleSpinBox()
        self.dq_peak.setRange(.01, 100)
        self.dq_peak.setValue(5)
        self.dq_peak.setSuffix(' %')
        self.dq_peak_distance = QDoubleSpinBox()
        self.dq_peak_distance.setRange(0, 1000)
        self.dq_peak_distance.setSpecialValueText('기본 5보간점')
        self.dq_peak_distance.setSuffix(' mV')
        self.dq_track_shift = QDoubleSpinBox()
        self.dq_track_shift.setRange(.1, 500)
        self.dq_track_shift.setValue(50)
        self.dq_track_shift.setSuffix(' mV')
        for label, widget in [('Cycle', self.dq_cycles), ('방향', self.dq_direction), ('StepNo', self.dq_step),
                              ('보간점', self.dq_points), ('스무딩', self.dq_smooth), ('윈도 (홀수)', self.dq_window),
                              ('다항식 차수', self.dq_order), ('피크 돌출도', self.dq_peak),
                              ('피크 최소 간격', self.dq_peak_distance), ('피크 추적 허용 이동', self.dq_track_shift)]:
            form.addRow(label, widget)
        restore = QPushButton('미분 기본값 복원')
        restore.clicked.connect(self.reset_derivative_settings)
        form.addRow(restore)
        self._pages[3] = self.settings_stack.addWidget(dq_settings)
        blank = QWidget()
        blank_layout = QVBoxLayout(blank)
        blank_layout.addWidget(QLabel('이 분석의 세부 설정은 화면 안에서 조절합니다.'))
        self._pages[4] = self._pages[6] = self.settings_stack.addWidget(blank)
        layout.addWidget(self.settings_stack)
        apply_button = QPushButton('현재 조건으로 분석')
        apply_button.setMinimumHeight(40)
        apply_button.clicked.connect(self.refresh_current)
        layout.addWidget(apply_button)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        self.settings_dock.setWidget(scroll)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.settings_dock)
        for widget in [self.dq_cycles, self.dq_step, self.raw_cycles, self.raw_step]:
            widget.textChanged.connect(self.mark_dirty)
        for widget in [self.dq_points, self.dq_window, self.dq_order, self.dq_peak, self.dq_peak_distance, self.dq_track_shift]:
            widget.valueChanged.connect(self.mark_dirty)
        self.dq_smooth.toggled.connect(self.mark_dirty)
        self.dq_direction.currentIndexChanged.connect(self.mark_dirty)
        for widget in [self.raw_x, self.raw_y, self.raw_direction]:
            widget.currentIndexChanged.connect(self.mark_dirty)

    def selected_channels(self):
        ids = {self.tree.topLevelItem(i).child(j).data(0, Qt.ItemDataRole.UserRole)
               for i in range(self.tree.topLevelItemCount())
               for j in range(self.tree.topLevelItem(i).childCount())
               if self.tree.topLevelItem(i).child(j).checkState(0) == Qt.CheckState.Checked}
        return [c for c in self.channels if c.id in ids]

    def active_channel(self):
        item = self.tree.currentItem()
        id_ = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return next((c for c in self.channels if c.id == id_), None) or next(iter(self.selected_channels()), None)

    def _selection_changed(self, *_):
        if not self._restoring:
            self._invalidate_analysis()
            self._selection_timer.start(250)

    def _active_changed(self, *_):
        channel = self.active_channel()
        if channel:
            self.dcr.set_active_channel(channel.id, auto_detect=self.nav.currentRow() == 4 and not self._restoring)
            self.update_cards()
            if self.nav.currentRow() == 2 and not self._restoring:
                self._show_raw_tables(channel)

    def _show_raw_tables(self, channel):
        if self.raw.table_toggle.isChecked():
            return
        tab = self.raw_tables.currentIndex()
        if tab not in (1, 2):
            return
        page_size = 10000
        page = 1
        if tab == 2:
            previous = self.raw_detail_page.blockSignals(True)
            if self._raw_table_sources.get(2, ())[:2] != (channel.id, channel.fingerprint):
                self.raw_detail_page.setValue(1)
            self.raw_detail_page.setMaximum(max(1, (channel.row_count + page_size - 1) // page_size))
            page = self.raw_detail_page.value()
            self.raw_detail_page.blockSignals(previous)
        key = (channel.id, channel.fingerprint, page)
        if self._raw_table_sources.get(tab) == key:
            return
        if tab == 1:
            table, frame = self.match_table, channel.steps
        else:
            start = (page - 1) * page_size
            table = self.source_table
            frame = channel.read_rows([slice(start, min(start + page_size, channel.row_count))])
        table.set_frame(frame)
        self._raw_table_sources[tab] = key

    def _toggle_raw_details(self, collapsed):
        self.raw_tables.setVisible(not collapsed)
        self.raw.table_toggle.setText('상세 데이터 보기' if collapsed else '상세 데이터 접기')
        if not collapsed:
            self._load_raw_detail()

    def _load_raw_detail(self, *_):
        channel = self.active_channel()
        if channel:
            self._show_raw_tables(channel)

    def check_all(self, checked):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            for j in range(self.tree.topLevelItem(i).childCount()):
                self.tree.topLevelItem(i).child(j).setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self.refresh_current()

    def filter_tree(self, text):
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            visible = False
            for j in range(parent.childCount()):
                child = parent.child(j)
                match = text.casefold() in (parent.text(0) + child.text(0)).casefold()
                child.setHidden(not match)
                visible |= match
            parent.setHidden(not visible)

    def _populate_tree(self):
        selected = {c.id for c in self.selected_channels()}
        previous = {self.tree.topLevelItem(i).child(j).data(0, Qt.ItemDataRole.UserRole)
                    for i in range(self.tree.topLevelItemCount())
                    for j in range(self.tree.topLevelItem(i).childCount())}
        self.tree.blockSignals(True)
        self.tree.clear()
        groups = {}
        for c in self.channels:
            source = str(c.source_path)
            if source not in groups:
                parent = QTreeWidgetItem([Path(source).name])
                parent.setToolTip(0, source)
                self.tree.addTopLevelItem(parent)
                parent.setExpanded(True)
                groups[source] = parent
            kind = c.metadata.get("import_kind", "cycle")
            title = f"{c.name} · {'RPT' if kind == 'rpt' else '기타'}" if kind != "cycle" else c.name
            child = QTreeWidgetItem([title])
            child.setData(0, Qt.ItemDataRole.UserRole, c.id)
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            counts = c.metadata.get('status_counts', {})
            analyzable = any(int(counts.get(status, 0) or 0) > 0 for status in ('MATCHED', 'CSV'))
            should_check = c.id in selected or (c.id not in previous and analyzable)
            child.setCheckState(0, Qt.CheckState.Checked if should_check else Qt.CheckState.Unchecked)
            availability = '' if analyzable else '정량분석 가능한 연결 데이터 없음\n'
            child.setToolTip(0, f'{c.row_count:,} records\n{availability}' + '\n'.join(c.warnings))
            groups[source].addChild(child)
        self.tree.blockSignals(False)
        self.dcr.set_channels(self.channels)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0).child(0))
        self.info.setText(f'{len(groups)}개 입력 · {len(self.channels)}개 채널 · {sum(c.row_count for c in self.channels):,}점')

    def change_page(self, row):
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        if row < 6:
            self._last_analysis_page = row
        if hasattr(self, 'settings_stack'):
            self.settings_stack.setCurrentIndex(self._pages[row])
            for widget in (self.baseline, self.aggregate):
                self._common_form.setRowVisible(widget, row in (0, 1, 5))
        if hasattr(self, '_selection_timer') and not self._restoring:
            if row != 4 and self.dcr._busy:
                self.dcr.cancel()
            self.refresh_current()

    def _review_changed(self):
        self._summaries.clear()
        self.dcr.set_include_review(self.include_review.isChecked(),
                                    auto_detect=self.nav.currentRow() == 4 and not self._restoring)
        self.mark_dirty()
        if not self._restoring:
            self.refresh_current()

    def _summary_dirty(self):
        if self._restoring:
            return
        self._dirty_pages.update([0, 1, 5])
        for page in (self.summary, self.life, self.compare):
            page.graph.set_stale(True)

    def _invalidate_analysis(self):
        self._cancel_task('analysis')

    def _cancel_task(self, key):
        self._queued_jobs.pop(key, None)
        if key in self._jobs:
            self._jobs[key][1].cancel.set()
        self._tokens[key] = self._tokens.get(key, 0) + 1

    def mark_dirty(self, *_):
        if self._restoring:
            return
        self._invalidate_analysis()
        row = self.nav.currentRow()
        self._dirty_pages.add(row)
        if row == 3:
            for graph in [self.dq_plot, self.vq_plot, self.dvdq_plot, self.peak_trend]:
                graph.set_stale(True)
        elif row == 2:
            self.raw.graph.set_stale(True)

    def reset_derivative_settings(self):
        self.dq_points.setValue(0)
        self.dq_smooth.setChecked(True)
        self.dq_window.setValue(0)
        self.dq_order.setValue(3)
        self.dq_peak.setValue(5.)
        self.dq_peak_distance.setValue(0)
        self.dq_track_shift.setValue(50)

    def _start(self, key, fn, callback):
        self._cancel_task(key)
        token = self._tokens[key]
        if self._threads:
            self._queued_jobs[key] = (token, fn, callback)
            return
        self._launch_task(key, token, fn, callback)

    def _launch_task(self, key, token, fn, callback):
        worker = Task(fn, key, token)
        thread = QThread(self)
        worker.moveToThread(thread)
        self._threads.add(thread)
        self._workers[thread] = worker
        self._jobs[key] = (thread, worker)
        thread.setProperty('task_key', key)
        thread.setProperty('task_token', token)
        self._callbacks[(key, token)] = callback
        worker.finished.connect(self._job_complete, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._job_failed, Qt.ConnectionType.QueuedConnection)
        worker.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        worker.stopped.connect(thread.quit)
        worker.stopped.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self.progress.show()
        self.progress.setRange(0, 0)
        self.cancel_button.show()
        thread.start()

    @Slot(object)
    def _job_complete(self, payload):
        key, token, result = payload
        callback = self._callbacks.pop((key, token), None)
        if callback is not None and self._tokens.get(key) == token:
            try:
                callback(result)
            except Exception as exc:
                self.show_error(str(exc))

    @Slot(object)
    def _job_failed(self, payload):
        key, token, message = payload
        self._callbacks.pop((key, token), None)
        if self._tokens.get(key) == token:
            self.show_error(message)

    @Slot()
    def _thread_finished(self):
        thread = self.sender()
        key, token = thread.property('task_key'), thread.property('task_token')
        self._threads.discard(thread)
        self._workers.pop(thread, None)
        if self._jobs.get(key, (None,))[0] is thread:
            self._jobs.pop(key, None)
        self._callbacks.pop((key, token), None)
        if self._queued_jobs:
            next_key = next(iter(self._queued_jobs))
            next_token, fn, callback = self._queued_jobs.pop(next_key)
            self._launch_task(next_key, next_token, fn, callback)
        else:
            self.progress.hide()
            self.cancel_button.hide()

    @Slot(int, int, str)
    def _progress(self, done, total, message):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.statusBar().showMessage(message)

    def cancel_jobs(self):
        self._queued_jobs.clear()
        for _, worker in self._jobs.values():
            worker.cancel.set()
        if self.dcr._busy:
            self.dcr.cancel()
        self.statusBar().showMessage('취소 요청 중… 현재 데이터 구간 처리 후 종료합니다.')

    def show_error(self, text):
        self.notice.setText(text)
        self.notice.setStyleSheet('color:#f2a950;padding:4px')
        self.statusBar().showMessage(text)

    def add_files(self):
        if self.study_workspace.study:
            self.study_workspace.add()
            return
        paths, _ = QFileDialog.getOpenFileNames(self, 'PNE ZIP 또는 Raw CSV 추가', '',
            '배터리 데이터 (*.zip *.csv *.cyc);;모든 파일 (*)')
        if paths:
            self.import_paths(paths, configure=True)

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, '시험 또는 채널 폴더 선택')
        if path:
            if self.study_workspace.study:
                self.study_workspace.add([path], cycle_only=True)
            else:
                self.import_paths([path], configure=True)

    def add_mapped_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, '컬럼·단위를 지정할 CSV', '', 'CSV/TSV (*.csv *.tsv);;모든 파일 (*)')
        if not path:
            return
        from .csv_dialog import CSVImportDialog
        dialog = CSVImportDialog(path, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.study_workspace.study:
                self.study_workspace.add([path], cycle_only=True, csv_options=dialog.options())
            else:
                self.import_paths([path], csv_options=dialog.options(), configure=True)

    def show_study_channels(self, channels, state):
        self._invalidate_analysis()
        self._selection_timer.stop()
        self.channels = channels
        self._summaries.clear()
        self._summary_cache.clear()
        self._last_raw_result = self._last_derivative_result = None
        self._derivatives.clear()
        self._raw_table_sources.clear()
        self._dirty_pages.update([0, 1, 2, 3, 5])
        for graph in (self.raw.graph, self.dq_plot, self.vq_plot, self.dvdq_plot, self.peak_trend):
            graph.set_curves([])
        for table in (self.raw.table, self.match_table, self.source_table, self.peak_table):
            table.set_frame(pd.DataFrame())
        self._restoring = True
        try:
            self.tree.clear()
            self._populate_tree()
        finally:
            self._restoring = False
        self.merge_button.hide()
        self.study_details_button.show()
        warnings = [f'{c.name}: {w}' for c in channels for w in c.warnings]
        self.notice.setText(' · '.join(warnings) if warnings else
                            '시험을 열었습니다. ＋ 데이터에서 구간을 추가하거나 갱신하세요.')
        self.notice.setToolTip('')
        self.render_summaries()
        self.restore_state(state)

    def import_paths(self, paths, replace=False, csv_options=None, *, configure=False):
        self._selection_timer.stop()
        self._invalidate_analysis()
        if self.dcr._busy:
            self.dcr.cancel()
        self._last_input_paths = list(paths)
        def loaded(channels):
            if replace:
                self.channels = []
            by_id = {c.id: c for c in self.channels}
            for channel in channels:
                by_id[channel.id] = channel
            self.channels = list(by_id.values())
            sources = {(c.id, c.fingerprint) for c in self.channels}
            for tab, table in ((1, self.match_table), (2, self.source_table)):
                if tab in self._raw_table_sources and self._raw_table_sources[tab][:2] not in sources:
                    table.set_frame(pd.DataFrame())
                    self._raw_table_sources.pop(tab)
            self._summary_cache = {key: value for key, value in self._summary_cache.items()
                                   if key[:2] in sources}
            if self._pending_project:
                self._restore_merged_channels(self._pending_project)
            self._summaries.clear()
            self._last_raw_result = None
            self._last_derivative_result = None
            self._restoring = True
            try:
                self._populate_tree()
            finally:
                self._restoring = False
            warnings = [f'{c.name}: {w}' for c in channels for w in c.warnings]
            self.notice.setText(f'입력 완료 · 가져오기 안내 {len(warnings):,}건 (마우스를 올려 보기)'
                                if warnings else '입력 완료')
            self.notice.setToolTip('\n'.join(warnings))
            active = self.active_channel()
            if active and active.cycles:
                self.raw_cycles.setText(str(active.cycles[0]))
                self.dq_cycles.setText(str(active.cycles[0]))
            if self._pending_project:
                project = self._pending_project
                self._pending_project = None
                self.restore_state(project.get('ui_state', {}))
                expected = {s.get('id'): s for s in project.get('sources', [])}
                if expected and set(expected) != {c.id for c in self.channels}:
                    self.show_error('원본이 변경되었거나 채널 구성이 달라졌습니다. 결과와 수동 설정을 확인하세요.')
            elif self.nav.currentRow() != 0:
                self.nav.setCurrentRow(0)
            else:
                self.refresh_current()
        import tempfile
        cache_dir = str(Path(tempfile.gettempdir()) / 'BatteryAnalysisStudio3' / 'cache')
        settings = self.derivative_settings()
        settings.pop('peak_track_shift_mv')

        def load(progress, cancel, configured=None):
            def prepare(channel):
                progress(0, 1, f'곡선·요약·DCR 준비 · {channel.name}')
                return prepare_channel(channel, cache_dir, cancel)
            channels = (load_inputs(paths, progress=progress, cancel=cancel,
                                    csv_options=csv_options, consume=prepare) if configured is None
                        else [prepare(channel) for channel in configured])
            if channels and channels[0].cycles:
                first = channels[0]
                progress(0, 1, f'기본 미분 곡선 준비 · {first.name}')
                try:
                    cached_derivatives(first, [first.cycles[0]], settings, cancel)
                except ValueError:
                    # Import remains available when this selection has no usable curve.
                    pass
            return channels

        if configure:
            def rules_ready(channels):
                configured = classify_channels(self, channels, paths=paths)
                if configured is not None:
                    self._start('load', lambda progress, cancel: load(progress, cancel, configured), loaded)
            self._start('load', lambda progress, cancel: load_inputs(
                paths, progress=progress, cancel=cancel, csv_options=csv_options), rules_ready)
        else:
            self._start('load', load, loaded)

    def refresh_current(self):
        if not self.channels:
            return
        row = self.nav.currentRow()
        if row in (0, 1, 5):
            self.calculate_summaries()
        elif row == 2:
            channel = self.active_channel()
            if channel:
                self._show_raw_tables(channel)
            self.calculate_raw()
        elif row == 3:
            self.calculate_derivatives()
        elif row == 4:
            channel = self.active_channel()
            if channel:
                self.dcr.set_active_channel(channel.id)

    def _baseline_value(self):
        text = self.baseline.text().strip()
        if text and (not text.isdigit() or int(text) < 0):
            raise ValueError('기준 Cycle은 실제 존재하는 정수로 입력하세요.')
        return int(text) if text else None

    @staticmethod
    def _cycle_table_key(channel, review):
        counts = channel.metadata.get('status_counts')
        effective_review = bool(review and (counts is None or counts.get('REVIEW')))
        return channel.id, channel.fingerprint, channel.name, effective_review

    @staticmethod
    def _cycle_cache_dir():
        import tempfile
        return Path(tempfile.gettempdir()) / 'BatteryAnalysisStudio3' / 'cycle_tables'

    def calculate_summaries(self):
        try:
            baseline = self._baseline_value()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        channels = [c for c in self.selected_channels() if c.metadata.get("import_kind", "cycle") == "cycle"]
        review, aggregation = self.include_review.isChecked(), self.aggregate.currentData()
        keys = [(channel, self._cycle_table_key(channel, review)) for channel in channels]
        pending = [(channel, key) for channel, key in keys if key not in self._summary_cache]
        current = self._jobs.get('analysis')
        def display(tables, errors, active_baseline, active_aggregation):
            self._summaries = {}
            for channel, key in keys:
                if key not in tables:
                    continue
                try:
                    self._summaries[channel.id] = apply_cycle_baseline(
                        tables[key][active_aggregation], channel.name, active_baseline, first_cycle=2)
                except ValueError as exc:
                    errors.append(str(exc))
            self._dirty_pages.difference_update([0, 1, 5])
            self.render_summaries()
            if errors:
                self.show_error(' / '.join(errors))
            else:
                self.statusBar().showMessage('저장된 Cycle 집계로 분석 완료')
        if not pending:
            if current or 'analysis' in self._queued_jobs:
                self._invalidate_analysis()
            display(self._summary_cache, [], baseline, aggregation)
            return
        requested = tuple(key for _, key in pending)
        if (self._summary_pending == (self._tokens.get('analysis'), requested) and
                ('analysis' in self._queued_jobs or (current and not current[1].cancel.is_set()))):
            return
        cache_dir = self._cycle_cache_dir()
        def calculate(progress, cancel):
            results, errors = {}, []
            for i, (channel, key) in enumerate(pending):
                if cancel():
                    raise InterruptedError('분석 취소')
                progress(i, len(pending), f'Cycle 집계 준비 · {channel.name}')
                try:
                    results[key] = prepare_cycle_tables(channel, key[-1], cache_dir, cancel)
                except InterruptedError:
                    raise
                except Exception as exc:
                    errors.append(f'{channel.name}: {exc}')
            return results, errors
        def ready(result):
            tables, errors = result
            self._summary_cache.update(tables)
            display(self._summary_cache, errors, self._baseline_value(), self.aggregate.currentData())
        self._start('analysis', calculate, ready)
        self._summary_pending = (self._tokens['analysis'], requested)

    def render_summaries(self, *_):
        for index, page in [(0, self.summary), (1, self.life), (5, self.compare)]:
            if index not in self.metric_widgets:
                continue
            combo = self.metric_widgets[index]
            metric = combo.currentData()
            curves, frames = [], []
            for channel in self.selected_channels():
                frame = self._summaries.get(channel.id)
                if frame is None or frame.empty:
                    continue
                if index == 0:
                    frame = frame.loc[frame['Cycle'] >= self.summary_start.value()]
                    if frame.empty:
                        continue
                enriched = frame.copy()
                enriched.insert(0, 'Channel', channel.name)
                enriched['Source_ID'] = channel.id
                preferred = ['Channel', 'Cycle', 'OriginalCycle', 'Discharge_Capacity_mAh',
                    'Discharge_Capacity_Retention_pct', 'Discharge_Energy_mWh', 'Energy_Retention_pct',
                    'Coulombic_Efficiency_pct', 'Energy_Efficiency_pct', 'Max_Temp_C', 'Baseline_Cycle']
                ordered = [name for name in preferred if name in enriched]
                enriched = enriched[ordered + [col for col in enriched if col not in ordered]]
                frames.append(enriched)
                if metric in frame:
                    x = frame['Cycle'].to_numpy()
                    y = frame[metric].to_numpy(dtype=float)
                    if channel.metadata.get('input_type') == 'study_cell':
                        gaps = np.flatnonzero(np.diff(x) > 1) + 1
                        y = np.insert(y, gaps, np.nan)
                        x = np.insert(x, gaps, x[gaps] - 1)
                    curves.append(dict(x=x, y=y,
                        name=channel.name, source_id=channel.id, point_kind='calculated',
                        color=self._channel_color(channel.id)))
            page.graph.set_labels('Cycle', combo.currentText())
            page.graph.set_curves(curves)
            if index in self._dirty_pages:
                page.graph.set_stale(True)
            page.table.set_frame(pd.concat(frames, ignore_index=True) if frames else pd.DataFrame())
            self._restore_graph({0: 'summary', 1: 'life', 5: 'compare'}[index], page.graph)
        self.update_cards()

    def update_cards(self):
        if not hasattr(self, 'cards'):
            return
        channel = self.active_channel()
        summary = self._summaries.get(channel.id) if channel else None
        values = ['—'] * 4
        if summary is not None and not summary.empty:
            row = summary.iloc[-1]
            values = [display_value(row.get('Discharge_Capacity_mAh')) + ' mAh',
                      display_value(row.get('Discharge_Capacity_Retention_pct')) + ' %',
                      display_value(row.get('Max_Temp_C')) + ' °C',
                      display_value(row.get('Baseline_Cycle', summary.attrs.get('baseline_cycle')))]
        for card, title, value in zip(self.cards, ['방전용량', '용량 유지율', '최대 온도', '기준 Cycle'], values):
            card.setText(f'{title}\n{value}')

    def _channel_color(self, channel_id):
        ids = [c.id for c in self.channels]
        return PALETTE[(ids.index(channel_id) if channel_id in ids else 0) % len(PALETTE)]

    def _color_curves(self, curves, *, prefer_cycles=False):
        cycles = [int(c.get('cycle', 0)) for c in curves] or [0]
        channel_mode = self.color_mode.currentData() == 'channel' or (
            not prefer_cycles and self.color_mode.currentData() == 'auto'
            and len({c.get('source_id') for c in curves}) > 1)
        colors = {cycle: cycle_color(cycle) for cycle in set(cycles)}
        for curve in curves:
            curve['color'] = self._channel_color(curve.get('source_id')) if channel_mode else colors[int(curve.get('cycle', 0))]

    def _recolor_graphs(self):
        for graph in (self.raw.graph, self.dq_plot, self.vq_plot, self.dvdq_plot):
            self._color_curves(graph.curves, prefer_cycles=True)
            graph.update_colors()

    def _cycles(self, edit):
        cycles = parse_cycle_spec(edit.text())
        if not cycles:
            raise ValueError('Cycle을 입력하세요. 예: 1,5,10-50:5')
        if len(cycles) > 2000:
            raise ValueError('한 번에 2,000개 이하의 Cycle을 선택하세요.')
        return cycles

    def calculate_raw(self):
        try:
            cycles = self._cycles(self.raw_cycles)
        except ValueError as exc:
            self.show_error(str(exc))
            return
        channels = self.selected_channels()
        kwargs = dict(x=self.raw_x.currentData(), y=self.raw_y.currentData(),
                      direction=self.raw_direction.currentData(), step=self.raw_step.text().strip(),
                      include_review=self.include_review.isChecked())
        x_label, y_label = self.raw_x.currentText(), self.raw_y.currentText()
        key = (tuple((c.id, c.fingerprint) for c in channels), tuple(cycles), tuple(kwargs.items()))
        def calculate(progress, cancel):
            curves, errors = [], []
            for i, channel in enumerate(channels):
                if cancel():
                    raise InterruptedError('Raw 분석 취소')
                progress(i, len(channels), f'Raw 준비 · {channel.name}')
                try:
                    curves.extend(raw_curves(channel, cycles, display_budget=PREVIEW_POINTS, cancel=cancel, **kwargs))
                except InterruptedError:
                    raise
                except Exception as exc:
                    errors.append(f'{channel.name}: {exc}')
            return curves, errors
        def ready(result):
            self._last_raw_result = (key, result)
            curves, errors = result
            self._color_curves(curves, prefer_cycles=True)
            self._dirty_pages.discard(2)
            self.raw.graph.set_labels(x_label, y_label)
            self.raw.graph.set_curves(curves, group_cycles=True)
            self._restore_graph('raw', self.raw.graph)
            table = [dict(Channel=c.get('name'), Cycle=c.get('cycle'), Step=c.get('step'),
                          Direction=c.get('direction'), Points=len(c['x']), Source_ID=c.get('source_id')) for c in curves]
            self.raw.table.set_frame(pd.DataFrame(table))
            if errors:
                self.show_error(' / '.join(errors))
        if self._last_raw_result and self._last_raw_result[0] == key:
            self._invalidate_analysis()
            self._dirty_pages.discard(2)
            self.raw.graph.set_stale(False)
            self.statusBar().showMessage('표시한 Raw 데이터 재사용')
        else:
            self._start('analysis', calculate, ready)

    def derivative_settings(self):
        return dict(direction=self.dq_direction.currentData(), n_points=max(50, self.dq_points.value()) if self.dq_points.value() else 0,
            smooth=self.dq_smooth.isChecked(), smooth_window=self.dq_window.value() or None,
            smooth_polyorder=self.dq_order.value(), peak_prominence_pct=self.dq_peak.value() / 100.,
            peak_min_distance_mv=self.dq_peak_distance.value() or None,
            peak_track_shift_mv=self.dq_track_shift.value(),
            step=self.dq_step.text().strip(), include_review=self.include_review.isChecked())

    def calculate_derivatives(self):
        try:
            cycles = self._cycles(self.dq_cycles)
        except ValueError as exc:
            self.show_error(str(exc))
            return
        channels = [c for c in self.selected_channels() if c.metadata.get("import_kind") != "other"]
        settings = self.derivative_settings()
        settings.pop('peak_track_shift_mv')
        key = (tuple((c.id, c.fingerprint) for c in channels), tuple(cycles), tuple(settings.items()))
        def calculate(progress, cancel):
            result = []
            for i, channel in enumerate(channels):
                if cancel():
                    raise InterruptedError('미분 분석 취소')
                progress(i, len(channels), f'미분 분석 · {channel.name}')
                try:
                    result.extend(cached_derivatives(channel, cycles, settings, cancel))
                except InterruptedError:
                    raise
                except Exception as exc:
                    result.append(dict(error=f'{channel.name}: {exc}'))
            return result
        def ready(result):
            self._last_derivative_result = (key, result)
            self.render_derivatives(result)
        if self._last_derivative_result and self._last_derivative_result[0] == key:
            self._invalidate_analysis()
            self.render_derivatives(self._last_derivative_result[1])
            self.statusBar().showMessage('저장된 미분 계산 결과 재사용')
        else:
            self._start('analysis', calculate, ready)

    def render_derivatives(self, result):
        self._dirty_pages.discard(3)
        self._derivatives = result
        valid = [c for c in result if not c.get('error') and len(c.get('x', []))]
        errors = [c['error'] for c in result if c.get('error')]
        self._color_curves(valid, prefer_cycles=True)
        for c in valid:
            c['point_kind'] = 'calculated'
        self.dq_plot.set_curves(valid, group_cycles=True)
        self.vq_plot.set_curves([dict(c, x=c['vq_x'], y=c['vq_y']) for c in valid], group_cycles=True)
        self.dvdq_plot.set_curves([dict(c, x=c['dvdq_x'], y=c['dvdq_y']) for c in valid], group_cycles=True)
        peaks = []
        for c in valid:
            for p in c.get('peaks', []):
                peaks.append(dict(Source_ID=c.get('source_id'), Channel=c.get('name'), Cycle=c.get('cycle'),
                    Step=c.get('step'), Direction=c.get('direction'), **p))
        self.peak_table.set_frame(pd.DataFrame(peaks))
        shift_mv = self.dq_track_shift.value()
        tracked = track_peaks(valid, max_shift_v=shift_mv / 1000.)
        trends = {}
        if not tracked.empty:
            for key, group in tracked.groupby(['Source_ID', 'Direction', 'Peak_ID']):
                trends[key] = group
        trend_curves = []
        for (source, direction, identity), pts in trends.items():
            pts = pts.sort_values('Cycle')
            voltage_key = 'Voltage_V' if 'Voltage_V' in pts else 'voltage'
            trend_curves.append(dict(x=pts['Cycle'].to_numpy(), y=pts[voltage_key].to_numpy(),
                name=f'{source[:6]} · {direction} · 피크 {identity}', source_id=source,
                point_kind='calculated'))
        self.peak_trend.set_curves(trend_curves)
        self._restore_graph('dq', self.dq_plot)
        self.dq_note.setText(f'{len(valid)}개 곡선 · 물리 Step별 분석 · 피크 추이는 최대 {shift_mv:g} mV 이동 내에서 대응 (수동 검토 필요)'
                             + (' · ' + ' / '.join(errors[:4]) if errors else ''))
        if errors:
            self.show_error(' / '.join(errors[:4]))

    def _restore_graph(self, name, graph):
        state = getattr(self, '_saved_graphs', {}).pop(name, None)
        if state:
            graph.restore_graph_state(state)

    def _restore_merged_channels(self, project):
        from .analysis import merge_same_cell
        by_id = {channel.id: channel for channel in self.channels}
        for source in project.get('sources', []):
            metadata = source.get('metadata', {})
            if metadata.get('input_type') != 'same_cell_merge':
                continue
            components = metadata.get('components', [])
            if all(component.get('id') in by_id for component in components):
                merged = merge_same_cell([by_id[c['id']] for c in components],
                    [int(c['offset']) for c in components], source.get('name', '연결 시험'))
                if merged.id not in by_id:
                    merged = self._prepare_merge(merged, [by_id[c['id']] for c in components], [int(c['offset']) for c in components])
                    self.channels.append(merged)
                    by_id[merged.id] = merged

    def ui_state(self):
        return dict(selected_channels=[c.id for c in self.selected_channels()],
            active_channel=self.active_channel().id if self.active_channel() else None,
            page=self.nav.currentRow(), baseline=self.baseline.text(), aggregation=self.aggregate.currentData(),
            summary_start=self.summary_start.value(),
            include_review=self.include_review.isChecked(),
            color_mode=self.color_mode.currentData(),
            metrics={str(k): v.currentData() for k, v in self.metric_widgets.items()},
            raw=dict(cycles=self.raw_cycles.text(), direction=self.raw_direction.currentData(),
                     step=self.raw_step.text(), x=self.raw_x.currentData(), y=self.raw_y.currentData()),
            derivative=dict(cycles=self.dq_cycles.text(), **self.derivative_settings()),
            dcr=self.dcr.state(), main_split=self.main_splitter.sizes(),
            graphs={name: graph.graph_state() for name, graph in [('summary', self.summary.graph),
                ('life', self.life.graph), ('raw', self.raw.graph), ('dq', self.dq_plot), ('compare', self.compare.graph)]})

    def restore_state(self, state):
        self._restoring = True
        try:
            self.baseline.setText(str(state.get('baseline', '')))
            self.summary_start.setValue(int(state.get('summary_start', 2)))
            self.aggregate.setCurrentIndex(max(0, self.aggregate.findData(state.get('aggregation', 'representative'))))
            self.include_review.setChecked(bool(state.get('include_review', False)))
            self.color_mode.setCurrentIndex(max(0, self.color_mode.findData(state.get('color_mode', 'auto'))))
            for key, value in state.get('metrics', {}).items():
                combo = self.metric_widgets.get(int(key))
                if combo:
                    combo.setCurrentIndex(max(0, combo.findData(value)))
            selected = state.get('selected_channels')
            for i in range(self.tree.topLevelItemCount()):
                for j in range(self.tree.topLevelItem(i).childCount()):
                    child = self.tree.topLevelItem(i).child(j)
                    id_ = child.data(0, Qt.ItemDataRole.UserRole)
                    if selected is not None:
                        child.setCheckState(0, Qt.CheckState.Checked if id_ in selected else Qt.CheckState.Unchecked)
                    if id_ == state.get('active_channel'):
                        self.tree.setCurrentItem(child)
            raw = state.get('raw', {})
            self.raw_cycles.setText(str(raw.get('cycles', '1')))
            self.raw_step.setText(str(raw.get('step', '')))
            for combo, key in [(self.raw_x, 'x'), (self.raw_y, 'y'), (self.raw_direction, 'direction')]:
                if key in raw:
                    combo.setCurrentIndex(max(0, combo.findData(raw[key])))
            dq = state.get('derivative', {})
            self.dq_cycles.setText(str(dq.get('cycles', '1')))
            self.dq_step.setText(str(dq.get('step', '')))
            self.dq_direction.setCurrentIndex(max(0, self.dq_direction.findData(dq.get('direction', 'both'))))
            self.dq_points.setValue(int(dq.get('n_points', 0)))
            self.dq_smooth.setChecked(bool(dq.get('smooth', True)))
            self.dq_window.setValue(int(dq.get('smooth_window') or 0))
            self.dq_order.setValue(int(dq.get('smooth_polyorder', 3)))
            self.dq_peak.setValue(float(dq.get('peak_prominence_pct', .05)) * 100.)
            self.dq_peak_distance.setValue(float(dq.get('peak_min_distance_mv') or 0))
            self.dq_track_shift.setValue(float(dq.get('peak_track_shift_mv', 50)))
            self.dcr.restore_state(state.get('dcr', {}), auto_detect=False)
            if state.get('main_split'):
                self.main_splitter.setSizes(state['main_split'])
            self.nav.setCurrentRow(max(0, min(6, int(state.get('page', 0)))))
            self._saved_graphs = state.get('graphs', {})
        finally:
            self._restoring = False
        self.refresh_current()

    def save_current_project(self):
        if self.study_workspace.study:
            try:
                self.study_workspace.save()
                self.statusBar().showMessage('시험 분석 설정 저장 완료')
            except (OSError, ValueError) as exc:
                self.show_error(str(exc))
            return
        if not self.channels:
            self.show_error('먼저 데이터를 불러오세요.')
            return
        path, _ = QFileDialog.getSaveFileName(self, '프로젝트 저장', self._project_path or
            str(Path(default_output_dir()) / 'Battery_Analysis.basproj'), '분석 프로젝트 (*.basproj)')
        if path:
            try:
                save_project(path, self.channels, self.ui_state())
                self._project_path = path
                self.statusBar().showMessage(f'프로젝트 저장 완료 · {path}')
            except Exception as exc:
                self.show_error(str(exc))

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, '프로젝트 열기', str(default_output_dir()), '분석 프로젝트 (*.basproj)')
        if not path:
            return
        try:
            project = load_project(path)
            prepared = project_channels(project)
            if prepared is not None:
                self.study_workspace.leave()
                self._pending_project = None
                self._project_path = path
                self.show_study_channels(prepared, project.get('ui_state', {}))
                self.merge_button.show()
                self.study_details_button.hide()
                return
            sources = project.get('sources', [])
            paths = []
            for source in sources:
                metadata = source.get('metadata', {})
                entries = metadata.get('components', []) if metadata.get('input_type') == 'same_cell_merge' else [source]
                paths.extend(str(entry.get('source_path', '')) for entry in entries)
            paths = list(dict.fromkeys(paths))
            paths = [p for p in paths if p]
            resolved = []
            for original in paths:
                if Path(original).exists():
                    resolved.append(original)
                else:
                    replacement, _ = QFileDialog.getOpenFileName(self, f'원본 다시 연결: {Path(original).name}')
                    if not replacement:
                        raise ValueError('원본 파일 연결을 취소했습니다.')
                    resolved.append(replacement)
            if not resolved:
                raise ValueError('프로젝트에 원본 경로가 없습니다.')
            self.study_workspace.leave()
            self._pending_project = project
            self._project_path = path
            csv_options = {}
            relocated = dict(zip(paths, resolved))
            for source in sources:
                metadata = source.get('metadata', {})
                if metadata.get('csv_options'):
                    old_path = source['source_path']
                    new_path = relocated.get(old_path, old_path)
                    suffix = '::' + metadata['source_member'] if Path(new_path).suffix.lower() == '.zip' else ''
                    csv_options[new_path + suffix] = metadata['csv_options']
            self.import_paths(resolved, replace=True, csv_options=csv_options or None)
        except Exception as exc:
            self.show_error(str(exc))

    def save_preset(self):
        path, _ = QFileDialog.getSaveFileName(self, '분석 프리셋 저장',
            str(Path(default_output_dir()) / 'analysis_preset.json'), 'JSON (*.json)')
        if path:
            state = self.ui_state()
            for key in ['selected_channels', 'active_channel', 'dcr', 'graphs']:
                state.pop(key, None)
            Path(path).write_text(json.dumps({'type': 'bas3_preset', 'ui_state': state}, ensure_ascii=False, indent=2), encoding='utf-8')

    def open_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, '분석 프리셋 불러오기', str(default_output_dir()), 'JSON (*.json)')
        if path:
            try:
                value = json.loads(Path(path).read_text(encoding='utf-8'))
                if value.get('type') != 'bas3_preset':
                    raise ValueError('지원하는 분석 프리셋이 아닙니다.')
                self.restore_state(value['ui_state'])
            except Exception as exc:
                self.show_error(str(exc))

    def current_frame(self):
        row = self.nav.currentRow()
        if row == 6:
            row = self._last_analysis_page
        if row in (0, 1, 2, 5):
            if row == 2 and self.raw_tables.currentIndex() == 1:
                return self.match_table.model.frame
            if row == 2 and self.raw_tables.currentIndex() == 2:
                return self.source_table.model.frame
            return self.pages[row].table.model.frame
        if row == 3:
            return self.peak_table.model.frame
        if row == 4:
            return pd.DataFrame(getattr(self.dcr, 'result_rows', [])) if self.dcr.result_valid else pd.DataFrame()
        frames = []
        for channel in self.selected_channels():
            if channel.id in self._summaries:
                frames.append(self._summaries[channel.id].assign(Channel=channel.name, Source_ID=channel.id))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def export_current_table(self):
        current_page = self._last_analysis_page if self.nav.currentRow() == 6 else self.nav.currentRow()
        if current_page in self._dirty_pages:
            self.show_error('설정이 변경되었습니다. 재분석 후 새 결과를 저장하세요.')
            return
        frame = self.current_frame()
        if frame.empty:
            self.show_error('저장할 결과 표가 없습니다. 먼저 분석하세요.')
            return
        path, _ = QFileDialog.getSaveFileName(self, '현재 결과 표 저장', str(Path(default_output_dir()) / 'analysis.csv'), 'CSV (*.csv)')
        if path:
            frame.to_csv(path, index=False, encoding='utf-8-sig')
            self.statusBar().showMessage(f'저장 완료 · {path}')

    def show_rpt_results(self):
        channels = [c for c in self.channels if c.metadata.get("import_kind") == "rpt"]
        if not channels:
            self.show_error('RPT로 분류한 파일을 먼저 가져오세요.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('저장된 RPT 결과')
        dialog.resize(950, 520)
        layout = QVBoxLayout(dialog)
        selector = QComboBox()
        for channel in channels:
            selector.addItem(f'{channel.name} · {Path(channel.source_path).name}')
        layout.addWidget(selector)
        table = DataTable()
        layout.addWidget(table)
        def display(index):
            table.set_frame(pd.DataFrame(channels[index].frame.rpt_rows()))
        selector.currentIndexChanged.connect(display)
        display(0)
        button = QPushButton('결과 CSV 저장')
        def export():
            path, _ = QFileDialog.getSaveFileName(dialog, 'RPT 결과 저장',
                str(Path(default_output_dir()) / 'RPT.csv'), 'CSV (*.csv)')
            if path:
                try:
                    table.model.frame.to_csv(path, index=False, encoding='utf-8-sig')
                except OSError as exc:
                    self.show_error(str(exc))
        button.clicked.connect(export)
        layout.addWidget(button)
        dialog.exec()

    def export_raw(self):
        channel = self.active_channel()
        if channel is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, '활성 채널 곡선 데이터 저장', str(Path(default_output_dir()) / 'raw.csv'), 'CSV (*.csv)')
        if path:
            self._start('export', lambda progress, cancel: self._write_csv(channel, path, cancel),
                        lambda _: self.statusBar().showMessage(f'Raw CSV 저장 완료 · {path}'))

    def export_origin(self, graph, title):
        from .origin_export import paste_origin_to_powerpoint, prepare_curves

        if graph._stale:
            self.show_error('분석 조건을 적용한 뒤 PPT에 붙여넣으세요.')
            return
        if 'origin_export' in self._jobs or 'origin_export' in self._queued_jobs:
            self.statusBar().showMessage('진행 중인 PPT 붙여넣기가 끝날 때까지 기다려 주세요.')
            return
        checked = graph.checked_curves()
        names = {c.id: c.name for c in self.channels}
        sets = {}
        for curve in checked:
            source_id = curve.get('source_id')
            if source_id and curve.get('cycle') is not None:
                sets.setdefault(source_id, names.get(source_id) or curve.get('channel') or source_id)
        if not sets:
            self.show_error('범례에서 붙여넣을 Cycle을 체크하세요.')
            return
        source_id = next(iter(sets))
        if len(sets) > 1:
            options = {f'{i + 1}. {name}': key for i, (key, name) in enumerate(sets.items())}
            current = self.active_channel()
            default = list(options.values()).index(current.id) if current and current.id in sets else 0
            choice, ok = QInputDialog.getItem(self, 'PPT Cycle 비교', '붙여넣을 시험 세트 / 셀',
                                              list(options), default, False)
            if not ok:
                return
            source_id = options[choice]
        # Keep the chosen curves fixed while the graph is being built.
        checked = [dict(curve) for curve in checked if curve.get('source_id') == source_id]
        name = f'{sets[source_id]} · {title} Cycle 비교'
        x_label, y_label = (graph.plot.getAxis(axis).labelText for axis in ('bottom', 'left'))
        ranges = tuple(tuple(bounds) for bounds in graph.plot.viewRange())

        def write(progress, cancel):
            curves = prepare_curves(checked, source_id)
            return paste_origin_to_powerpoint(curves, name, x_label, y_label, progress, cancel, ranges)

        self._start('origin_export', write,
                    lambda slide: self.statusBar().showMessage(f'PPT 붙여넣기 완료 · {slide}'))

    @staticmethod
    def _write_csv(channel, path, cancel):
        target = Path(path)
        temp = target.with_suffix(target.suffix + '.partial')
        try:
            for start in range(0, max(1, channel.row_count), 100000):
                if cancel():
                    raise InterruptedError('저장 취소')
                end = min(start + 100000, channel.row_count)
                channel.read_rows([slice(start, end)]).to_csv(temp, mode='w' if start == 0 else 'a',
                    header=start == 0, index=False, encoding='utf-8-sig' if start == 0 else 'utf-8')
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()
        return str(target)

    def export_excel(self):
        channels = self.selected_channels()
        if not channels:
            self.show_error('저장할 채널을 선택하세요.')
            return
        try:
            baseline = self._baseline_value()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, '분석 Excel 저장',
            str(Path(default_output_dir()) / 'Battery_Analysis.xlsx'), 'Excel (*.xlsx)')
        if not path:
            return
        state = self.ui_state()
        review, aggregation = self.include_review.isChecked(), self.aggregate.currentData()
        cached_tables = dict(self._summary_cache)
        cache_dir = self._cycle_cache_dir()
        peaks = self.peak_table.model.frame.copy() if 3 not in self._dirty_pages else pd.DataFrame()
        dcr_rows = list(getattr(self.dcr, 'result_rows', [])) if self.dcr.result_valid else []
        def export(progress, cancel):
            temp = Path(path).with_suffix('.partial.xlsx')
            try:
                with pd.ExcelWriter(temp, engine='openpyxl') as writer:
                    provenance = []
                    for i, c in enumerate(channels):
                        if cancel():
                            raise InterruptedError('Excel 저장 취소')
                        progress(i, len(channels), f'Excel 저장 · {c.name}')
                        key = self._cycle_table_key(c, review)
                        base = cached_tables.get(key)
                        if base is None:
                            base = prepare_cycle_tables(c, key[-1], cache_dir, cancel)
                        summary = apply_cycle_baseline(base[aggregation], c.name, baseline, first_cycle=2)
                        for start in range(0, max(1, len(summary)), 1000000):
                            summary.iloc[start:start + 1000000].to_excel(writer, sheet_name=f'Life_{i + 1}_{start // 1000000 + 1}', index=False)
                        if len(c.steps):
                            for start in range(0, len(c.steps), 1000000):
                                c.steps.iloc[start:start + 1000000].to_excel(writer, sheet_name=f'Steps_{i + 1}_{start // 1000000 + 1}', index=False)
                        provenance.append(dict(ID=c.id, Channel=c.name, Source=c.source_path,
                            Warnings=' / '.join(c.warnings), Metadata=json.dumps(c.metadata, ensure_ascii=False, default=str)))
                    pd.DataFrame(provenance).to_excel(writer, sheet_name='Sources', index=False)
                    pd.DataFrame([dict(Setting=k, Value=json.dumps(v, ensure_ascii=False, default=str)) for k, v in state.items()]).to_excel(writer, sheet_name='Settings', index=False)
                    if not peaks.empty:
                        peaks.to_excel(writer, sheet_name='Peaks', index=False)
                    if dcr_rows:
                        pd.DataFrame(dcr_rows).to_excel(writer, sheet_name='DCR', index=False)
                if cancel():
                    raise InterruptedError('Excel 저장 취소')
                os.replace(temp, path)
            finally:
                if temp.exists():
                    temp.unlink()
            return path
        self._start('export', export, lambda p: self.statusBar().showMessage(f'Excel 저장 완료 · {p}'))

    @staticmethod
    def _prepare_merge(merged, channels, offsets, cancel=None):
        if all(isinstance(channel.frame, CompactStore) for channel in channels):
            from tempfile import gettempdir
            return prepare_channel(merged, Path(gettempdir()) / 'BatteryAnalysisStudio3' / 'cache',
                                   cancel, components=list(zip(channels, offsets)))
        return merged

    def merge_channels(self):
        channels = self.selected_channels()
        if any(c.metadata.get("import_kind", "cycle") != "cycle" for c in channels):
            self.show_error('Cycle로 분류한 채널만 연결할 수 있습니다.')
            return
        if len(channels) < 2:
            self.show_error('같은 셀의 분할시험 채널을 두 개 이상 선택하세요.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('같은 셀의 연속 기록 연결')
        layout = QVBoxLayout(dialog)
        note = QLabel('선택 순서대로 연결합니다. 같은 셀의 연속 시험인지 확인하세요.\n'
                      '원본 Cycle은 보존됩니다. 서로 다른 셀에는 이 기능을 사용하지 마세요.')
        note.setWordWrap(True)
        layout.addWidget(note)
        offsets = []
        for c in channels:
            row = QHBoxLayout()
            row.addWidget(QLabel(c.name))
            spin = QSpinBox()
            spin.setRange(-10000000, 10000000)
            spin.setPrefix('Cycle Offset  ')
            offsets.append(spin)
            row.addWidget(spin)
            layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                from .analysis import merge_same_cell
                values = [s.value() for s in offsets]
                def work(progress, cancel):
                    return self._prepare_merge(merge_same_cell(channels, values), channels, values, cancel)
                def ready(merged):
                    self.channels.append(merged)
                    self._populate_tree()
                    self.refresh_current()
                self._start('analysis', work, ready)
            except Exception as exc:
                self.show_error(str(exc))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            if self.study_workspace.study:
                self.study_workspace.add(paths, cycle_only=any(Path(p).is_dir() for p in paths))
            else:
                self.import_paths(paths, configure=True)

    def closeEvent(self, event):
        active_threads = [t for t in self._threads if t.isRunning()]
        if active_threads:
            self.cancel_jobs()
            self.show_error('작업을 취소하고 있습니다. 종료 후 창을 닫아주세요.')
            event.ignore()
            return
        if hasattr(self.dcr, 'shutdown') and self.dcr.shutdown() is False:
            event.ignore()
            return
        try:
            self.study_workspace.save()
        except (OSError, ValueError) as exc:
            self.show_error(f'시험 설정 저장 실패: {exc}')
            if QMessageBox.question(self, '종료', '시험 설정을 저장하지 못했습니다. 그래도 닫을까요?') != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()
