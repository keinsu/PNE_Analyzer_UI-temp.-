# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Battery Cycle Analyzer — native Qt (PySide6 + PyQtGraph) desktop app.
# 브라우저 엔진(WebView2/Chromium)을 전혀 사용하지 않으므로 오프라인 PC에 별도 런타임 설치가 필요 없습니다.
#
# 빌드 방법 (인터넷 되는 Windows PC에서, 한 번만):
#   pip install -r requirements.txt
#   pyinstaller battery_analyzer_qt.spec
#
# 결과물: dist/BatteryAnalyzerQt.exe  (이 파일 하나만 오프라인 PC로 복사하면 됩니다)

import glob
import os

block_cipher = None
ASSETS_DIR = "assets"

datas = []
for path in glob.glob(os.path.join(ASSETS_DIR, "**", "*"), recursive=True):
    if os.path.isfile(path):
        rel_dir = os.path.dirname(path)
        datas.append((path, rel_dir))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "pandas", "numpy", "scipy", "scipy.signal", "openpyxl",
        "pyqtgraph",
        "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # QtWebEngine, QtWebEngineWidgets 등 사용하지 않는 무거운 Qt 모듈은 명시적으로 제외해
    # exe 용량과 빌드 시간을 줄인다 (native Qt widgets만 사용하므로 필요 없음).
    excludes=[
        "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtMultimedia",
        "PySide6.QtNetwork", "PySide6.QtPdf", "PySide6.QtBluetooth",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="BatteryAnalyzerQt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # 콘솔창 없이 GUI만 표시. 문제 진단 시 True로 바꿔서 다시 빌드하면 로그가 보입니다.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,        # 아이콘 파일(.ico)이 있다면 여기에 경로를 지정하세요.
)
