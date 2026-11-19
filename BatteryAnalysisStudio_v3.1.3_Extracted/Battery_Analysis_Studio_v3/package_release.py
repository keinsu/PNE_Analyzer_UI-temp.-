"""Create reviewable source and portable Windows ZIPs after frozen validation."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    executable = ROOT / 'dist/BatteryAnalysisStudio/BatteryAnalysisStudio.exe'
    smoke_path = ROOT / 'validation/frozen_smoke.json'
    smoke = json.loads(smoke_path.read_text(encoding='utf-8'))
    if smoke.get('status') != 'ok' or not smoke.get('frozen') or smoke_path.stat().st_mtime < executable.stat().st_mtime:
        raise SystemExit('The current executable must pass tests/verify_frozen.py before packaging.')
    release = ROOT / 'release'
    release.mkdir(exist_ok=True)
    source_files = [path for path in ROOT.rglob('*') if path.is_file()
        and not {'build', 'dist', 'release', '__pycache__', '.cache', '.pytest_cache'}.intersection(path.relative_to(ROOT).parts)
        and path.suffix.lower() not in {'.exe', '.dll', '.pyc', '.pyd', '.partial'}]
    manifest = dict(version='3.0.1', source_reference_commit='d19620572e89fa376f63b24eb09420e169e67b67',
        reference_repository='https://github.com/keinsu/PNE_Analyzer_UI-temp.-',
        baseline_note='Go parser port, Qt backend/UI reused in separate project; original projects preserved.',
        actual_instrument_validation=False,
        files={str(path.relative_to(ROOT)).replace('\\', '/'): sha256(path) for path in source_files
               if path.relative_to(ROOT).parts[0] != 'validation' and path != ROOT / 'docs/source_manifest.json'})
    manifest_path = ROOT / 'docs/source_manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    source_files = sorted(set(source_files + [manifest_path]))
    source_zip = release / 'BatteryAnalysisStudio_v3_Source.zip'
    with zipfile.ZipFile(source_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in source_files:
            archive.write(path, 'Battery_Analysis_Studio_v3/' + path.relative_to(ROOT).as_posix())
    portable_zip = release / 'BatteryAnalysisStudio_v3_Windows_x64.zip'
    with zipfile.ZipFile(portable_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted((ROOT / 'dist/BatteryAnalysisStudio').rglob('*')):
            if path.is_file():
                archive.write(path, 'BatteryAnalysisStudio/' + path.relative_to(ROOT / 'dist/BatteryAnalysisStudio').as_posix())
        archive.write(ROOT / 'README.md', '사용설명서.md')
        for path in (ROOT / 'docs').glob('*.md'):
            archive.write(path, '검증보고서/' + path.name)
        for name in ['frozen_smoke.json', 'source_smoke.json', 'go_comparison.json', 'performance.json', 'unit_tests.log']:
            path = ROOT / 'validation' / name
            if path.exists():
                archive.write(path, '검증결과/' + name)
    result = {path.name: {'bytes': path.stat().st_size, 'sha256': sha256(path)} for path in [source_zip, portable_zip]}
    (release / 'SHA256.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
