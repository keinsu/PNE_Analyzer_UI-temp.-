"""Run the shipped Windows executable, not the Python entry point."""
import json
from pathlib import Path
import subprocess
import sys
import argparse

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    executable = args.exe or root / 'dist' / 'BatteryAnalysisStudio' / 'BatteryAnalysisStudio.exe'
    if not executable.is_absolute():
        executable = root / executable
    result = args.result or root / 'validation' / 'frozen_smoke.json'
    if not result.is_absolute():
        result = root / result
    result.parent.mkdir(exist_ok=True)
    if result.exists():
        result.unlink()
    completed = subprocess.run([str(executable), '--smoke-test', str(result)], cwd=root,
                               timeout=120, capture_output=True, text=True)
    if completed.returncode != 0 or not result.exists():
        raise SystemExit(f'Frozen executable failed: {completed.returncode}\n{completed.stdout}\n{completed.stderr}')
    payload = json.loads(result.read_text(encoding='utf-8'))
    if payload.get('status') != 'ok' or not payload.get('frozen'):
        raise SystemExit('Smoke result did not confirm frozen application success')
    print(json.dumps(payload, ensure_ascii=False, indent=2))
