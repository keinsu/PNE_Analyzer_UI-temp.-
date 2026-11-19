# -*- coding: utf-8 -*-
"""
widgets.py
리포트 화면 전반에서 재사용하는 작은 위젯들.
"""
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QSizePolicy,
    QTableWidget, QAbstractItemView, QApplication
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QKeySequence
from . import theme


class StatCard(QFrame):
    def __init__(self, label: str, value: str = "-", color: str | None = None, small=False, parent=None):
        super().__init__(parent)
        self.setObjectName("readout")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        self.lbl = QLabel(label.upper())
        self.lbl.setProperty("role", "statlbl")
        self.val = QLabel(value)
        self.val.setProperty("role", "statval")
        if small:
            self.val.setStyleSheet("font-size: 16px;")
        if color:
            self.val.setStyleSheet(self.val.styleSheet() + f"color: {color};")
        layout.addWidget(self.lbl)
        layout.addWidget(self.val)

    def set_value(self, value: str, color: str | None = None):
        self.val.setText(value)
        if color:
            base = "font-size: 16px;" if "16px" in self.val.styleSheet() else ""
            self.val.setStyleSheet(base + f"color: {color};")


class CopyableTableWidget(QTableWidget):
    """선택한 직사각형 셀 범위를 TSV로 복사할 수 있는 결과표 위젯.

    Qt의 기본 QTableWidget은 선택 셀 하나만 클립보드에 넣는 환경이 있어, Excel 등에 붙여넣을
    때 사용자가 선택한 범위를 보존하도록 직접 처리한다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selected_range()
            event.accept()
            return
        super().keyPressEvent(event)

    def copy_selected_range(self):
        ranges = self.selectedRanges()
        if not ranges:
            return
        top = min(r.topRow() for r in ranges)
        bottom = max(r.bottomRow() for r in ranges)
        left = min(r.leftColumn() for r in ranges)
        right = max(r.rightColumn() for r in ranges)
        # 데이터 범위를 Excel에 붙여넣을 때 의미가 유지되도록, 선택 범위의 열 제목도 첫 줄에 넣는다.
        headers = []
        for column in range(left, right + 1):
            header = self.horizontalHeaderItem(column)
            headers.append(header.text().replace("\n", " ") if header is not None else "")
        lines = ["\t".join(headers)]
        for row in range(top, bottom + 1):
            values = []
            for column in range(left, right + 1):
                item = self.item(row, column)
                values.append(item.text() if item is not None else "")
            lines.append("\t".join(values))
        QApplication.clipboard().setText("\n".join(lines))


class AxisRangeControl(QWidget):
    """'자동'/'직접' 토글 + min/max 입력. 값이 바뀌면 changed 시그널을 emit."""
    changed = Signal()

    def __init__(self, label: str, default_min="", default_max="", parent=None):
        super().__init__(parent)
        self.mode = "auto"
        self._min = default_min
        self._max = default_max

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        lbl = QLabel(label)
        lbl.setProperty("role", "mono subtle")
        lbl.setStyleSheet(f"color: {theme.SUBTLE}; font-family: '{theme.FONT_MONO}'; font-size: 11.5px;")
        root.addWidget(lbl)

        self.btnAuto = QPushButton("자동")
        self.btnManual = QPushButton("직접")
        for b in (self.btnAuto, self.btnManual):
            b.setFixedHeight(24)
            b.setStyleSheet("padding: 3px 10px; font-size: 11px;")
        self.btnAuto.clicked.connect(lambda: self._set_mode("auto"))
        self.btnManual.clicked.connect(lambda: self._set_mode("manual"))
        root.addWidget(self.btnAuto)
        root.addWidget(self.btnManual)

        self.inpMin = QLineEdit(str(default_min))
        self.inpMax = QLineEdit(str(default_max))
        for inp in (self.inpMin, self.inpMax):
            inp.setFixedWidth(64)
            inp.editingFinished.connect(self._on_value_changed)
        self.tilde = QLabel("~")
        self.tilde.setStyleSheet(f"color: {theme.SUBTLE};")

        root.addWidget(self.inpMin)
        root.addWidget(self.tilde)
        root.addWidget(self.inpMax)
        root.addStretch(1)

        self._update_visual()

    def _set_mode(self, mode):
        self.mode = mode
        self._update_visual()
        self.changed.emit()

    def _on_value_changed(self):
        self._min = self.inpMin.text()
        self._max = self.inpMax.text()
        self.changed.emit()

    def _update_visual(self):
        self.btnAuto.setProperty("active", "true" if self.mode == "auto" else "false")
        self.btnManual.setProperty("active", "true" if self.mode == "manual" else "false")
        for b in (self.btnAuto, self.btnManual):
            b.style().unpolish(b)
            b.style().polish(b)
        show_manual = self.mode == "manual"
        self.inpMin.setVisible(show_manual)
        self.inpMax.setVisible(show_manual)
        self.tilde.setVisible(show_manual)

    def value_range(self):
        """('auto', None, None) 또는 ('manual', float, float) 반환. 파싱 실패시 auto로 취급."""
        if self.mode != "manual":
            return None
        try:
            return (float(self.inpMin.text()), float(self.inpMax.text()))
        except ValueError:
            return None


def parse_cycle_spec(text: str) -> list[int]:
    """
    '10-20' (범위), '10-50:5' (범위+간격), '5,10,15' (콤마 목록)을 조합해서 사이클 번호
    리스트로 파싱한다. 예: '1,5-10,20-40:5' -> [1,5,6,7,8,9,10,20,25,30,35,40]
    잘못된 조각은 무시하고 나머지는 계속 파싱한다. 중복은 제거하고 오름차순 정렬해 반환.
    """
    result = set()
    if not text:
        return []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            range_part, _, step_part = part.partition(":")
            try:
                a_str, b_str = range_part.split("-")
                a, b = int(a_str), int(b_str)
            except ValueError:
                continue
            step = 1
            if step_part:
                try:
                    step = max(1, int(step_part))
                except ValueError:
                    step = 1
            lo, hi = min(a, b), max(a, b)
            result.update(range(lo, hi + 1, step))
        else:
            try:
                result.add(int(part))
            except ValueError:
                continue
    return sorted(result)


def hline():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"background-color: {theme.BORDER}; max-height: 1px;")
    return line


def section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "display")
    lbl.setStyleSheet(f"font-family: '{theme.FONT_DISPLAY}'; font-size: 15px; font-weight: 600;")
    return lbl
