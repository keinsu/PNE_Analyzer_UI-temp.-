"""Import rules and RPT values computed from original observations."""
from dataclasses import replace
import hashlib
import json

import numpy as np
import pandas as pd

from .analysis import _step_segments, analysis_frame
from .project import _json_value

FIELDS = {"Voltage_V": "전압 (V)", "Capacity_mAh": "용량 (mAh)", "Current_mA": "전류 (mA)",
          "StepTime_s": "시간 (s)", "Energy_mWh": "에너지 (mWh)", "Temp_C": "온도 (°C)"}
OPERATIONS = {"first": "시작값", "last": "종료값", "delta": "변화량 (종료−시작)",
              "mean": "평균", "max": "최댓값", "min": "최솟값"}
KINDS = {"cycle": "Cycle", "rpt": "RPT", "other": "기타"}


def step_options(channel):
    frame = channel.read_rows(columns=["StepNo", "TypeStd", "Cycle"])
    if "StepNo" not in frame:
        return []
    grouped = frame.dropna(subset=["StepNo"]).groupby(["StepNo", "TypeStd"], sort=False, observed=True)
    return [(str(step), str(mode), group["Cycle"].nunique(), len(group))
            for (step, mode), group in grouped]


def apply_rules(channel, rules):
    # Source identity stays stable. Derived data is keyed by the exact rules.
    encoded = json.dumps(rules, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256((channel.fingerprint + "\0" + encoded).encode()).hexdigest()
    return replace(channel, metadata=dict(channel.metadata, import_kind=rules["kind"],
                                         import_rules=rules, parse_fingerprint=digest))


def rpt_rows(channel, rules, cancel=None):
    frame = analysis_frame(channel)
    if frame.empty or "StepNo" not in frame or not rules:
        return []
    result = []
    by_step = {str(step): rows for step, rows in frame.groupby("StepNo", sort=False, observed=True)}
    for rule in rules:
        if cancel and cancel():
            raise InterruptedError("RPT 준비 취소")
        name, step, field, operation = (rule[k] for k in ("name", "step", "field", "operation"))
        matching = by_step.get(step, frame.iloc[:0])
        if rule.get("direction"):
            matching = matching[matching["TypeStd"].eq(rule["direction"])]
        segments = _step_segments(matching)
        if not segments:
            result.append(dict(Item=name, Step=step, Field=field, Method=operation, Value=None,
                               Status="분석 가능한 Step 없음"))
        for segment in segments:
            clean = pd.to_numeric(segment[field], errors="coerce").dropna() if field in segment else pd.Series(dtype=float)
            clean = clean[np.isfinite(clean)]
            value = None
            if len(clean):
                if operation == "first":
                    value = clean.iloc[0]
                elif operation == "last":
                    value = clean.iloc[-1]
                elif operation == "delta":
                    value = clean.iloc[-1] - clean.iloc[0] if len(clean) >= 2 else None
                else:
                    value = clean.agg(operation)
            result.append(dict(Item=name, Step=step, Field=field, Method=operation,
                               Cycle=int(segment["Cycle"].iloc[0]),
                               PhysicalStep=str(segment["PhysicalStepID"].iloc[0]) if "PhysicalStepID" in segment else "",
                               Value=_json_value(value), Status="계산됨" if value is not None else "측정값 부족"))
    return result
