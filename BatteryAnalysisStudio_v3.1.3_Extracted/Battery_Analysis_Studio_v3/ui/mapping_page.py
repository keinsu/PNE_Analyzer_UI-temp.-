# -*- coding: utf-8 -*-
"""
mapping_page.py
2단계: 컬럼 자동 매핑 확인/수정, 전처리(포메이션) 구간 제외, CC/CV 상한전압 설정.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel, QComboBox,
    QRadioButton, QButtonGroup, QLineEdit, QCheckBox, QPushButton, QSpinBox, QScrollArea
)
from PySide6.QtCore import Signal, Qt
from . import theme
from .widgets import hline

FIELD_LABELS = {
    "cycle": "Cycle 번호",
    "stepType": "Step 구분",
    "voltage": "Voltage",
    "current": "Current",
    "time": "시간 (선택, CC/CV 시간용)",
    "stepNo": "Step 번호 (전처리 감지용, 선택)",
    "energy": "에너지 (선택)",
    "temp": "온도 (선택)",
}
FIELD_ORDER = ["cycle", "stepType", "voltage", "current", "time", "stepNo", "energy", "temp"]


class MappingPage(QWidget):
    reparseRequested = Signal(str, int)          # encoding, skip_lines
    pretreatDetectRequested = Signal(str, str)   # cycle_col, stepno_col
    startAnalysis = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.headers = []
        self.mapping = {}
        self.cap_mode = "split"
        self.detected_pretreat_steps = []
        self.combos = {}

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        panel = QFrame()
        panel.setObjectName("panel")
        self.layout_ = QVBoxLayout(panel)
        self.layout_.setContentsMargins(24, 24, 24, 24)
        self.layout_.setSpacing(14)
        scroll.setWidget(panel)

        # header row
        head_row = QHBoxLayout()
        title = QLabel("컬럼 자동 매핑 확인")
        title.setProperty("role", "display")
        title.setStyleSheet(f"font-family:'{theme.FONT_DISPLAY}'; font-size:16px; font-weight:600;")
        self.fileBadge = QLabel("")
        self.fileBadge.setProperty("role", "badge")
        head_row.addWidget(title)
        head_row.addStretch(1)
        head_row.addWidget(self.fileBadge)
        self.layout_.addLayout(head_row)

        self.subInfo = QLabel("")
        self.subInfo.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11.5px;")
        self.layout_.addWidget(self.subInfo)

        # 단위 표기와 숫자 단위가 어긋난 내보내기 파일도 안전하게 처리할 수 있는 수동 보정값.
        unit_row = QHBoxLayout()
        unit_title = QLabel("DCR 단위 확인")
        unit_title.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:12px;")
        self.currentUnitCombo = QComboBox()
        self.currentUnitCombo.addItem("전류: 헤더 자동 / 전력 검증", "auto")
        self.currentUnitCombo.addItem("전류: A", "A")
        self.currentUnitCombo.addItem("전류: mA", "mA")
        self.capacityUnitCombo = QComboBox()
        self.capacityUnitCombo.addItem("용량: 헤더 자동", "auto")
        self.capacityUnitCombo.addItem("용량: Ah", "Ah")
        self.capacityUnitCombo.addItem("용량: mAh", "mAh")
        unit_row.addWidget(unit_title)
        unit_row.addWidget(self.currentUnitCombo)
        unit_row.addWidget(self.capacityUnitCombo)
        unit_row.addStretch(1)
        self.layout_.addLayout(unit_row)

        # encoding / skip lines / reparse
        enc_row = QHBoxLayout()
        lbl_enc = QLabel("인코딩")
        lbl_enc.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:12px;")
        self.encodingCombo = QComboBox()
        self.encodingCombo.addItem("EUC-KR / CP949", "euc-kr")
        self.encodingCombo.addItem("UTF-8", "utf-8")
        lbl_skip = QLabel("상단 무시 행 수")
        lbl_skip.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:12px;")
        self.skipSpin = QSpinBox()
        self.skipSpin.setRange(0, 100)
        self.btnReparse = QPushButton("다시 파싱")
        self.btnReparse.clicked.connect(lambda: self.reparseRequested.emit(self.encodingCombo.currentData(), self.skipSpin.value()))
        enc_row.addWidget(lbl_enc)
        enc_row.addWidget(self.encodingCombo)
        enc_row.addSpacing(12)
        enc_row.addWidget(lbl_skip)
        enc_row.addWidget(self.skipSpin)
        enc_row.addWidget(self.btnReparse)
        enc_row.addStretch(1)
        self.layout_.addLayout(enc_row)

        # cap mode radios
        cap_frame = QFrame()
        cap_frame.setObjectName("hairline")
        cap_layout = QHBoxLayout(cap_frame)
        self.radioSplit = QRadioButton("충전/방전 용량 컬럼이 분리되어 있음")
        self.radioSingle = QRadioButton("용량 컬럼 1개 + Step 구분으로 판별")
        self.capGroup = QButtonGroup(self)
        self.capGroup.addButton(self.radioSplit)
        self.capGroup.addButton(self.radioSingle)
        self.radioSplit.toggled.connect(self._on_cap_mode_changed)
        cap_layout.addWidget(self.radioSplit)
        cap_layout.addWidget(self.radioSingle)
        cap_layout.addStretch(1)
        self.layout_.addWidget(cap_frame)

        # field mapping grid
        self.fieldGrid = QGridLayout()
        self.fieldGrid.setHorizontalSpacing(24)
        self.fieldGrid.setVerticalSpacing(10)
        self.layout_.addLayout(self.fieldGrid)
        self._build_field_grid()

        # pretreatment box
        pre_frame = QFrame()
        pre_frame.setObjectName("hairline")
        pre_layout = QVBoxLayout(pre_frame)
        pre_title = QLabel("전처리(포메이션) 구간 제외")
        pre_title.setStyleSheet(f"font-family:'{theme.FONT_DISPLAY}'; font-size:13px; font-weight:600;")
        pre_layout.addWidget(pre_title)

        self.pretreatAutoChk = QCheckBox("Step 번호 기준 자동 감지 (대부분의 사이클에 없는, 특이한 Step을 전처리로 간주)")
        # 시험 목적에 따라 진단/레퍼런스/펄스 Step이 일부 Cycle에만 나타날 수 있으므로,
        # 자동 제외는 사용자가 감지 결과를 확인한 뒤 명시적으로 켜도록 한다.
        self.pretreatAutoChk.setChecked(False)
        self.pretreatAutoChk.toggled.connect(self._refresh_pretreat_label)
        pre_layout.addWidget(self.pretreatAutoChk)

        self.pretreatInfoLabel = QLabel("")
        self.pretreatInfoLabel.setStyleSheet(f"font-family:'{theme.FONT_MONO}'; font-size:11.5px;")
        self.pretreatInfoLabel.setWordWrap(True)
        pre_layout.addWidget(self.pretreatInfoLabel)

        exclude_row = QHBoxLayout()
        col1 = QVBoxLayout()
        l1 = QLabel("직접 제외할 Step 번호 (쉼표/범위, 예: 2,3,4-5)")
        l1.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        self.excludeStepsInp = QLineEdit()
        self.excludeStepsInp.setPlaceholderText("예: 2,3,4-5")
        col1.addWidget(l1)
        col1.addWidget(self.excludeStepsInp)
        col2 = QVBoxLayout()
        l2 = QLabel("직접 제외할 사이클 번호 (쉼표/범위)")
        l2.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        self.excludeCyclesInp = QLineEdit()
        self.excludeCyclesInp.setPlaceholderText("예: 1")
        col2.addWidget(l2)
        col2.addWidget(self.excludeCyclesInp)
        exclude_row.addLayout(col1)
        exclude_row.addLayout(col2)
        pre_layout.addLayout(exclude_row)
        self.layout_.addWidget(pre_frame)

        # upper voltage box
        uv_frame = QFrame()
        uv_frame.setObjectName("hairline")
        uv_layout = QHBoxLayout(uv_frame)
        uv_title = QLabel("충전 CC/CV 구분 상한전압")
        uv_title.setStyleSheet(f"font-family:'{theme.FONT_DISPLAY}'; font-size:13px; font-weight:600;")
        self.uvAuto = QRadioButton("자동 감지 (충전구간 최대전압)")
        self.uvManual = QRadioButton("직접 입력 (V)")
        self.uvAuto.setChecked(True)
        self.uvGroup = QButtonGroup(self)
        self.uvGroup.addButton(self.uvAuto)
        self.uvGroup.addButton(self.uvManual)
        self.uvValueInp = QLineEdit()
        self.uvValueInp.setPlaceholderText("예: 4.2")
        self.uvValueInp.setFixedWidth(90)
        self.uvManual.toggled.connect(lambda checked: self.uvValueInp.setEnabled(checked))
        self.uvValueInp.setEnabled(False)
        uv_layout.addWidget(uv_title)
        uv_layout.addSpacing(16)
        uv_layout.addWidget(self.uvAuto)
        uv_layout.addWidget(self.uvManual)
        uv_layout.addWidget(self.uvValueInp)
        uv_layout.addStretch(1)
        self.layout_.addWidget(uv_frame)

        self.errorLabel = QLabel("")
        self.errorLabel.setProperty("role", "error")
        self.errorLabel.setWordWrap(True)
        self.layout_.addWidget(self.errorLabel)

        self.btnStart = QPushButton("분석 시작 →")
        self.btnStart.setProperty("variant", "primary")
        self.btnStart.clicked.connect(self._emit_start)
        self.layout_.addWidget(self.btnStart)
        self.layout_.addStretch(1)

    # ------------------------------------------------------------------
    def _build_field_grid(self):
        while self.fieldGrid.count():
            item = self.fieldGrid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)  # 즉시 분리 (deleteLater는 다음 이벤트루프까지 남아있어 겹쳐 보일 수 있음)
        self.combos = {}

        row = 0
        col = 0
        keys = list(FIELD_ORDER)
        if self.cap_mode == "split":
            keys += ["chgCap", "dchgCap"]
        else:
            keys += ["capacity"]

        labels = dict(FIELD_LABELS)
        labels["chgCap"] = "충전 용량"
        labels["dchgCap"] = "방전 용량"
        labels["capacity"] = "용량 (단일 컬럼)"

        for key in keys:
            lbl = QLabel(labels[key])
            lbl.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:12px;")
            combo = QComboBox()
            combo.addItem("-- 선택 --", "")
            for hd in self.headers:
                combo.addItem(hd, hd)
            cur = self.mapping.get(key, "")
            idx = combo.findData(cur)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(lambda _, k=key, c=combo: self._on_field_changed(k, c))
            self.combos[key] = combo

            self.fieldGrid.addWidget(lbl, row, col * 2)
            self.fieldGrid.addWidget(combo, row, col * 2 + 1)
            col += 1
            if col >= 2:
                col = 0
                row += 1

    def _on_field_changed(self, key, combo):
        self.mapping[key] = combo.currentData() or ""
        self._update_start_enabled()
        if key in ("cycle", "stepNo"):
            self._maybe_request_pretreat()

    def _on_cap_mode_changed(self):
        self.cap_mode = "split" if self.radioSplit.isChecked() else "single"
        self._build_field_grid()
        self._update_start_enabled()

    def _maybe_request_pretreat(self):
        cyc = self.mapping.get("cycle")
        stepno = self.mapping.get("stepNo")
        if cyc and stepno:
            self.pretreatDetectRequested.emit(cyc, stepno)

    def _refresh_pretreat_label(self):
        if not self.mapping.get("stepNo"):
            self.pretreatInfoLabel.setText('위에서 "Step 번호" 컬럼을 매핑해야 자동 감지를 사용할 수 있어요.')
            return
        if not self.pretreatAutoChk.isChecked():
            self.pretreatInfoLabel.setText(
                f"<span style='color:{theme.SUBTLE}'>자동 제외가 꺼져 있습니다. 변동 Step이 정상 시험 단계라면 이 상태를 유지하세요.</span>"
            )
            return
        if self.detected_pretreat_steps:
            self.pretreatInfoLabel.setText(
                f"<span style='color:{theme.AMBER}'>감지된 전처리 Step: {', '.join(self.detected_pretreat_steps)}</span>"
            )
        else:
            self.pretreatInfoLabel.setText(f"<span style='color:{theme.SUBTLE}'>특이 Step이 감지되지 않았습니다.</span>")

    # ------------------------------------------------------------------
    # public API (MainWindow에서 호출)
    # ------------------------------------------------------------------
    def set_file_badge(self, name: str):
        self.fileBadge.setText(name)

    def set_encoding_skip(self, encoding: str, skip_lines: int):
        idx = self.encodingCombo.findData(encoding)
        if idx >= 0:
            self.encodingCombo.setCurrentIndex(idx)
        self.skipSpin.setValue(skip_lines)

    def set_headers_and_mapping(self, headers: list, mapping: dict, cap_mode: str, row_count: int = None):
        self.headers = headers
        self.mapping = dict(mapping)
        self.cap_mode = cap_mode
        self.radioSplit.blockSignals(True)
        self.radioSingle.blockSignals(True)
        self.radioSplit.setChecked(cap_mode == "split")
        self.radioSingle.setChecked(cap_mode == "single")
        self.radioSplit.blockSignals(False)
        self.radioSingle.blockSignals(False)
        self._build_field_grid()
        info = f"헤더 {len(headers)}개 인식됨 · 자동 추정이 틀렸다면 아래에서 직접 지정하세요"
        if row_count:
            info += f" (전체 {row_count:,}행)"
        self.subInfo.setText(info)
        self._update_start_enabled()
        self._maybe_request_pretreat()

    def set_detected_pretreat(self, steps: list):
        self.detected_pretreat_steps = steps
        self._refresh_pretreat_label()

    def set_error(self, msg: str):
        self.errorLabel.setText(msg or "")

    def set_busy(self, busy: bool):
        self.btnStart.setEnabled(not busy and self._mapping_complete())

    def _mapping_complete(self) -> bool:
        m = self.mapping
        if not m.get("cycle") or not m.get("voltage"):
            return False
        if self.cap_mode == "split":
            return bool(m.get("chgCap") and m.get("dchgCap"))
        return bool(m.get("stepType") and m.get("capacity"))

    def _update_start_enabled(self):
        self.btnStart.setEnabled(self._mapping_complete())

    def _parse_range_list(self, text: str) -> list:
        result = set()
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    a, b = part.split("-")
                    a, b = int(a), int(b)
                    for i in range(min(a, b), max(a, b) + 1):
                        result.add(str(i))
                except ValueError:
                    continue
            elif part.isdigit():
                result.add(part)
        return sorted(result)

    def _emit_start(self):
        exclude_steps = self._parse_range_list(self.excludeStepsInp.text())
        if self.pretreatAutoChk.isChecked():
            exclude_steps = sorted(set(exclude_steps) | set(self.detected_pretreat_steps))
        exclude_cycles = self._parse_range_list(self.excludeCyclesInp.text())

        upper_voltage = None
        if self.uvManual.isChecked():
            try:
                upper_voltage = float(self.uvValueInp.text())
            except ValueError:
                upper_voltage = None

        params = {
            "mapping": dict(self.mapping),
            "capMode": self.cap_mode,
            "unitOverrides": {
                "current": self.currentUnitCombo.currentData(),
                "capacity": self.capacityUnitCombo.currentData(),
            },
            "excludeSteps": exclude_steps,
            "excludeCycles": exclude_cycles,
            "upperVoltage": upper_voltage,
        }
        self.startAnalysis.emit(params)
