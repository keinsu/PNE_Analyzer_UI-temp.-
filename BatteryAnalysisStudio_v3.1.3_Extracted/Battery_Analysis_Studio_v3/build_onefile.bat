@echo off
setlocal
cd /d "%~dp0"
python -m unittest discover -s tests -v
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --distpath dist_onefile --workpath build_onefile BatteryAnalysisStudio_OneFile.spec
if errorlevel 1 exit /b 1
python tests\verify_frozen.py --exe dist_onefile\BatteryAnalysisStudio_v3_OneFile.exe --result validation\onefile_frozen_smoke.json
if errorlevel 1 exit /b 1
echo Verified: dist_onefile\BatteryAnalysisStudio_v3_OneFile.exe
