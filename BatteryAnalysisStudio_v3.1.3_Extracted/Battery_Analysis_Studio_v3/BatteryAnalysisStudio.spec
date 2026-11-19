# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
root = Path(SPECPATH)
a = Analysis([str(root / 'main.py')], pathex=[str(root)],
    binaries=[], datas=[], hiddenimports=['scipy.signal', 'openpyxl', 'pyqtgraph.exporters', 'studio.demo'],
    excludes=['PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore', 'PySide6.QtQuick',
              'PySide6.QtQml', 'PySide6.QtMultimedia', 'PySide6.QtPdf', 'matplotlib', 'tkinter', 'IPython'],
    noarchive=False)
# Previous baseline failed to start when unrelated Poppler ICU libraries were bundled.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BatteryAnalysisStudio',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BatteryAnalysisStudio')
