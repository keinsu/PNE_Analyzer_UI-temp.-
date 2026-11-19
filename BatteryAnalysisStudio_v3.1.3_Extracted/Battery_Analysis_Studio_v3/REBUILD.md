# Battery Analysis Studio v3.1.3 이관 및 재빌드

ZIP을 풀면 Battery_Analysis_Studio_v3 작업 폴더가 나옵니다. 소스, 의존성, Windows 빌드 설정, 테스트와 사용 안내서를 함께 보관하세요.

## Windows용 단일 실행 파일

Windows x64와 Python 3.13 x64가 필요합니다. 이 패키지는 Python 3.13.5에서 검증합니다. PowerShell에서 압축을 푼 Battery_Analysis_Studio_v3 폴더로 이동한 뒤 다음 순서대로 실행하세요.

    python --version
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    $env:Path = (Resolve-Path '.\.venv\Scripts').Path + ';' + $env:Path
    .\build_onefile.bat

이 스크립트는 자동 테스트, PyInstaller 빌드, 생성된 EXE의 실행 검사를 차례로 수행합니다. 결과는 dist_onefile/BatteryAnalysisStudio_v3_OneFile.exe에 생성됩니다. 폴더형 EXE와 _internal을 만들려면 build_exe.bat를 실행하세요. 소스를 바로 실행하려면 python main.py, 합성 예제는 python main.py --demo를 사용하세요.

실제 Origin 그래프를 PowerPoint에 삽입하려면 해당 Windows PC에 Origin과 데스크톱 PowerPoint가 설치되어 있어야 합니다. 자동 확인은 합성 데이터를 사용합니다.

## Codex Cloud로 옮기기

Cloud에서 사용할 작업 폴더로 압축을 푼 Battery_Analysis_Studio_v3 폴더 전체를 옮기세요. Windows EXE 재빌드 및 실행 검사는 Windows에서 수행합니다. 이 ZIP에는 소스와 설정이 들어 있으며, 회사 측정 데이터, 사용자 시험 기록, EXE, 빌드 결과 폴더는 제외했습니다.

docs/source_manifest.json에서 v3.1.3 패키지 파일들의 SHA-256을 확인할 수 있습니다. 과거 Go 비교 도구 및 문서에는 별도로 보관된 합성 비교용 입력과 Go 실행 파일을 참조하는 경로가 있습니다. package_release.py는 예전 폴더형 배포 ZIP 생성 도구이며, 이 버전의 재빌드에는 위의 배치 파일을 사용합니다.
