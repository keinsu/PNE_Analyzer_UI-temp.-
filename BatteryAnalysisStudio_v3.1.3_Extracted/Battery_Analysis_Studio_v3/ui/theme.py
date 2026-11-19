# -*- coding: utf-8 -*-
"""
theme.py
다크테마 색상 팔레트, 폰트 로딩, 전역 Qt 스타일시트(QSS).
"""
import os
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtCore import Qt

BG = "#10161a"
PANEL = "#161e23"
PANEL_INPUT = "#0d1316"
BORDER = "#2a363e"
BORDER_STRONG = "#3a4750"
TEXT = "#e7eef2"
SUBTLE = "#7e93a1"
TEAL = "#45d8c0"
AMBER = "#f2a950"
CORAL = "#f2665a"
PURPLE = "#b39ddb"
BLUE = "#8fb8de"
ORANGE = "#e67e22"
STEEL = "#2980b9"
PURPLE_DARK = "#8e44ad"
GOLD = "#f39c12"
GREEN = "#16a085"

FONT_MONO = "IBM Plex Mono"
FONT_DISPLAY = "Space Grotesk"
FONT_BODY = "Inter"

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "fonts")


def load_fonts():
    """assets/fonts 안의 ttf 파일들을 앱 전용 폰트로 등록한다 (시스템에 설치하지 않음)."""
    if not os.path.isdir(ASSETS_DIR):
        return
    for fn in os.listdir(ASSETS_DIR):
        if fn.lower().endswith(".ttf"):
            QFontDatabase.addApplicationFont(os.path.join(ASSETS_DIR, fn))


def base_font(size=10) -> QFont:
    f = QFont(FONT_BODY, size)
    return f


def mono_font(size=10, weight=QFont.Weight.Normal) -> QFont:
    f = QFont(FONT_MONO, size)
    f.setWeight(weight)
    return f


def display_font(size=14, weight=QFont.Weight.DemiBold) -> QFont:
    f = QFont(FONT_DISPLAY, size)
    f.setWeight(weight)
    return f


STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: '{FONT_BODY}';
    font-size: 12px;
}}
QMainWindow {{ background-color: {BG}; }}

QFrame#panel {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
}}
QFrame#readout {{
    background-color: {PANEL_INPUT};
    border: 1px solid {BORDER};
    border-radius: 3px;
}}
QFrame#hairline {{
    border: 1px solid {BORDER};
    border-radius: 3px;
}}
QFrame#dropZone {{
    border: 2px dashed {BORDER_STRONG};
    border-radius: 4px;
    background-color: {PANEL};
}}
QFrame#dropZone[dragActive="true"] {{
    border-color: {TEAL};
    background-color: #131a1e;
}}

QLabel {{ background: transparent; }}
QLabel[role="subtle"] {{ color: {SUBTLE}; }}
QLabel[role="mono"] {{ font-family: '{FONT_MONO}'; }}
QLabel[role="display"] {{ font-family: '{FONT_DISPLAY}'; font-weight: 600; }}
QLabel[role="eyebrow"] {{ color: {SUBTLE}; font-family: '{FONT_MONO}'; font-size: 11px; letter-spacing: 2px; }}
QLabel[role="statval"] {{ font-family: '{FONT_MONO}'; font-size: 22px; font-weight: 600; }}
QLabel[role="statlbl"] {{ color: {SUBTLE}; font-size: 10px; }}
QLabel[role="badge"] {{
    font-family: '{FONT_MONO}'; font-size: 10px; color: {SUBTLE};
    border: 1px solid {BORDER_STRONG}; border-radius: 3px; padding: 2px 7px;
}}
QLabel[role="error"] {{ color: {CORAL}; font-family: '{FONT_MONO}'; font-size: 12px; }}

QPushButton {{
    font-family: '{FONT_MONO}'; font-size: 12px;
    padding: 7px 14px; border-radius: 3px; border: 1px solid {BORDER_STRONG};
    background-color: #1c262c; color: {TEXT};
}}
QPushButton:hover {{ border-color: {TEAL}; color: {TEAL}; }}
QPushButton:disabled {{ color: #445056; border-color: {BORDER}; }}
QPushButton[variant="primary"] {{ background-color: {TEAL}; color: #0d1416; font-weight: 600; border-color: {TEAL}; }}
QPushButton[variant="primary"]:hover {{ background-color: #5be8d0; }}
QPushButton[variant="primary"]:disabled {{ background-color: #2a3a3a; color: #5a6a6a; border-color: #2a3a3a; }}
QPushButton[active="true"] {{ border-color: {TEAL}; color: {TEAL}; background-color: #17262a; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    font-family: '{FONT_MONO}'; font-size: 12px;
    background-color: {PANEL_INPUT}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 3px; padding: 5px 7px;
}}
QComboBox:focus, QLineEdit:focus {{ border-color: {TEAL}; }}
QComboBox QAbstractItemView {{
    background-color: {PANEL_INPUT}; color: {TEXT}; selection-background-color: #17262a;
    selection-color: {TEAL}; border: 1px solid {BORDER};
}}

QCheckBox, QRadioButton {{
    font-family: '{FONT_MONO}'; font-size: 11.5px; spacing: 6px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 13px; height: 13px; border: 1px solid {BORDER_STRONG}; background: {PANEL_INPUT};
}}
QRadioButton::indicator {{ border-radius: 7px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background-color: {TEAL}; border-color: {TEAL}; }}

QTableWidget {{
    background-color: {PANEL_INPUT}; color: {TEXT}; gridline-color: {BORDER};
    font-family: '{FONT_MONO}'; font-size: 11px; border: 1px solid {BORDER};
}}
QHeaderView::section {{
    background-color: {PANEL}; color: {SUBTLE}; padding: 5px; border: none;
    border-bottom: 1px solid {BORDER}; font-family: '{FONT_MONO}'; font-size: 11px;
}}
QTableWidget::item:selected {{ background-color: #1a2328; color: {TEAL}; }}

QTabWidget::pane {{
    border: 1px solid {BORDER}; background-color: {PANEL_INPUT}; top: -1px;
}}
QTabBar::tab {{
    background-color: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};
    border-bottom: none; border-top-left-radius: 3px; border-top-right-radius: 3px;
    padding: 8px 16px; margin-right: 3px; font-family: '{FONT_MONO}'; font-size: 12px;
}}
QTabBar::tab:hover {{ background-color: #1c2a30; color: {TEAL}; }}
QTabBar::tab:selected {{ background-color: #1d3031; color: {TEAL}; border-color: {TEAL}; font-weight: 600; }}

QSlider::groove:horizontal {{ background: {BORDER}; height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEAL}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {TEAL}; border-radius: 2px; }}

QScrollBar:vertical {{ background: {BG}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 20px; }}
QScrollBar:horizontal {{ background: {BG}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 20px; }}

QScrollArea {{ border: none; }}
QSplitter::handle {{ background-color: {BORDER}; }}
"""
