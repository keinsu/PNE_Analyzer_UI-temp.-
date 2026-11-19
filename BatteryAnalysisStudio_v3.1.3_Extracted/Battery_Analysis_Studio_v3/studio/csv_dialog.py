"""Manual equipment CSV mapping with a small, read-only source preview."""
from __future__ import annotations

from pathlib import Path
import csv
import io

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QPlainTextEdit, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView,
)

from backend.mapping_loader import guess_mapping
from .ingestion import _text


class CSVImportDialog(QDialog):
    """Inspect up to 100 possible header lines and assign source columns.

    options() returns only plain JSON-compatible values; it is safe to store
    together with a project and pass back to load_inputs(csv_options=...).
    """
    FIELDS = [
        ("cycle", "Cycle *"), ("voltage", "전압 *"),
        ("current", "전류"), ("stepType", "Step 구분 · 충전/방전/휴지"),
        ("stepNo", "Step 번호"), ("time", "Step 시간"),
        ("capacity", "공통 용량 · 단일 모드 *"),
        ("chgCap", "충전 용량 · 분리 모드 *"),
        ("dchgCap", "방전 용량 · 분리 모드 *"),
        ("energy", "에너지"), ("temp", "온도"),
    ]

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = str(Path(path).resolve())
        self.setWindowTitle("CSV 컬럼 매핑")
        self.resize(1040, 760)
        self._headers = []
        self._rows = []
        self._read_error = ""
        self._updating = False
        self._text_lines = []
        self.encoding = ""
        try:
            # Preview is bounded independently of the full import size.
            chunks = []
            with open(self.path, "rb") as stream:
                for _ in range(130):
                    chunk = stream.readline(131072)
                    if not chunk:
                        break
                    chunks.append(chunk)
            data = b"".join(chunks)
            # A truncated preview must finish on a complete line/code point.
            if len(chunks) >= 130 or (chunks and len(chunks[-1]) == 131072):
                last_newline = data.rfind(b"\n")
                if last_newline >= 0:
                    data = data[:last_newline + 1]
            if data[:2] in {b"\xff\xfe", b"\xfe\xff"} and len(data) % 2:
                data = data[:-1]
            text, self.encoding = _text(data)
            self._text_lines = text.splitlines()
        except (OSError, ValueError) as exc:
            self._read_error = str(exc)

        layout = QVBoxLayout(self)
        title = QLabel(f"<b>{Path(self.path).name}</b> · {self.encoding or '인코딩 확인 필요'}")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setText(f"{Path(self.path).name} · {self.encoding or '인코딩 확인 필요'}")
        layout.addWidget(title)
        note = QLabel("원본 파일은 변경하지 않습니다. 헤더 행과 단위를 확인한 뒤 분석할 컬럼을 연결하세요.")
        note.setWordWrap(True)
        layout.addWidget(note)

        top = QHBoxLayout()
        top.addWidget(QLabel("헤더 행"))
        self.header_spin = QSpinBox()
        self.header_spin.setRange(1, max(1, min(100, len(self._text_lines))))
        self.header_spin.setToolTip("컬럼 이름이 있는 실제 행 번호입니다. 1행부터 셉니다.")
        top.addWidget(self.header_spin)
        top.addWidget(QLabel("구분 문자"))
        self.separator_combo = QComboBox()
        for label, value in (("자동", ""), ("쉼표 (,)", ","), ("탭", "\t"), ("세미콜론 (;)", ";")):
            self.separator_combo.addItem(label, value)
        top.addWidget(self.separator_combo)
        top.addWidget(QLabel("용량 형식"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("공통 용량 컬럼", "single")
        self.mode_combo.addItem("충전·방전 용량 분리", "split")
        top.addWidget(self.mode_combo)
        top.addStretch(1)
        layout.addLayout(top)

        self.source_preview = QPlainTextEdit()
        self.source_preview.setReadOnly(True)
        self.source_preview.setMaximumHeight(125)
        self.source_preview.setPlainText("\n".join(f"{i + 1:>3}: {line[:500]}" for i, line in enumerate(self._text_lines[:100])))
        layout.addWidget(self.source_preview)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        column_panel = QWidget()
        form = QFormLayout(column_panel)
        form.setContentsMargins(0, 0, 8, 0)
        self.column_combos = {}
        for key, label in self.FIELDS:
            combo = QComboBox()
            combo.setMinimumWidth(210)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(20)
            self.column_combos[key] = combo
            form.addRow(label, combo)
            combo.currentIndexChanged.connect(self._validate)
        splitter.addWidget(column_panel)
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_label = QLabel("선택한 헤더의 데이터 미리보기 · 최대 8행")
        preview_layout.addWidget(self.preview_label)
        self.preview_table = QTableWidget()
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        preview_layout.addWidget(self.preview_table)
        splitter.addWidget(preview_panel)
        splitter.setSizes([400, 600])
        layout.addWidget(splitter, 1)

        units = QHBoxLayout()
        units.addWidget(QLabel("전압 단위"))
        self.voltage_unit_combo = QComboBox()
        for label, value in (("자동 판별", "auto"), ("V", "V"), ("mV", "mV")):
            self.voltage_unit_combo.addItem(label, value)
        units.addWidget(self.voltage_unit_combo)
        units.addWidget(QLabel("전류 단위"))
        self.current_unit_combo = QComboBox()
        for label, value in (("자동 판별", "auto"), ("mA", "mA"), ("A", "A")):
            self.current_unit_combo.addItem(label, value)
        units.addWidget(self.current_unit_combo)
        units.addWidget(QLabel("용량 단위"))
        self.capacity_unit_combo = QComboBox()
        for label, value in (("자동 판별", "auto"), ("mAh", "mAh"), ("Ah", "Ah")):
            self.capacity_unit_combo.addItem(label, value)
        units.addWidget(self.capacity_unit_combo)
        units.addWidget(QLabel("에너지 단위"))
        self.energy_unit_combo = QComboBox()
        for label, value in (("자동 판별", "auto"), ("mWh", "mWh"), ("Wh", "Wh")):
            self.energy_unit_combo.addItem(label, value)
        units.addWidget(self.energy_unit_combo)
        unit_note = QLabel("자동 판별은 헤더 단위와 기존 정합도 검증을 사용합니다.")
        unit_note.setWordWrap(True)
        units.addWidget(unit_note, 1)
        layout.addLayout(units)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:#cc5544;")
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("이 설정으로 열기")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        guessed_header = next((i for i, line in enumerate(self._text_lines[:100])
                               if ("voltage" in line.casefold() or "전압" in line)
                               and ("cycle" in line.casefold() or "사이클" in line or "싸이클" in line)), 0)
        self.header_spin.setValue(guessed_header + 1)
        self.header_spin.valueChanged.connect(self._refresh)
        self.separator_combo.currentIndexChanged.connect(self._refresh)
        self.mode_combo.currentIndexChanged.connect(self._validate)
        self._refresh()

    def _separator(self):
        selected = self.separator_combo.currentData()
        if selected:
            return selected
        i = self.header_spin.value() - 1
        line = self._text_lines[i] if i < len(self._text_lines) else ""
        return max((",", "\t", ";"), key=lambda sep: line.count(sep))

    def _refresh(self, *_):
        self._updating = True
        self._headers, self._rows = [], []
        parse_error = self._read_error
        if not parse_error:
            try:
                reader = csv.reader(io.StringIO("\n".join(self._text_lines[self.header_spin.value() - 1:])), delimiter=self._separator())
                self._headers = [str(h).lstrip("\ufeff").strip() for h in next(reader)]
                self._rows = [row for _, row in zip(range(8), reader)]
                if len(set(self._headers)) != len(self._headers):
                    parse_error = "중복된 컬럼명이 있습니다. 정확한 헤더 행을 선택하세요."
                if not self._headers or not any(self._headers):
                    parse_error = "선택한 행에 컬럼 이름이 없습니다."
            except (csv.Error, StopIteration) as exc:
                parse_error = f"미리보기를 읽을 수 없습니다: {exc}"
        self._parse_error = parse_error
        guessed = guess_mapping(self._headers)
        for key, combo in self.column_combos.items():
            combo.clear()
            combo.addItem("— 연결 안 함 —", "")
            for header in self._headers:
                combo.addItem(header, header)
            selected = guessed["mapping"].get(key, "")
            index = combo.findData(selected)
            combo.setCurrentIndex(max(0, index))
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(guessed["capMode"])))
        shown_headers = self._headers[:30]
        self.preview_table.setColumnCount(len(shown_headers))
        self.preview_table.setHorizontalHeaderLabels(shown_headers)
        self.preview_table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            for j, value in enumerate(row[:30]):
                self.preview_table.setItem(i, j, QTableWidgetItem(value[:500]))
        self._updating = False
        self._validate()

    def options(self) -> dict:
        return {"mapping": {key: str(combo.currentData() or "") for key, combo in self.column_combos.items()},
                "cap_mode": str(self.mode_combo.currentData()),
                "skip_lines": self.header_spin.value() - 1,
                "separator": self._separator(),
                "unit_overrides": {"voltage": str(self.voltage_unit_combo.currentData()),
                                   "current": str(self.current_unit_combo.currentData()),
                                   "capacity": str(self.capacity_unit_combo.currentData()),
                                   "energy": str(self.energy_unit_combo.currentData())}}

    def _validation_error(self):
        if self._parse_error:
            return self._parse_error
        options = self.options()
        mapping = options["mapping"]
        if not mapping["cycle"] or not mapping["voltage"]:
            return "Cycle과 전압 컬럼을 연결하세요."
        if mapping["cycle"] == mapping["voltage"]:
            return "Cycle과 전압에 같은 컬럼을 사용할 수 없습니다."
        if options["cap_mode"] == "split":
            if not mapping["chgCap"] or not mapping["dchgCap"]:
                return "분리 모드에서는 충전 용량과 방전 용량을 모두 연결하세요."
            if mapping["chgCap"] == mapping["dchgCap"]:
                return "충전 용량과 방전 용량에 서로 다른 컬럼을 연결하세요."
        else:
            if not mapping["capacity"]:
                return "공통 용량 컬럼을 연결하세요."
            if not mapping["stepType"] and not mapping["current"]:
                return "충전·방전을 구분할 Step 구분 또는 부호가 있는 전류 컬럼을 연결하세요."
        return ""

    def _validate(self, *_):
        if self._updating:
            return
        error = self._validation_error()
        self.error_label.setText(error)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not error)

    def _accept_if_valid(self):
        self._validate()
        if not self._validation_error():
            self.accept()
