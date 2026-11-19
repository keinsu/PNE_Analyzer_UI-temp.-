# -*- coding: utf-8 -*-
"""
home_page.py
프로그램 첫 화면: 어떤 분석 단위 프로그램을 쓸지 선택하는 화면.
Cycle, DCR, dQ/dV 분석을 선택하며 RPT 분석은 준비 중 표시.
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel, QPushButton
from PySide6.QtCore import Qt, Signal
from . import theme


class ProgramCard(QFrame):
    clicked = Signal()

    def __init__(self, emoji: str, title: str, desc: str, enabled: bool = True, badge: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._enabled = enabled
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.setMinimumSize(320, 220)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 28)
        lay.setSpacing(10)

        top_row = QHBoxLayout()
        emoji_lbl = QLabel(emoji)
        emoji_lbl.setStyleSheet("font-size:36px;")
        top_row.addWidget(emoji_lbl)
        top_row.addStretch(1)
        if badge:
            badge_lbl = QLabel(badge)
            badge_lbl.setProperty("role", "badge")
            top_row.addWidget(badge_lbl)
        lay.addLayout(top_row)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-family:'{theme.FONT_DISPLAY}'; font-size:19px; font-weight:700;")
        lay.addWidget(title_lbl)

        desc_lbl = QLabel(desc)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11.5px;")
        lay.addWidget(desc_lbl)
        lay.addStretch(1)

        if not enabled:
            self.setStyleSheet(f"QFrame#panel {{ border: 1px solid {theme.BORDER}; }}")
            emoji_lbl.setStyleSheet("font-size:36px; opacity: 0.5;")
            title_lbl.setStyleSheet(
                f"font-family:'{theme.FONT_DISPLAY}'; font-size:19px; font-weight:700; color:{theme.SUBTLE};"
            )

    def mousePressEvent(self, event):
        if self._enabled:
            self.clicked.emit()
        super().mousePressEvent(event)


class HomePage(QWidget):
    cycleAnalysisSelected = Signal()
    dcrAnalysisSelected = Signal()
    dqdvAnalysisSelected = Signal()
    rptAnalysisSelected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        eyebrow = QLabel("PNE · CYCLE TESTER")
        eyebrow.setProperty("role", "eyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(eyebrow)

        title = QLabel("배터리 분석 프로그램")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-family:'{theme.FONT_DISPLAY}'; font-size:28px; font-weight:700;")
        outer.addWidget(title)

        sub = QLabel("진행할 분석을 선택하세요")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:12px;")
        outer.addSpacing(4)
        outer.addWidget(sub)

        outer.addSpacing(28)

        cards_row = QGridLayout()
        cards_row.setSpacing(20)

        self.cycleCard = ProgramCard(
            "🔋", "Cycle 분석",
            "충방전 사이클 데이터를 분석합니다. 용량/에너지/쿨롱효율, CC/CV 분리, dQ/dV·dV/dQ,"
            " 휴지 전압이완, 온도, 여러 파일 이어붙이기, 여러 셀 비교까지 지원합니다.",
            enabled=True,
        )
        self.cycleCard.clicked.connect(self.cycleAnalysisSelected.emit)
        cards_row.addWidget(self.cycleCard, 0, 0)

        self.dcrCard = ProgramCard(
            "⚡", "DCR 분석",
            "특정 SOC에서 전류 펄스를 인가해 1s/10s/30s 등 원하는 시점의 DCIR(저항)을 계산합니다."
            " 현재 입력 형식은 [저장된 모든 Data] Raw Data이며, SOC 블록/펄스 자동 감지 후 확인·수정하는 단계를 거칩니다.",
            enabled=True,
        )
        self.dcrCard.clicked.connect(self.dcrAnalysisSelected.emit)
        cards_row.addWidget(self.dcrCard, 0, 1)

        self.dqdvCard = ProgramCard(
            "📈", "dQ/dV 분석",
            "장비 전체 CSV에서 dQ/dV만 분석합니다. 사이클·충방전 방향을 선택하고 "
            "보간 포인트 수와 스무딩 조건을 조절해 곡선과 피크를 비교합니다.",
        )
        self.dqdvCard.clicked.connect(self.dqdvAnalysisSelected.emit)
        cards_row.addWidget(self.dqdvCard, 1, 0)

        self.rptCard = ProgramCard(
            "📋", "RPT 분석",
            "Reference Performance Test 분석 (준비 중입니다).",
            enabled=False, badge="준비중",
        )
        self.rptCard.clicked.connect(self._on_rpt_clicked)
        cards_row.addWidget(self.rptCard, 1, 1)

        outer.addLayout(cards_row)
        outer.addStretch(1)

    def _on_rpt_clicked(self):
        pass  # 비활성 카드이므로 클릭이 전달되지 않음 (안내 메시지는 MainWindow에서 처리 가능)
