"""Cycle onset resistance and repeated RPT discharge pulses."""
import numpy as np
import pandas as pd

from backend.dcr_analysis import _interp_at, _stable_pulse_current_mA


def onset_resistances(frame, targets):
    """Use the observed Step t=0 voltage and stable current within each window."""
    label = {target: np.nan for target in targets}
    required = {"StepTime_s", "Voltage_V", "Current_mA"}
    if frame.empty or not required.issubset(frame.columns):
        return label
    time, voltage, current = (pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
                              for column in ("StepTime_s", "Voltage_V", "Current_mA"))
    if not np.isfinite(voltage[0]) or not np.isclose(time[0], 0, atol=1e-6, rtol=0):
        return label
    initial_current = current[0] if np.isfinite(current[0]) else 0.0
    initial_voltage = voltage[0]
    for target in targets:
        at_target = _interp_at(target, time, voltage)
        if not np.isfinite(at_target):
            continue
        within = (time > 0) & (time <= target + 1e-8) & np.isfinite(current)
        stable, valid = _stable_pulse_current_mA(np.r_[0.0, time[within]],
                                                 np.r_[initial_current, current[within]], "")
        delta = stable - initial_current
        if within.any() and valid and np.isfinite(delta) and abs(delta) > 1e-8:
            label[target] = abs(initial_voltage - at_target) * 1_000_000 / abs(delta)
    return label


def onset_context(cycle, segment):
    """Recover adjacent t=0 rows classified as REST within the same physical Step."""
    first = segment.iloc[0]
    position = cycle["_row"].searchsorted(first["_row"])
    if position <= 0 or "PhysicalStepID" not in cycle:
        return segment
    prior = cycle.iloc[position - 1]
    if (prior["_row"] + 1 == first["_row"] and prior["PhysicalStepID"] == first["PhysicalStepID"]
            and prior.get("Source_File") == first.get("Source_File") and prior.get("StepTime_s") == 0):
        return cycle.iloc[position - 1:position + len(segment)]
    return segment


def rpt_pulses(frame, step, socs, cancel=None):
    from .analysis import _step_segments
    valid = frame["StepNo"].astype(str).eq(step) & frame["TypeStd"].eq("DISCHARGE")
    segments = _step_segments(frame.loc[valid])
    matched = len(segments) == len(socs)
    if not segments:
        return [dict(Item="방전 10초 DCIR", Step=step, Value=None, Status="선택 Step의 방전 펄스 없음")]
    rows = []
    for index, pulse in enumerate(segments):
        if cancel and cancel():
            raise InterruptedError("RPT 준비 취소")
        value = onset_resistances(onset_context(frame, pulse), (10,))[10]
        rows.append(dict(Item="방전 10초 DCIR", Step=step, Pulse=index + 1, Direction="DISCHARGE",
                         SOC_pct=socs[index] if matched else None,
                         PhysicalStep=str(pulse.iloc[0].get("PhysicalStepID", "")),
                         Value=float(value) if np.isfinite(value) else None,
                         Status=("SOC 개수 불일치" if not matched else
                                 "계산됨" if np.isfinite(value) else "10초 전압 또는 안정 전류 부족")))
    return rows


def dcir_changes(points, manual_baseline=None, *, bol_baseline=None):
    """Use an unambiguous Cycle 1 measurement or a user-entered mΩ reference."""
    baseline_rows = [p["Value"] for p in points if p["Cycle"] == 1]
    baseline = bol_baseline if bol_baseline is not None else (
        baseline_rows[0] if len(baseline_rows) == 1 else manual_baseline)
    if baseline is None or not np.isfinite(baseline) or baseline <= 0:
        return {}
    result = {1: 0.0} if not baseline_rows and manual_baseline is not None and bol_baseline is None else {}
    counts = {}
    for point in points:
        cycle = point["Cycle"]
        counts[cycle] = counts.get(cycle, 0) + 1
        result[cycle] = 100 * (point["Value"] / baseline - 1)
    return {cycle: value for cycle, value in result.items() if counts.get(cycle, 1) == 1}
