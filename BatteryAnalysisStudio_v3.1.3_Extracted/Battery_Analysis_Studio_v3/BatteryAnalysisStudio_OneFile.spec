# -*- mode: python ; coding: utf-8 -*-
"""Standalone, single-EXE distribution.

PyInstaller extracts its bundled runtime to a temporary folder during startup.
The user only needs to keep and run the EXE itself.
"""
from pathlib import Path

root = Path(SPECPATH)
a = Analysis([str(root / 'main.py')], pathex=[str(root)],
    binaries=[], datas=[], hiddenimports=['scipy.signal', 'openpyxl', 'pyqtgraph.exporters', 'studio.demo'],
    excludes=['PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore', 'PySide6.QtQuick',
              'PySide6.QtQml', 'PySide6.QtMultimedia', 'PySide6.QtPdf', 'matplotlib', 'tkinter', 'IPython'],
    noarchive=False)
# Avoid the known Poppler ICU collision that can prevent QtWidgets from loading.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name='BatteryAnalysisStudio_v3_OneFile',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
