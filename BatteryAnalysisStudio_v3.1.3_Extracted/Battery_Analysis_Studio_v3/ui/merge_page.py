# -*- coding: utf-8 -*-
"""
merge_page.py
하나의 셀을 여러 파일로 나눠 기록한 경우(중단 후 재시작 등), 파일 순서와 사이클 번호
오프셋을 확인/조정한 뒤 하나로 이어붙이기 위한 확인 화면.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton, QSpinBox,
    QScrollArea
)
from PySide6.QtCore import Qt, Signal
from . import theme
from .widgets import section_title


class MergeFileRow(QFrame):
    moveUp = Signal(object)
    moveDown = Signal(object)
    offsetChanged = Signal(object, int)

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.info = info
        self.setObjectName("hairline")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)

        self.orderLabel = QLabel("")
        self.orderLabel.setStyleSheet(f"color:{theme.TEAL}; font-family:'{theme.FONT_MONO}'; font-size:12px; min-width:24px;")
        lay.addWidget(self.orderLabel)

        name_box = QVBoxLayout()
        self.nameLabel = QLabel(info["name"])
        self.nameLabel.setStyleSheet(f"font-family:'{theme.FONT_MONO}'; font-size:12px; font-weight:600;")
        self.rangeLabel = QLabel("")
        self.rangeLabel.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        name_box.addWidget(self.nameLabel)
        name_box.addWidget(self.rangeLabel)
        lay.addLayout(name_box, 1)

        offset_box = QVBoxLayout()
        offset_lbl = QLabel("사이클 오프셋 (+)")
        offset_lbl.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:10.5px;")
        self.offsetSpin = QSpinBox()
        self.offsetSpin.setRange(-100000, 100000)
        self.offsetSpin.setValue(info.get("offset", 0))
        self.offsetSpin.valueChanged.connect(lambda v: self.offsetChanged.emit(self, v))
        offset_box.addWidget(offset_lbl)
        offset_box.addWidget(self.offsetSpin)
        lay.addLayout(offset_box)

        result_box = QVBoxLayout()
        result_lbl = QLabel("병합 후 Cycle 범위")
        result_lbl.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:10.5px;")
        self.resultLabel = QLabel("")
        self.resultLabel.setStyleSheet(f"color:{theme.TEAL}; font-family:'{theme.FONT_MONO}'; font-size:12px; font-weight:600;")
        result_box.addWidget(result_lbl)
        result_box.addWidget(self.resultLabel)
        lay.addLayout(result_box)

        btn_box = QVBoxLayout()
        self.btnUp = QPushButton("▲")
        self.btnDown = QPushButton("▼")
        for b in (self.btnUp, self.btnDown):
            b.setFixedWidth(32)
            b.setStyleSheet("padding:2px;")
        self.btnUp.clicked.connect(lambda: self.moveUp.emit(self))
        self.btnDown.clicked.connect(lambda: self.moveDown.emit(self))
        btn_box.addWidget(self.btnUp)
        btn_box.addWidget(self.btnDown)
        lay.addLayout(btn_box)

        self.update_labels()

    def update_labels(self):
        mn, mx = self.info["min_cycle"], self.info["max_cycle"]
        self.rangeLabel.setText(f"원본 Cycle {mn}~{mx} · {self.info['row_count']:,}행")
        offset = self.offsetSpin.value()
        self.resultLabel.setText(f"{mn + offset} ~ {mx + offset}")

    def set_order_index(self, i: int):
        self.orderLabel.setText(str(i + 1))


class MergePage(QWidget):
    mergeConfirmed = Signal(list)   # [{"original_index":int, "offset":int}, ...] 최종 순서대로
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[MergeFileRow] = []

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        panel = QFrame()
        panel.setObjectName("panel")
        self.layout_ = QVBoxLayout(panel)
        self.layout_.setContentsMargins(24, 24, 24, 24)
        self.layout_.setSpacing(12)
        scroll.setWidget(panel)

        head_row = QHBoxLayout()
        head_row.addWidget(section_title("여러 파일 이어붙이기 확인"))
        head_row.addStretch(1)
        btnAuto = QPushButton("오프셋 자동 재계산")
        btnAuto.clicked.connect(self._recompute_offsets)
        head_row.addWidget(btnAuto)
        self.layout_.addLayout(head_row)

        desc = QLabel(
            "파일 순서가 실제 기록 순서(시간순)와 같은지 확인하세요. 순서는 ▲▼ 버튼으로 바꿀 수 있고,"
            " 사이클 오프셋은 자동으로 제안되지만 직접 값을 입력해 수정할 수도 있습니다."
            " '병합 후 Cycle 범위'가 서로 겹치지 않고 자연스럽게 이어지는지 확인한 다음 진행하세요."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        self.layout_.addWidget(desc)

        self.rowsLayout = QVBoxLayout()
        self.rowsLayout.setSpacing(8)
        self.layout_.addLayout(self.rowsLayout)

        self.errorLabel = QLabel("")
        self.errorLabel.setProperty("role", "error")
        self.errorLabel.setWordWrap(True)
        self.layout_.addWidget(self.errorLabel)

        btn_row = QHBoxLayout()
        btnCancel = QPushButton("취소하고 새로 업로드")
        btnCancel.clicked.connect(self.cancelRequested.emit)
        self.btnConfirm = QPushButton("병합해서 분석 시작 →")
        self.btnConfirm.setProperty("variant", "primary")
        self.btnConfirm.clicked.connect(self._emit_confirm)
        btn_row.addWidget(btnCancel)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btnConfirm)
        self.layout_.addLayout(btn_row)
        self.layout_.addStretch(1)

    def set_files(self, file_infos: list):
        """file_infos: [{"original_index":int, "name":str, "row_count":int,
                          "min_cycle":int, "max_cycle":int, "offset":int}, ...]  (순서대로 표시)"""
        for r in self.rows:
            r.setParent(None)
        self.rows = []
        for info in file_infos:
            row = MergeFileRow(dict(info))
            row.moveUp.connect(self._move_up)
            row.moveDown.connect(self._move_down)
            row.offsetChanged.connect(self._on_offset_changed)
            self.rowsLayout.addWidget(row)
            self.rows.append(row)
        self._refresh_order_labels()
        self.errorLabel.setText("")

    def set_error(self, msg: str):
        self.errorLabel.setText(msg or "")

    def _refresh_order_labels(self):
        for i, row in enumerate(self.rows):
            row.set_order_index(i)
            row.update_labels()

    def _move_up(self, row: MergeFileRow):
        idx = self.rows.index(row)
        if idx == 0:
            return
        self.rows[idx - 1], self.rows[idx] = self.rows[idx], self.rows[idx - 1]
        self.rowsLayout.removeWidget(row)
        self.rowsLayout.insertWidget(idx - 1, row)
        self._refresh_order_labels()

    def _move_down(self, row: MergeFileRow):
        idx = self.rows.index(row)
        if idx == len(self.rows) - 1:
            return
        self.rows[idx + 1], self.rows[idx] = self.rows[idx], self.rows[idx + 1]
        self.rowsLayout.removeWidget(row)
        self.rowsLayout.insertWidget(idx + 1, row)
        self._refresh_order_labels()

    def _on_offset_changed(self, row: MergeFileRow, value: int):
        row.info["offset"] = value
        row.update_labels()

    def _recompute_offsets(self):
        running_max = None
        for row in self.rows:
            mn, mx = row.info["min_cycle"], row.info["max_cycle"]
            if running_max is None:
                offset = 0
            elif mn <= running_max:
                offset = running_max - mn + 1
            else:
                offset = 0
            row.offsetSpin.blockSignals(True)
            row.offsetSpin.setValue(offset)
            row.offsetSpin.blockSignals(False)
            row.info["offset"] = offset
            row.update_labels()
            running_max = mx + offset

    def _emit_confirm(self):
        result = [{"original_index": r.info["original_index"], "offset": r.offsetSpin.value()} for r in self.rows]
        self.mergeConfirmed.emit(result)
