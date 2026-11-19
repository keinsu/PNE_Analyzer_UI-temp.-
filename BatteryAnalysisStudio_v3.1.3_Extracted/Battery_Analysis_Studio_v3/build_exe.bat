@echo off
setlocal
cd /d "%~dp0"
python -m unittest discover -s tests -v
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean BatteryAnalysisStudio.spec
if errorlevel 1 exit /b 1
python tests\verify_frozen.py
if errorlevel 1 exit /b 1
echo Verified: dist\BatteryAnalysisStudio\BatteryAnalysisStudio.exe
