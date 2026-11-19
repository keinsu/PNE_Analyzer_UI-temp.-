"""Read-only PNE CYC/StepEnd and equipment CSV import.

The 72/176-byte layout is an experimentally validated profile, not a vendor
specification. Alignment and numerical plausibility are checked before decoding.
Go v2.6 matching costs and confidence thresholds are preserved explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
import math
import os
import re
import uuid
import zipfile

import numpy as np
import pandas as pd

from backend.mapping_loader import guess_mapping, build_df_from_mapping, _parse_time_value
from .models import ChannelData
from .raw_store import RawStore

PROFILE_VERSION = "pne72-176-v3.0.8"
HEADER_BYTES, RECORD_BYTES = 72, 176
OFFSETS = {"Index": 0, "StepTime_s": 4, "CVTime_s": 8,
           "Voltage_mV": 16, "Current_mA": 20, "Capacity_mAh": 24,
           "ChargeCapacity_mAh": 32, "DischargeCapacity_mAh": 36,
           "Energy_mWh": 40, "Temp_C": 64, "Imp10s_mOhm": 68,
           "Power_mW": 72, "Imp0_1s_mOhm": 112, "Imp1s_mOhm": 116,
           "Imp5s_mOhm": 120, "Imp30s_mOhm": 124, "Imp60s_mOhm": 128,
           "DateRaw_Offset140_Unverified": 140}
_DTYPE = np.dtype({"names": list(OFFSETS), "formats": ["<f4"] * len(OFFSETS),
                   "offsets": list(OFFSETS.values()), "itemsize": RECORD_BYTES})


@dataclass
class _Source:
    container: str
    member: str
    zipped: bool = False

    @property
    def name(self):
        return PurePosixPath(self.member).name

    @property
    def group(self):
        return str(PurePosixPath(self.member).parent)

    @property
    def reference(self):
        return self.container + ("::" + self.member if self.zipped else "")

    def read(self, cancel=None):
        def read_stream(stream):
            chunks = []
            while True:
                _check_cancel(cancel)
                chunk = stream.read(4 * 1024**2)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        if self.zipped:
            with zipfile.ZipFile(self.container) as archive:
                with archive.open(self.member) as stream:
                    return read_stream(stream)
        with open(self.container, "rb") as stream:
            return read_stream(stream)


def _check_cancel(cancel):
    if cancel and cancel():
        raise InterruptedError("분석을 취소했습니다.")


def _natural_key(value):
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", str(value))]


def _discover(path: Path, cancel=None):
    _check_cancel(cancel)
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.is_dir():
        result = []
        for current, dirs, names in os.walk(path, followlinks=False):
            _check_cancel(cancel)
            dirs[:] = [d for d in dirs if not Path(current, d).is_symlink()]
            for name in names:
                file = Path(current, name)
                if file.suffix.lower() in {".cyc", ".csv", ".tsv"} and not file.is_symlink():
                    result.append(_Source(str(file.resolve()), file.relative_to(path).as_posix()))
        return result
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 100000:
                raise ValueError("ZIP 항목 수가 지원 범위를 초과합니다.")
            result = []
            for item in archive.infolist():
                _check_cancel(cancel)
                member = item.filename
                norm = member.replace("\\", "/")
                parts = norm.split("/")
                if item.is_dir() or norm.startswith("/") or ":" in norm or any(p in {"..", ".", ""} for p in parts):
                    continue
                if PurePosixPath(norm).suffix.lower() in {".cyc", ".csv", ".tsv"}:
                    if item.file_size > 8 * 1024**3:
                        raise ValueError(f"ZIP 항목이 너무 큽니다: {member}")
                    result.append(_Source(str(path.resolve()), member, True))
            return result
    if path.suffix.lower() == ".cyc":
        siblings = [_Source(str(p.resolve()), p.name) for p in path.parent.iterdir()
                    if p.is_file() and (p == path or (p.suffix.lower() == ".csv" and "stepend" in p.stem.casefold()))]
        return siblings
    if path.suffix.lower() in {".csv", ".tsv"}:
        return [_Source(str(path.resolve()), path.name)]
    raise ValueError(f"지원하지 않는 입력입니다: {path.name}")


def _text(data: bytes):
    encodings = ["utf-16"] if data[:2] in {b"\xff\xfe", b"\xfe\xff"} else ["utf-8-sig", "cp949"]
    for encoding in encodings:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("UTF-8/CP949/UTF-16으로 해독할 수 없는 CSV입니다.")


def _normal(value):
    return re.sub(r"[\s_\-()\[\]]", "", str(value).lstrip("\ufeff").lower())


def _number(value):
    try:
        result = float(str(value).replace(",", "").strip())
        return result if math.isfinite(result) else math.nan
    except (ValueError, TypeError):
        return math.nan


def _direction(value, current=0.0):
    value = str(value).casefold()
    if any(key in value for key in ("discharge", "dchg", "방전")):
        return "DISCHARGE"
    if any(key in value for key in ("charge", "chg", "충전")):
        return "CHARGE"
    if any(key in value for key in ("rest", "휴지", "pause", "대기")):
        return "REST"
    if math.isfinite(current) and abs(current) > 0.01:
        return "CHARGE" if current > 0 else "DISCHARGE"
    return "REST"


def _read_steps(data):
    text, encoding = _text(data)
    lines = text.splitlines()
    header = next((i for i, line in enumerate(lines[:101])
                   if "stepno" in _normal(line) and "voltage" in _normal(line)), None)
    if header is None:
        raise ValueError("StepEnd CSV에서 StepNo/Voltage 헤더를 찾지 못했습니다.")
    sep = max((",", "\t", ";"), key=lambda s: lines[header].count(s))
    raw = pd.read_csv(io.StringIO("\n".join(lines[header:])), sep=sep, dtype=str, keep_default_na=False)
    columns = {_normal(c): c for c in raw.columns}

    def get(row, *keys):
        for key in keys:
            if _normal(key) in columns:
                return row[columns[_normal(key)]]
        return ""

    result = []
    for _, row in raw.iterrows():
        no = str(get(row, "StepNo")).strip()
        if not no or no.casefold() == "stepno":
            continue
        result.append({"StepNo": no, "StepMode": get(row, "StepType"),
                       "StateCode": get(row, "StateCode"), "OriginalCycle": get(row, "CurCycle", "Cycle", "TotalCycle", "TotCycle"),
                       "TotalCycle": get(row, "TotalCycle", "TotCycle"),
                       "StartTime": get(row, "StartTime"), "EndTime": get(row, "EndTime"),
                       "seconds": float(_parse_time_value(get(row, "StepTime"))),
                       "voltage": _number(get(row, "Voltage")),
                       "current": _number(get(row, "Current")),
                       "capacity": _number(get(row, "Capacity")),
                       "energy": _number(get(row, "WattHour", "Energy"))})
    if not result:
        raise ValueError("StepEnd 데이터 행이 없습니다.")
    return pd.DataFrame(result), encoding


def _cost(end, row):
    vals = [(end["StepTime_s"], row["seconds"], max(1.2, .01 * max(row["seconds"], 1)), 4),
            (end["Voltage_mV"] / 1000, row["voltage"], .025, 4),
            (end["Current_mA"] / 1000, row["current"], max(.3, abs(row["current"]) * .07), 3),
            (end["Capacity_mAh"] / 1000, row["capacity"], max(.012, abs(row["capacity"]) * .04), 3)]
    differences, score, count = [], 0.0, 0
    for actual, expected, norm, ceiling in vals:
        if math.isfinite(expected):
            delta = abs(float(actual) - expected)
            differences.append(delta)
            score += min(ceiling, delta / norm)
            count += 1
        else:
            differences.append(math.nan)
    score = 9.0 if count == 0 else score + (2.0 if count < 3 else 0.0)
    return score, differences


def _match_segments(records, segments, steps, cancel=None):
    n, m = len(segments), len(steps)
    assigned = {}
    if not m:
        return assigned, "StepEnd 없음"
    if n * m > 5_000_000:
        rows = steps.to_dict("records")
        cursor = 0
        for i, segment in enumerate(segments):
            _check_cancel(cancel)
            if segment["MatchStatus"] == "INITIAL":
                continue
            choices = [(_cost(records[segment["end"] - 1], rows[j])[0], j)
                       for j in range(cursor, min(m, cursor + 6))]
            if choices:
                score, j = min(choices)
                if score < 7.5:
                    assigned[i] = j
                    cursor = j + 1
        return assigned, "대규모 Step 매칭은 Go와 같은 근접 순서 매칭으로 대체했습니다."
    ptr = np.zeros((n + 1, m + 1), dtype=np.uint8)
    ptr[1:, 0] = 1
    ptr[0, 1:] = 2
    previous = np.arange(m + 1, dtype=np.float32) * 4
    expected = steps[["seconds", "voltage", "current", "capacity"]].to_numpy(dtype=np.float64)
    seconds, _, amps, capacity = expected.T
    normalizers = np.column_stack((np.maximum(1.2, .01 * np.maximum(seconds, 1)),
                                   np.full(m, .025), np.maximum(.3, np.abs(amps) * .07),
                                   np.maximum(.012, np.abs(capacity) * .04)))
    valid = np.isfinite(expected)
    counts = valid.sum(axis=1)
    # Match the scalar cost and preserve the previous float32 accumulation
    # and transition priority when candidates tie.
    safe_expected = np.where(valid, expected, 0.)
    normalizers = np.where(valid, normalizers, 1.)
    for i, segment in enumerate(segments, 1):
        _check_cancel(cancel)
        current = np.empty(m + 1, dtype=np.float32)
        current[0] = i * 4
        end = records[segment["end"] - 1]
        if segment["MatchStatus"] == "INITIAL":
            diagonal = previous[:-1].astype(np.float64) + 1e6
        else:
            actual = [end["StepTime_s"], end["Voltage_mV"] / 1000,
                      end["Current_mA"] / 1000, end["Capacity_mAh"] / 1000]
            terms = np.where(valid, np.fmin([4, 4, 3, 3],
                             np.abs(actual - safe_expected) / normalizers), 0.)
            costs = terms[:, 0] + terms[:, 1] + terms[:, 2] + terms[:, 3]
            costs = np.where(counts == 0, 9., costs + 2. * (counts < 3))
            diagonal = previous[:-1].astype(np.float64) + costs
        up = previous[1:].astype(np.float64) + 4
        actions = ptr[i]
        for j, (best, above) in enumerate(zip(diagonal.tolist(), up.tolist()), 1):
            from_left = float(current[j - 1]) + 4
            action = 0
            if above < best:
                best, action = above, 1
            if from_left < best:
                best, action = from_left, 2
            current[j] = best
            actions[j] = action
        previous = current
    i, j = n, m
    while i or j:
        action = ptr[i, j]
        if action == 0:
            assigned[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif action == 1:
            i -= 1
        else:
            j -= 1
    return assigned, ""


def _cycle_ids(original, total=None):
    """Keep original numbering where possible; repeated occurrences get unique IDs."""
    values, max_id, previous_key, current = [], -1, None, -1
    totals = total if total is not None else [""] * len(original)
    for raw, tot in zip(original, totals):
        raw_number = _number(raw)
        if not math.isfinite(raw_number):
            values.append(-1)
            continue
        if raw_number < 0 or abs(raw_number - round(raw_number)) > 1e-9:
            raise ValueError("Cycle 번호는 0 이상의 정수여야 합니다. 원본 번호를 임의로 바꾸지 않았습니다.")
        total_number = _number(tot)
        key = (int(raw_number), total_number if math.isfinite(total_number) else str(tot))
        if key != previous_key:
            requested = int(raw_number)
            current = requested if requested > max_id else max_id + 1
            max_id = max(max_id, current)
            previous_key = key
        values.append(current)
    return np.asarray(values, dtype=np.int64)


def _decode_pne(cyc_data, csv_data, source, csv_source, cancel=None):
    if len(cyc_data) <= HEADER_BYTES or (len(cyc_data) - HEADER_BYTES) % RECORD_BYTES:
        raise ValueError("CYC가 72B 헤더/176B 레코드 프로필과 일치하지 않습니다. 임의 해독하지 않았습니다.")
    records = np.frombuffer(cyc_data, dtype=_DTYPE, offset=HEADER_BYTES)
    _check_cancel(cancel)
    time, voltage, current = records["StepTime_s"], records["Voltage_mV"], records["Current_mA"]
    plausible = (np.isfinite(time) & (time >= 0) & (time < 1e9) & np.isfinite(voltage)
                 & (voltage > -1000) & (voltage < 15000) & np.isfinite(current) & (np.abs(current) < 1e7))
    initial = len(records) > 1 and not plausible[0] and plausible[1]
    if not np.all(plausible[1:] if initial else plausible):
        raise ValueError("CYC에 프로필과 맞지 않는 시간/전압/전류 값이 있습니다. 지원 형식을 확인하세요.")
    if not np.any((voltage[1:] if initial else voltage) > 100):
        raise ValueError("CYC에 유효한 셀 전압을 확인하지 못했습니다.")
    thresholds = np.maximum(.02, time[:-1] * .00001)
    resets = np.flatnonzero((time[1:] + thresholds < time[:-1]) & (time[:-1] > .05)) + 1
    if initial and (not len(resets) or resets[0] != 1):
        resets = np.concatenate(([1], resets))
    edges = np.concatenate(([0], resets, [len(records)]))
    segments = [{"start": int(a), "end": int(b), "MatchStatus": "INITIAL" if initial and a == 0 else "UNMATCHED"}
                for a, b in zip(edges[:-1], edges[1:])]
    if len(segments) > 400000:
        raise ValueError("CYC Step 구간이 비정상적으로 많습니다.")
    warnings = ["PNE 72/176B 해독은 역분석 프로필입니다. 실제 장비 결과와 검증이 필요합니다."]
    if csv_data is not None:
        steps, encoding = _read_steps(csv_data)
    else:
        steps, encoding = pd.DataFrame(), ""
        warnings.append("StepEnd CSV 연결이 없어 Cycle/Step 정량분석에서 제외됩니다.")
    assigned, note = _match_segments(records, segments, steps, cancel)
    if note and csv_data is not None:
        warnings.append(note)
    frame = pd.DataFrame({name: records[name].astype(np.float64) for name in OFFSETS}, copy=False)
    frame["Voltage_V"] = frame["Voltage_mV"] / 1000
    frame["_row"] = np.arange(len(frame), dtype=np.int64)
    total_time = np.full(len(records), np.nan)
    state_codes = []
    step_rows, elapsed = [], 0.0
    used_csv = set()
    for i, segment in enumerate(segments):
        _check_cancel(cancel)
        a, b = segment["start"], segment["end"]
        end = records[b - 1]
        status, row, score, differences = segment["MatchStatus"], None, math.nan, [math.nan] * 4
        if i in assigned and status != "INITIAL":
            j = assigned[i]
            used_csv.add(j)
            row = steps.iloc[j]
            score, differences = _cost(end, row)
            dt, dv, di, dq = differences
            tests = [not math.isfinite(dt) or dt <= max(2.0, .015 * float(end["StepTime_s"])),
                     not math.isfinite(dv) or dv <= .02,
                     not math.isfinite(di) or di <= max(.25, abs(float(end["Current_mA"]) / 1000) * .05),
                     not math.isfinite(dq) or dq <= max(.012, abs(float(end["Capacity_mAh"]) / 1000) * .04)]
            status = "MATCHED" if score <= 2.4 and all(tests) else "REVIEW"
        mode = str(row["StepMode"]) if row is not None else ""
        direction = _direction(mode, float(end["Current_mA"]))
        state_codes.append(str(row["StateCode"]) if row is not None else "")
        if status != "INITIAL":
            total_time[a:b] = elapsed + time[a:b].astype(np.float64)
            elapsed += max(0, float(end["StepTime_s"]))
        step_rows.append({"PhysicalStepID": str(i + 1), "FirstRecord": a + 1, "LastRecord": b,
                          "RecordCount": b - a, "StepNo": str(row["StepNo"]) if row is not None else "",
                          "OriginalCycle": str(row["OriginalCycle"]) if row is not None else "",
                          "TotalCycle": str(row["TotalCycle"]) if row is not None else "",
                          "StepMode": mode, "TypeStd": direction, "MatchStatus": status,
                          "StepTime_s": float(end["StepTime_s"]), "Voltage_V": float(end["Voltage_mV"]) / 1000,
                          "Current_mA": float(end["Current_mA"]), "Capacity_mAh": float(end["Capacity_mAh"]),
                          "Energy_mWh": float(end["Energy_mWh"]), "MatchCost": score,
                          "DeltaTime_s": differences[0], "DeltaVoltage_V": differences[1],
                          "DeltaCurrent_A": differences[2], "DeltaCapacity_Ah": differences[3]})
    step_frame = pd.DataFrame(step_rows)
    step_frame["Cycle"] = _cycle_ids(step_frame["OriginalCycle"], step_frame["TotalCycle"])
    lengths = step_frame["RecordCount"].to_numpy()
    for name in ("OriginalCycle", "TotalCycle", "StepNo", "StepMode"):
        frame[name] = np.repeat(step_frame[name].to_numpy(), lengths)
    frame["StateCode"] = np.repeat(np.asarray(state_codes, dtype=object), lengths)
    frame["TypeStd"] = np.repeat(step_frame["TypeStd"].to_numpy(), lengths)
    frame["Source_File"] = source.reference
    for name in ("MatchStatus", "PhysicalStepID"):
        frame[name] = np.repeat(step_frame[name].to_numpy(), lengths)
    frame["TotalTime_s"] = total_time
    frame["Cycle"] = np.repeat(step_frame["Cycle"].to_numpy(), lengths)
    counts = step_frame["MatchStatus"].value_counts().to_dict()
    for status in ("INITIAL", "REVIEW", "UNMATCHED"):
        if counts.get(status):
            warnings.append(f"{status} 구간 {counts[status]}개: 원본은 보존하며 기본 정량분석에서 제외됩니다.")
    orphan = len(steps) - len(used_csv)
    if orphan:
        warnings.append(f"매칭되지 않은 StepEnd 행 {orphan}개")
    metadata = {"input_type": "PNE", "profile": PROFILE_VERSION, "stepend_source": csv_source.reference if csv_source else "",
                "stepend_encoding": encoding, "original_count": len(frame), "status_counts": counts,
                "orphan_stepend_rows": orphan, "header_bytes": HEADER_BYTES, "record_bytes": RECORD_BYTES}
    return frame, step_frame, metadata, warnings


def _read_raw_csv(data, source, options=None):
    options = options or {}
    text, encoding = _text(data)
    lines = text.splitlines()
    if "skip_lines" in options:
        header = int(options["skip_lines"])
    else:
        header = next((i for i, line in enumerate(lines[:101])
                       if ("voltage" in line.casefold() or "전압" in line)
                       and ("cycle" in line.casefold() or "사이클" in line or "싸이클" in line)), None)
        if header is None:
            raise ValueError("CSV 헤더를 자동으로 찾지 못했습니다. Cycle·Voltage 컬럼 매핑을 지정하세요.")
    if header < 0 or header >= len(lines):
        raise ValueError("CSV 헤더 위치가 범위를 벗어났습니다.")
    sep = options.get("separator") or max((",", "\t", ";"), key=lambda s: lines[header].count(s))
    raw = pd.read_csv(io.StringIO("\n".join(lines[header:])), sep=sep)
    raw.columns = raw.columns.astype(str)
    guessed = guess_mapping(raw.columns.tolist())
    mapping = dict(options.get("mapping") or guessed["mapping"])
    if not mapping.get("stepType"):
        mapping["stepType"] = next((c for c in raw.columns if _normal(c) in {"stepmode", "typestd", "state"}), "")
    if not mapping.get("stepType") and mapping.get("current"):
        currents = pd.to_numeric(raw[mapping["current"]], errors="coerce")
        raw["__StudioDirection"] = np.where(currents > .01, "CHARGE", np.where(currents < -.01, "DISCHARGE", "REST"))
        mapping["stepType"] = "__StudioDirection"
    source_mapping = {key: value for key, value in mapping.items() if value != "__StudioDirection"}
    overrides = options.get("unit_overrides", {})
    if overrides.get("voltage") in {"V", "mV"} and mapping.get("voltage"):
        factor = .001 if overrides["voltage"] == "mV" else 1.0
        raw["__StudioVoltage_V"] = pd.to_numeric(raw[mapping["voltage"]], errors="coerce") * factor
        mapping["voltage"] = "__StudioVoltage_V"
    if overrides.get("energy") in {"Wh", "mWh"} and mapping.get("energy"):
        factor = 1000.0 if overrides["energy"] == "Wh" else 1.0
        raw["__StudioEnergy_mWh"] = pd.to_numeric(raw[mapping["energy"]], errors="coerce") * factor
        mapping["energy"] = "__StudioEnergy_mWh"
    cap_mode = options.get("cap_mode", options.get("capMode", guessed["capMode"]))
    # The established mapping pipeline expects comma-separated text.
    normalized_text = raw.to_csv(index=False)
    # Keep original record positions through legacy filtering. Without these,
    # excluded or malformed rows would be compressed into adjacent samples and
    # later interpolation could bridge a hole in the instrument record.
    source_mask = pd.Series(True, index=raw.index)
    if mapping.get("stepNo") and options.get("exclude_steps"):
        source_mask &= ~raw[mapping["stepNo"]].astype(str).isin({str(x) for x in options["exclude_steps"]})
    if mapping.get("cycle") and options.get("exclude_cycles"):
        source_mask &= ~raw[mapping["cycle"]].astype(str).isin({str(x) for x in options["exclude_cycles"]})
    if mapping.get("cycle") and mapping.get("voltage"):
        source_cycle = pd.to_numeric(raw[mapping["cycle"]], errors="coerce")
        source_voltage = pd.to_numeric(raw[mapping["voltage"]], errors="coerce")
        finite_cycle = source_cycle.notna()
        if ((source_cycle[finite_cycle] < 0) | (source_cycle[finite_cycle] % 1 != 0)).any():
            raise ValueError("CSV Cycle 번호는 0 이상의 정수여야 합니다.")
        source_mask &= finite_cycle & source_voltage.notna()
    source_rows = np.flatnonzero(source_mask.to_numpy())
    frame = build_df_from_mapping(normalized_text, 0, mapping, cap_mode,
                                  {str(x) for x in options.get("exclude_steps", [])},
                                  {str(x) for x in options.get("exclude_cycles", [])},
                                  options.get("unit_overrides", {}))
    if frame.empty:
        raise ValueError("유효한 CSV 측정 데이터가 없습니다.")
    if len(source_rows) != len(frame):
        raise ValueError("CSV 원본 행과 표준화 결과를 정확히 대응하지 못했습니다. 매핑 조건을 확인하세요.")
    frame["_row"] = source_rows
    finite_voltage = np.isfinite(frame["Voltage_V"].to_numpy(dtype=float))
    if not finite_voltage.all():
        frame = frame.loc[finite_voltage].copy()
    if frame.empty:
        raise ValueError("유효한 전압 측정값이 없습니다.")
    metadata = dict(frame.attrs)
    warnings = []
    if len(frame) != len(raw):
        warnings.append(f"Cycle/Voltage 해석 또는 사용자 제외 조건으로 {len(raw) - len(frame)}행이 분석에서 제외됐습니다.")
    frame["Source_File"] = source.reference
    frame["OriginalCycle"] = frame["Cycle"].astype(str)
    total_col = next((c for c in raw.columns if _normal(c) in {"totalcycle", "totcycle"}), None)
    # Preserve TotalCycle when normalization did not filter raw rows.
    frame["TotalCycle"] = raw.loc[frame["_row"], total_col].astype(str).to_numpy() if total_col else ""
    for name in ("StepTime_s", "Current_mA", "Energy_mWh", "Temp_C"):
        if name not in frame:
            frame[name] = np.nan
    if "StepNo" not in frame:
        frame["StepNo"] = ""
    frame["StepNo"] = frame["StepNo"].astype(str)
    if "StepMode" not in frame:
        frame["StepMode"] = frame["TypeStd"]
    # CC/CP is a control mode, not a charge direction. Resolve only unspecified
    # active directions by signed current; explicit Rest stays Rest.
    if cap_mode != "split" and mapping.get("stepType"):
        mode = frame["StepMode"].astype(str)
        unknown_active = mode.str.fullmatch(r"(?i)\s*(?:CC|CP|CV|constant\s+(?:current|power|voltage))\s*")
        frame.loc[unknown_active & (frame["Current_mA"] > .01), "TypeStd"] = "CHARGE"
        frame.loc[unknown_active & (frame["Current_mA"] < -.01), "TypeStd"] = "DISCHARGE"
    if "CVTime_s" not in frame:
        frame["CVTime_s"] = np.nan
    frame["Cycle"] = _cycle_ids(frame["OriginalCycle"], frame["TotalCycle"])
    new_step = (frame["StepNo"].ne(frame["StepNo"].shift()) | frame["Cycle"].ne(frame["Cycle"].shift())
                | frame["TypeStd"].ne(frame["TypeStd"].shift()) | (frame["StepTime_s"].diff() < -1e-9))
    frame["PhysicalStepID"] = new_step.cumsum().astype(str)
    frame["MatchStatus"] = "CSV"
    elapsed = 0.0
    frame["TotalTime_s"] = np.nan
    rows = []
    for step, group in frame.groupby("PhysicalStepID", sort=False):
        first, last = group.iloc[0], group.iloc[-1]
        frame.loc[group.index, "TotalTime_s"] = group["StepTime_s"] + elapsed
        valid_time = group["StepTime_s"].dropna()
        if len(valid_time):
            elapsed += max(0, float(valid_time.max()))
        rows.append({"PhysicalStepID": step, "FirstRecord": int(first["_row"]) + 1,
                     "LastRecord": int(last["_row"]) + 1, "RecordCount": len(group),
                     **{key: last[key] for key in ("Cycle", "OriginalCycle", "TotalCycle", "StepNo", "StepMode", "TypeStd",
                                                    "MatchStatus", "Voltage_V", "Current_mA", "Capacity_mAh", "Energy_mWh", "StepTime_s")}})
    metadata.update({"input_type": "CSV", "encoding": encoding, "original_count": len(raw),
                     "mapping": source_mapping, "cap_mode": cap_mode, "header_line": header,
                     "csv_options": {"mapping": source_mapping,
                                     "cap_mode": cap_mode, "skip_lines": header,
                                     "separator": sep,
                                     "unit_overrides": options.get("unit_overrides", {}),
                                     "exclude_steps": list(options.get("exclude_steps", [])),
                                     "exclude_cycles": list(options.get("exclude_cycles", []))},
                     "status_counts": {"CSV": len(rows)}})
    return frame, pd.DataFrame(rows), metadata, warnings


def _compact_frame(frame):
    """Compact repeated labels without reducing measurement precision.

    Small frames stay unchanged; high-cardinality source labels stay strings.
    Categorical codes substantially reduce repeated per-row provenance labels.
    """
    if len(frame) < 32:
        return frame
    for name in ("Source_File", "PhysicalStepID", "StepNo", "StepMode", "MatchStatus",
                 "TypeStd", "OriginalCycle", "TotalCycle", "StateCode"):
        if name not in frame or isinstance(frame[name].dtype, pd.CategoricalDtype):
            continue
        count = frame[name].nunique(dropna=False)
        if count <= max(32, len(frame) // 4):
            original_bytes = frame[name].memory_usage(index=False, deep=True)
            categorical = frame[name].astype("category")
            if categorical.memory_usage(index=False, deep=True) < original_bytes:
                frame[name] = categorical
    return frame


def _cache_read(cache_dir, key):
    if not cache_dir:
        return None
    base = Path(cache_dir) / key
    try:
        manifest = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
        if manifest["version"] != PROFILE_VERSION:
            return None
        with np.load(base.with_suffix(".npz"), allow_pickle=False) as arrays:
            tables = []
            for table in ("frame", "steps"):
                columns = {}
                for i, (name, kind) in enumerate(manifest[table]):
                    array = arrays[f"{table}_{i}"]
                    if kind == "category_codes":
                        columns[name] = pd.Categorical.from_codes(array, arrays[f"{table}_{i}_labels"])
                    elif kind == "category":
                        columns[name] = pd.Categorical(array)
                    else:
                        columns[name] = array.astype(bool) if kind == "bool" else array
                tables.append(pd.DataFrame(columns))
        return tables[0], tables[1], manifest["metadata"], manifest["warnings"]
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


def _cache_write(cache_dir, key, result, cancel=None):
    if not cache_dir:
        return
    frame, steps, metadata, warnings = result
    root = Path(cache_dir)
    base = root / key
    staging = root / (key + "-" + uuid.uuid4().hex)
    npz_temp = staging.with_suffix(".npz")
    json_temp = staging.with_suffix(".json")
    try:
        root.mkdir(parents=True, exist_ok=True)
        manifest = {"version": PROFILE_VERSION, "metadata": metadata, "warnings": warnings}
        with zipfile.ZipFile(npz_temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            def write_array(name, array):
                _check_cancel(cancel)
                with archive.open(f"{name}.npy", "w", force_zip64=True) as entry:
                    np.lib.format.write_array(entry, array, allow_pickle=False)

            for table, data in (("frame", frame), ("steps", steps)):
                spec = []
                for i, col in enumerate(data.columns):
                    series = data[col]
                    entry = f"{table}_{i}"
                    if isinstance(series.dtype, pd.CategoricalDtype):
                        kind = "category_codes"
                        write_array(f"{entry}_labels", series.cat.categories.to_numpy(dtype=str))
                        array = series.cat.codes.to_numpy()
                    elif pd.api.types.is_numeric_dtype(series.dtype):
                        kind = "bool" if pd.api.types.is_bool_dtype(series.dtype) else "number"
                        array = series.to_numpy()
                    else:
                        kind = "string"
                        array = series.astype(object).where(series.notna(), "").to_numpy(dtype=str)
                    write_array(entry, array)
                    spec.append((str(col), kind))
                manifest[table] = spec
        _check_cancel(cancel)
        json_temp.write_text(json.dumps(manifest, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(npz_temp, base.with_suffix(".npz"))
        os.replace(json_temp, base.with_suffix(".json"))
    except OSError:
        # Cache is optional; read-only data import remains usable without it.
        pass
    finally:
        for temporary in (npz_temp, json_temp):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def load_inputs(paths: list[str], progress=None, cancel=None, cache_dir=None,
                csv_options=None, *, disk_backed=False, build_previews=True) -> list[ChannelData]:
    """Load inputs, returning one ChannelData per independent channel/file.

    Partial failures are returned in warnings/metadata of successful channels;
    if every candidate fails, raise ValueError. A requested cancel is never
    swallowed. csv_options may be common options or an exact-path option map.
    """
    candidates, errors, discovery_notes, seen = [], [], [], set()
    for value in paths:
        _check_cancel(cancel)
        try:
            input_path = Path(value).expanduser().resolve()
            sources = _discover(input_path, cancel)
            # Folder/ZIP imports are PNE batch imports.  Only complete CYC +
            # StepEnd pairs are channels there.  A directly selected CYC is
            # still retained for raw/unmatched inspection.
            batch_import = input_path.is_dir() or input_path.suffix.lower() == ".zip"
            groups = defaultdict(list)
            for source in sources:
                groups[source.group].append(source)
            for group, items in sorted(groups.items(), key=lambda pair: _natural_key(pair[0])):
                cycs = sorted([s for s in items if s.name.lower().endswith(".cyc")], key=lambda s: _natural_key(s.name))
                step_csvs = [s for s in items if s.name.lower().endswith(".csv") and "stepend" in s.name.casefold()]
                used = set()
                for cyc in cycs:
                    target = PurePosixPath(cyc.name).stem.casefold()
                    matches = [s for s in step_csvs if s.reference not in used
                               and s.name.casefold().startswith(target) and s.name.casefold().endswith("_stepend.csv")]
                    pair_note = ""
                    if len(matches) == 1:
                        paired = matches[0]
                    elif not matches and len(cycs) == 1 and len(step_csvs) == 1:
                        paired = step_csvs[0]
                        pair_note = "같은 폴더의 유일한 CYC/StepEnd를 연결했습니다. 파일명 연결을 확인하세요."
                    else:
                        paired = None
                        if len(matches) > 1:
                            pair_note = "StepEnd 후보가 중복되어 자동 연결하지 않았습니다."
                    if paired is None and batch_import:
                        reason = pair_note or "대응하는 StepEnd.csv가 없습니다."
                        discovery_notes.append(f"{cyc.reference}: {reason} 빈 중복 채널로 등록하지 않았습니다.")
                        continue
                    if paired:
                        used.add(paired.reference)
                    identity = (cyc.reference, paired.reference if paired else "")
                    if identity not in seen:
                        seen.add(identity)
                        candidates.append((cyc, paired, pair_note))
                # A CSV inside a PNE channel folder is often an auxiliary or
                # previously exported file.  It must not become another
                # channel unless the CSV itself (or a CSV-only folder) was
                # explicitly selected.
                pne_group = bool(cycs or step_csvs)
                for source in items:
                    if (not pne_group and source.name.lower().endswith((".csv", ".tsv"))
                            and "stepend" not in source.name.casefold()):
                        identity = (source.reference, "")
                        if identity not in seen:
                            seen.add(identity)
                            candidates.append((source, None, ""))
        except InterruptedError:
            raise
        except Exception as exc:
            errors.append(f"{value}: {exc}")
    channels = []
    for index, (source, step_source, note) in enumerate(candidates):
        _check_cancel(cancel)
        if progress:
            progress(index, len(candidates), f"읽는 중: {source.member}")
        try:
            data = source.read(cancel)
            step_data = step_source.read(cancel) if step_source else None
            content_hash = hashlib.sha256(data)
            if step_data is not None:
                content_hash.update(step_data)
            digest = content_hash.hexdigest()
            options = csv_options or {}
            if source.reference in options and isinstance(options[source.reference], dict):
                options = options[source.reference]
            key_data = {"sha256": digest, "source": source.reference, "step": step_source.reference if step_source else "",
                        "version": PROFILE_VERSION, "options": options}
            key = hashlib.sha256(json.dumps(key_data, sort_keys=True, default=str).encode()).hexdigest()
            # Older releases share this directory. Keep the new array layout
            # out of the legacy reader while preserving the source fingerprint.
            cache_key = key + "-2"
            raw_path = Path(cache_dir) / (key + "-raw-v2") if disk_backed and cache_dir else None
            result = None
            if raw_path is not None:
                compact = _cache_read(raw_path, "channel")
                if compact is not None:
                    try:
                        result = (RawStore(raw_path), *compact[1:])
                    except (OSError, ValueError, TypeError, KeyError):
                        pass
            else:
                result = _cache_read(cache_dir, cache_key)
            cache_hit = result is not None
            if result is None:
                result = (_decode_pne(data, step_data, source, step_source, cancel)
                          if source.name.lower().endswith(".cyc") else _read_raw_csv(data, source, options))
                _check_cancel(cancel)
                result = (_compact_frame(result[0]), *result[1:])
                if raw_path is not None:
                    decoded, steps, details, notes = result
                    try:
                        stored = RawStore.write(raw_path, decoded, cancel, build_previews=build_previews)
                        _cache_write(raw_path, "channel", (pd.DataFrame(), steps, details, notes), cancel)
                        result = stored, steps, details, notes
                    except (OSError, ValueError, TypeError, KeyError):
                        # Storage restrictions or insufficient space can fall
                        # back to the currently decoded channel.
                        pass
                    finally:
                        del decoded
                else:
                    _cache_write(cache_dir, cache_key, result, cancel)
            frame, steps, metadata, warnings = result
            metadata = dict(metadata, sha256=digest, parse_fingerprint=key, cache_hit=cache_hit, source_member=source.member,
                            source_path=source.container, loader_version=PROFILE_VERSION,
                            disk_backed=isinstance(frame, RawStore))
            warnings = list(warnings)
            if note:
                warnings.append(note)
            name = PurePosixPath(source.member).parent.name
            if not name or name == ".":
                name = PurePosixPath(source.name).stem
            if metadata["input_type"] == "CSV":
                name = PurePosixPath(source.name).stem
            identity = hashlib.sha256((source.reference + "\0" + digest).encode("utf-8")).hexdigest()[:24]
            channels.append(ChannelData(identity, name, source.container, frame, steps, metadata, warnings))
            del data, step_data, result, frame
        except InterruptedError:
            raise
        except Exception as exc:
            errors.append(f"{source.reference}: {exc}")
        if progress:
            progress(index + 1, len(candidates), f"완료: {source.member}")
    _check_cancel(cancel)
    if not channels:
        messages = errors + discovery_notes
        raise ValueError("\n".join(messages) if messages else "분석 가능한 CYC/StepEnd 또는 Raw CSV를 찾지 못했습니다.")
    # Multiple valid files in one physical channel folder are kept separate,
    # but their labels must be distinguishable.  Never silently merge tests.
    duplicate_names = Counter((str(channel.source_path), channel.name) for channel in channels)
    for channel in channels:
        if duplicate_names[(str(channel.source_path), channel.name)] > 1:
            member = PurePosixPath(str(channel.metadata.get("source_member", "")))
            channel.name = f"{channel.name} · {member.stem or channel.id[:6]}"
    if discovery_notes:
        channels[0].metadata["import_notices"] = discovery_notes
        channels[0].warnings.extend(discovery_notes)
    for channel in channels:
        if errors:
            channel.metadata["import_errors"] = errors
            channel.warnings.extend(errors)
    return channels
