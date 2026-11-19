"""Run the preserved Go CLI and compare decoder outputs on synthetic fixtures.

This verifies software equivalence only. It does not validate a PNE instrument.
The Go executable must be built from the unmodified reference source snapshot.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import zipfile

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from studio.demo import generate_demo
from studio.ingestion import load_inputs, PROFILE_VERSION
from test_ingestion import make_pne_bytes

REFERENCE_COMMIT = "13766d27bc73b1e82ef06c7ea259d5609e1d313a"
# Independent published-profile offsets, not imported from the NumPy decoder.
FIELDS = {
    "Index": (0, "Index"), "StepTime_s": (4, "StepTime_s"),
    "CVTime_s": (8, "CVTime_s"), "Voltage_mV": (16, "Voltage_mV"),
    "Current_mA": (20, "Current_mA"), "Capacity_mAh": (24, "Capacity_mAh"),
    "ChargeCapacity_mAh": (32, "ChargeCapacity_mAh"),
    "DischargeCapacity_mAh": (36, "DischargeCapacity_mAh"),
    "Energy_mWh": (40, "Energy_mWh"), "CellTemp_C": (64, "Temp_C"),
    "Imp10s_mOhm": (68, "Imp10s_mOhm"), "Power_mW": (72, "Power_mW"),
    "Imp0_1s_mOhm": (112, "Imp0_1s_mOhm"), "Imp1s_mOhm": (116, "Imp1s_mOhm"),
    "Imp5s_mOhm": (120, "Imp5s_mOhm"), "Imp30s_mOhm": (124, "Imp30s_mOhm"),
    "Imp60s_mOhm": (128, "Imp60s_mOhm"),
    "DateRaw_f32_Offset140_Unverified": (140, "DateRaw_Offset140_Unverified"),
}


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def status(value):
    return "INITIAL" if value == "INITIALIZATION" else value


def compare_case(name, input_path, executable, validation):
    output = validation / "go_reference" / name
    output.mkdir(parents=True, exist_ok=True)
    run = subprocess.run([str(executable), "--cli", "-input", str(input_path),
                          "-output", str(output), "-raw=true", "-pause=false"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    (output / "cli.log").write_text(run.stdout + run.stderr, encoding="utf-8")
    result = {"case": name, "synthetic": True, "input": str(input_path),
              "input_sha256": hashlib.sha256(Path(input_path).read_bytes()).hexdigest(),
              "go_exit_code": run.returncode, "channels": [], "passed": False}
    if run.returncode:
        result["error"] = "Go reference CLI failed; see cli.log"
        return result
    overview = read_csv(output / "PNE_Channels_Summary.csv")
    channels = load_inputs([str(input_path)])
    if len(overview) != len(channels):
        result["error"] = f"channel counts differ: Go={len(overview)}, Python={len(channels)}"
        return result
    with zipfile.ZipFile(input_path) as archive:
        for channel in channels:
            group = str(PurePosixPath(channel.metadata["source_member"]).parent)
            reference = next(row for row in overview if row["ChannelPath"].replace("\\", "/") == group)
            raw = read_csv(output / reference["RawCSV"])
            step_rows = read_csv(output / reference["StepCSV"])
            frame = channel.frame
            measurement = archive.read(channel.metadata["source_member"])
            channel_result = {"channel": channel.name, "go_rows": len(raw), "python_rows": len(frame),
                              "go_steps": len(step_rows), "python_steps": len(channel.steps),
                              "numeric_fields": {}, "metadata_fields": {}, "step_fields": {}}
            passed = len(raw) == len(frame) and len(step_rows) == len(channel.steps)
            if len(raw) == len(frame):
                for go_field, (offset, py_field) in FIELDS.items():
                    go_values = np.array([float(row[go_field]) for row in raw], dtype=np.float64)
                    py_values = frame[py_field].to_numpy(dtype=np.float64)
                    independent = np.array([struct.unpack_from("<f", measurement, 72 + n * 176 + offset)[0]
                                            for n in range(len(frame))], dtype=np.float64)
                    # Go's CSV formatter serializes all values at six decimals.
                    delta = np.abs(go_values - py_values)
                    tolerance = 0.500001e-6 + np.abs(py_values) * np.finfo(np.float64).eps * 2
                    rounded_equal = bool(np.all(delta <= tolerance))
                    exact_binary_equal = bool(np.array_equal(py_values, independent, equal_nan=True))
                    channel_result["numeric_fields"][go_field] = {
                        "go_csv_equal_within_rounding": rounded_equal,
                        "python_equals_independent_f32_exactly": exact_binary_equal,
                        "max_abs_go_csv_difference": float(np.nanmax(delta)) if len(delta) else 0.,
                    }
                    passed &= rounded_equal and exact_binary_equal
                for go_key, py_key in (("MatchedStepNo", "StepNo"), ("CurCycle", "OriginalCycle"),
                                       ("TotalCycle", "TotalCycle"), ("StepType", "StepMode"),
                                       ("StateCode", "StateCode"), ("MatchStatus", "MatchStatus")):
                    go_values = [status(row[go_key]) if go_key == "MatchStatus" else row[go_key] for row in raw]
                    py_values = frame[py_key].astype(str).tolist()
                    mismatches = [i + 1 for i, (a, b) in enumerate(zip(go_values, py_values)) if a != b]
                    channel_result["metadata_fields"][go_key] = {"equal": not mismatches, "mismatched_records": mismatches[:20]}
                    passed &= not mismatches
            if len(step_rows) == len(channel.steps):
                for go_key, py_key in (("FirstRecord", "FirstRecord"), ("LastRecord", "LastRecord"),
                                       ("RecordCount", "RecordCount"), ("StepNo", "StepNo"),
                                       ("CurCycle", "OriginalCycle"), ("TotalCycle", "TotalCycle"),
                                       ("MatchStatus", "MatchStatus")):
                    go_values = [status(row[go_key]) if go_key == "MatchStatus" else row[go_key] for row in step_rows]
                    py_values = channel.steps[py_key].astype(str).tolist()
                    equal = go_values == py_values
                    channel_result["step_fields"][go_key] = {"equal": equal}
                    passed &= equal
            channel_result["status_counts"] = dict(Counter(frame["MatchStatus"].astype(str)))
            channel_result["passed"] = bool(passed)
            result["channels"].append(channel_result)
    result["passed"] = bool(result["channels"] and all(c["passed"] for c in result["channels"]))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--go-exe", default=str(PROJECT / "validation" / "pne_go_reference.exe"))
    arguments = parser.parse_args()
    validation = PROJECT / "validation"
    executable = Path(arguments.go_exe).resolve()
    fixtures = validation / "go_fixture"
    demo_path = Path(generate_demo(fixtures)[0])
    original_fixture = fixtures / "ORIGINAL_Go_synthetic_test_nested.zip"
    if not original_fixture.exists():
        reference_fixture = PROJECT.parent / "review_20261118" / "go_source" / "synthetic_test_nested.zip"
        shutil.copyfile(reference_fixture, original_fixture)
    edge_fixture = fixtures / "SYNTHETIC_initial_cycle_restart.zip"
    cyc, steps = make_pne_bytes(cycles=(1, 2, 1), initial=True)
    with zipfile.ZipFile(edge_fixture, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("추가루트/시험/M02Ch037/data.cyc", cyc)
        archive.writestr("추가루트/시험/M02Ch037/data_StepEnd.csv", steps)
    test_events = []
    test_log = validation / "go_tests.jsonl"
    if test_log.exists():
        for line in test_log.read_text(encoding="utf-8-sig").splitlines():
            try:
                test_events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    test_pass = [event["Test"] for event in test_events if event.get("Action") == "pass" and event.get("Test")]
    test_fail = [event.get("Test", "package") for event in test_events if event.get("Action") == "fail"]
    report = {
        "validation_date": "2026-11-18", "reference_commit": REFERENCE_COMMIT,
        "python_profile_version": PROFILE_VERSION,
        "go_toolchain": "go1.27.4 windows/amd64",
        "go_archive_url": "https://go.dev/dl/go1.27.4.windows-amd64.zip",
        "go_archive_sha256_verified": "178ab98950ac69ab25069307dc6ade79f3816e414cce95b4da166a115cde1e7e",
        "reference_executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "legacy_go_tests": {"passed": len(test_pass), "failed": len(test_fail), "failed_names": test_fail},
        "status_normalization": {"Go INITIALIZATION": "Python INITIAL"},
        "numeric_comparison": "Go CLI exports 6 decimals: absolute bound 0.500001e-6 in raw field units plus double roundoff. Python compared exactly to independent struct.unpack_from('<f') values.",
        "instrument_data_verified": False,
        "cases": [compare_case(name, path, executable, validation) for name, path in (
            ("v3_demo", demo_path), ("original_go_fixture", original_fixture),
            ("initial_cycle_restart", edge_fixture))],
    }
    report["passed"] = bool(all(case["passed"] for case in report["cases"]) and not test_fail)
    (validation / "go_comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    lines = ["# 기존 Go 해독기와 Python 해독기 실행 비교", "", "검증일: 2026-11-18", "",
             f"- 기존 Go 소스 기준 커밋: `{REFERENCE_COMMIT}`", f"- 도구: `{report['go_toolchain']}` (공식 ZIP SHA256 확인, 임시 폴더 실행)",
             f"- 기존 Go 단위 테스트: {len(test_pass)}개 통과 / {len(test_fail)}개 실패", "- 시스템 Go 설치·레지스트리 변경 없이 휴대용 도구를 사용했다.", "",
             "## 실행 결과", "", "| 합성 입력 | 채널 | Raw 행 | Step 구간 | 비교 결과 |", "|---|---:|---:|---:|---|"]
    for case in report["cases"]:
        lines.append(f"| {case['case']} | {len(case['channels'])} | {sum(c['python_rows'] for c in case['channels'])} | {sum(c['python_steps'] for c in case['channels'])} | {'일치' if case['passed'] else '차이 있음'} |")
    lines += ["", "## 비교 항목과 허용오차", "", "- Raw 18개 수치 필드: 전압·전류·용량·에너지·시간·온도·저항·미검증 날짜 후보 등.",
              "- 원본 StepNo·CurCycle·TotalCycle·StepType·StateCode·매칭 상태와 Step별 원본 행 경계를 대조했다.",
              "- Go CSV는 소수점 6자리로 저장한다. Raw 단위에서 반올림 허용오차 0.500001e-6와 부동소수점 변환 오차를 적용했다.",
              "- Python 수치값은 별도의 struct.unpack_from('<f') 파서로 CYC 바이트를 직접 읽은 값과 정확히 비교했다. Go CSV 결과의 일치를 비트 단위 동등성으로 표현하지 않는다.",
              "- Go의 `INITIALIZATION` 표시는 Python의 `INITIAL`과 같은 제외 상태로 정규화하여 비교했다.",
              "- 반복 CurCycle의 사용자 표시 번호는 Python에서 별도 발생 순서로 관리한다. 이 비교에서는 변경하지 않은 원본 CurCycle·TotalCycle을 대조했다.", "",
              "## 검증 범위", "", "**모든 입력은 합성 데이터이다. 실제 PNE 장비 데이터 검증은 수행하지 않았다.**", "",
              "이 결과는 해독·StepEnd 결합의 실행 결과 비교이며, Go와 Qt의 모든 분석식이 동일하다는 의미는 아니다. Cycle 에너지 집계·DCR 휴지 전류 처리 등 분석 정의의 차이는 별도로 관리한다.", "",
              "상세 결과: `validation/go_comparison.json`. 기존 Go 테스트 로그: `validation/go_tests.jsonl`.",
              "재실행: `python validation/compare_go_reference.py --go-exe validation/pne_go_reference.exe`."]
    (PROJECT / "docs" / "go_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "go_tests_passed": len(test_pass),
                      "cases": [{"case": case["case"], "passed": case["passed"], "channels": len(case["channels"])} for case in report["cases"]]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
