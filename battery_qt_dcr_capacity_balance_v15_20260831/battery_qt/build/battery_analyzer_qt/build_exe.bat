@echo off
setlocal
echo ============================================================
echo  Battery Cycle Analyzer (Native Qt) - Windows exe build
echo  Run this once on a PC that has internet access.
echo ============================================================

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    echo         Install Python 3.10+ from https://www.python.org/downloads/
    echo         and make sure "Add python.exe to PATH" is checked during install.
    pause
    exit /b 1
)

echo.
echo [1/3] Installing required packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Package installation failed.
    pause
    exit /b 1
)

echo.
echo [2/3] Cleaning previous build output...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

echo.
echo [3/3] Building exe with PyInstaller (this can take a few minutes)...
python -m PyInstaller battery_analyzer_qt.spec
if errorlevel 1 (
    echo [ERROR] exe build failed. See the log above for details.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done. Check dist\BatteryAnalyzerQt.exe
echo  Copy that single exe file to the offline PC and run it.
echo  (No WebView2 or other runtime install is required.)
echo ============================================================
pause
