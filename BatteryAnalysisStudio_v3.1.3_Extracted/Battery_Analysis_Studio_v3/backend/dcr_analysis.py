# -*- coding: utf-8 -*-
"""DCR(Direct Current Resistance) 펄스 시험 분석.

지원하는 기본 프로시저
-----------------------
1. 목표 SOC 전압으로 setting (충전/방전/생략 모두 가능)
2. 휴지
3. 짧은 충전 또는 방전 펄스(DCIR 측정)
4. 반대 방향의 SOC return으로 펄스 전 용량/SOC에 복귀
5. 2~4를 전류별로 반복하고, 다음 목표 전압에서 다시 반복

SOC 블록은 완전방전 같은 특정 전처리를 전제하지 않는다. 측정 펄스와 그 뒤 SOC return의
용량 균형을 확인하고, 두 측정 사이에 별도 활성 setting Step이 있을 때만 다음 SOC 블록을
시작한다. 따라서 과전압으로 달라진 휴지전압은 블록 판정에 사용하지 않는다.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import re
import numpy as np
import pandas as pd

from .pne_parser import TYPE_CHARGE, TYPE_DISCHARGE, TYPE_REST


def _is_active(segment: dict) -> bool:
    return segment["type"] in (TYPE_CHARGE, TYPE_DISCHARGE)


def _is_opposite(first: dict, second: dict) -> bool:
    return {first["type"], second["type"]} == {TYPE_CHARGE, TYPE_DISCHARGE}


def segment_steps(df: pd.DataFrame, cancel=None) -> list:
    """원본 순서대로 실제 시험 Step을 분리한다.

    ``StepNo``가 매핑되어 있으면 Type이 같은 연속 Step도 서로 분리한다. Step 번호가
    없을 때는 Type 변화와 StepTime 감소(새 Step에서 시간 리셋)를 경계로 사용한다.
    """
    d = df.sort_values("_row").reset_index(drop=True)
    if d.empty:
        return []

    type_arr = d["TypeStd"].values
    row_arr = d["_row"].values
    step_arr = d["StepNo"].astype(str).values if "StepNo" in d.columns else None
    source_arr = d["Source_File"].astype(str).values if "Source_File" in d.columns else None
    mode_arr = d["StepMode"].astype(str).values if "StepMode" in d.columns else None
    physical_arr = d["PhysicalStepID"].astype(str).values if "PhysicalStepID" in d.columns else None
    cycle_arr = d["Cycle"].values if "Cycle" in d.columns else None
    time_arr = d["StepTime_s"].values.astype(float) if "StepTime_s" in d.columns else None

    boundaries = [0]
    for i in range(1, len(d)):
        if i % 4096 == 0 and cancel is not None and cancel():
            raise InterruptedError("DCR Step 탐지가 취소되었습니다.")
        is_new_step = type_arr[i] != type_arr[i - 1]
        if row_arr[i] != row_arr[i - 1] + 1:
            is_new_step = True
        if step_arr is not None and step_arr[i] != step_arr[i - 1]:
            is_new_step = True
        if physical_arr is not None and physical_arr[i] != physical_arr[i - 1]:
            is_new_step = True
        if cycle_arr is not None and cycle_arr[i] != cycle_arr[i - 1]:
            is_new_step = True
        # 여러 파일을 하나의 DCR 결과로 합칠 때 파일 경계를 절대로 같은 물리 Step으로 잇지 않는다.
        if source_arr is not None and source_arr[i] != source_arr[i - 1]:
            is_new_step = True
        if time_arr is not None and np.isfinite(time_arr[i]) and np.isfinite(time_arr[i - 1]):
            if time_arr[i] < time_arr[i - 1] - 1e-9:
                is_new_step = True
        if is_new_step:
            boundaries.append(i)
    boundaries.append(len(d))

    segments = []
    for si in range(len(boundaries) - 1):
        if cancel is not None and cancel():
            raise InterruptedError("DCR Step 탐지가 취소되었습니다.")
        a, b = boundaries[si], boundaries[si + 1]
        seg = d.iloc[a:b]
        t = seg["StepTime_s"].values.astype(float) if "StepTime_s" in seg.columns else np.arange(len(seg), dtype=float)
        t_valid = t[np.isfinite(t)]
        duration = float(t_valid.max() - t_valid.min()) if len(t_valid) >= 2 else float(len(seg))
        current = seg["Current_mA"].values.astype(float) if "Current_mA" in seg.columns else np.array([])
        cap = seg["Capacity_mAh"].values.astype(float) if "Capacity_mAh" in seg.columns else np.array([])
        segments.append(dict(
            index=si,
            type=type_arr[a],
            step_no=str(step_arr[a]) if step_arr is not None else None,
            source_file=str(source_arr[a]) if source_arr is not None else None,
            step_mode=str(mode_arr[a]) if mode_arr is not None else "",
            physical_step_id=str(physical_arr[a]) if physical_arr is not None else None,
            cycle=int(cycle_arr[a]) if cycle_arr is not None else None,
            start_row=int(row_arr[a]),
            end_row=int(row_arr[b - 1]),
            n_rows=b - a,
            start_voltage=float(seg["Voltage_V"].iloc[0]),
            end_voltage=float(seg["Voltage_V"].iloc[-1]),
            duration_s=duration,
            mean_current_mA=float(np.nanmean(current)) if len(current) and np.isfinite(current).any() else np.nan,
            start_capacity_mAh=float(cap[0]) if len(cap) else np.nan,
            end_capacity_mAh=float(cap[-1]) if len(cap) else np.nan,
            delta_capacity_mAh=float(abs(cap[-1] - cap[0])) if len(cap) >= 2 else np.nan,
        ))
    return segments


def _next_active_segment(segments: list, index: int):
    """index 뒤에서 REST를 건너뛴 첫 활성 Step과 그 index를 반환."""
    source_file = segments[index].get("source_file")
    for next_index in range(index + 1, len(segments)):
        # 여러 파일을 합쳐도 return Step은 현재 파일 안에서만 찾는다.
        if segments[next_index].get("source_file") != source_file:
            return None, None
        if _is_active(segments[next_index]):
            return next_index, segments[next_index]
    return None, None


def _physical_step_capacity_mAh(df: pd.DataFrame, segment: dict, ordered=None) -> float:
    """0.0초 전환행과 마지막 행을 포함한 물리적 Step의 용량 변화량을 반환한다.

    분리 용량 파일에서는 0.0초가 REST 세그먼트가 되어 활성 세그먼트가 0.1초부터 시작할 수
    있다. 세그먼트 내부의 첫/마지막 값만 빼면 return 용량이 초반 누적값만큼 작아진다.
    """
    step_rows, _ = _pulse_step_context(df, segment, ordered=ordered)
    values = step_rows["Capacity_mAh"].dropna() if "Capacity_mAh" in step_rows.columns else pd.Series(dtype=float)
    if len(values) < 2:
        return np.nan
    return float(abs(values.iloc[-1] - values.iloc[0]))


def detect_dcr_blocks(
    df: pd.DataFrame,
    pulse_max_duration_s: float = 90.0,
    return_capacity_tolerance_pct: float = 10.0,
    return_capacity_tolerance_mAh: float = 1.0,
    soc_setting_step_nos: set | None = None,
    cancel=None,
) -> dict:
    """SOC 블록과 ``휴지 → 펄스 → 반대방향 SOC return`` 측정 단위를 감지한다.

    펄스는 (1) 직전 Step이 휴지이고, (2) 최대 지속시간 이내이며, (3) 이후 첫 활성
    Step이 반대 방향인 구조로 검출한다. 사용자가 지정한 SOC setting StepNo는 펄스 후보에서
    제외한다. 펄스/return 용량 오차는 후보를 버리는 조건이 아니라
    검토용 신뢰도 표시다. 실제 시험에서는 return 전류·시간이 달라 용량이 완전히 같지 않을 수
    있으므로, 구조가 맞는 유효 펄스가 허용오차 때문에 누락되지 않게 한다.
    """
    soc_setting_step_nos = {str(step).strip() for step in (soc_setting_step_nos or set()) if str(step).strip()}
    ordered = df.sort_values("_row", kind="stable")
    segments = segment_steps(ordered, cancel=cancel)
    if not segments:
        return {"segments": [], "blocks": [], "global_min_voltage": None,
                "return_capacity_tolerance_pct": return_capacity_tolerance_pct,
                "return_capacity_tolerance_mAh": return_capacity_tolerance_mAh,
                "soc_setting_step_nos": sorted(soc_setting_step_nos),
                "grouping_method": "capacity_balance"}

    candidates = []
    for i, pulse in enumerate(segments):
        if cancel is not None and cancel():
            raise InterruptedError("DCR 탐지가 취소되었습니다.")
        if i == 0 or not _is_active(pulse) or pulse["duration_s"] > pulse_max_duration_s:
            continue
        if str(pulse.get("step_no") or "").strip() in soc_setting_step_nos:
            continue
        rest = segments[i - 1]
        if rest["type"] != TYPE_REST:
            continue
        if rest.get("source_file") != pulse.get("source_file"):
            continue
        return_index, return_seg = _next_active_segment(segments, i)
        if return_seg is None or not _is_opposite(pulse, return_seg):
            continue
        pulse_cap = _physical_step_capacity_mAh(ordered, pulse, ordered=ordered)
        return_cap = _physical_step_capacity_mAh(ordered, return_seg, ordered=ordered)
        cap_error = np.nan
        capacity_balanced = None
        allowed_error = np.nan
        if np.isfinite(pulse_cap) and np.isfinite(return_cap) and pulse_cap > 0 and return_cap > 0:
            cap_error = abs(return_cap - pulse_cap)
            allowed_error = max(return_capacity_tolerance_mAh, pulse_cap * return_capacity_tolerance_pct / 100.0)
            capacity_balanced = bool(cap_error <= allowed_error)
        candidates.append(dict(
            seg=i,
            rest_seg=i - 1,
            return_seg=return_index,
            direction=pulse["type"],
            reference_voltage=rest["end_voltage"],
            pulse_capacity_mAh=pulse_cap,
            return_capacity_mAh=return_cap,
            return_capacity_error_mAh=cap_error,
            return_capacity_allowed_error_mAh=allowed_error,
            return_capacity_balanced=capacity_balanced,
        ))

    blocks = []
    previous_return_index = None
    for candidate in candidates:
        has_setting_between = False
        if previous_return_index is not None:
            between = segments[previous_return_index + 1:candidate["rest_seg"]]
            has_setting_between = (
                segments[previous_return_index].get("source_file") != segments[candidate["seg"]].get("source_file")
                or any(_is_active(segment) for segment in between)
            )
        if not blocks or has_setting_between:
            blocks.append(dict(
                block_index=len(blocks),
                full_discharge_seg=None,
                soc_setting_seg=None,
                reference_voltage=candidate["reference_voltage"],
                charge_in_mAh=None,
                pulse_groups=[],
                segment_range=(candidate["rest_seg"], candidate["return_seg"]),
            ))

        block = blocks[-1]
        start, _ = block["segment_range"]
        block["segment_range"] = (min(start, candidate["rest_seg"]), candidate["return_seg"])
        if block["pulse_groups"] and block["pulse_groups"][-1]["direction"] == candidate["direction"]:
            block["pulse_groups"][-1]["pulses"].append(candidate)
        else:
            block["pulse_groups"].append({"direction": candidate["direction"], "pulses": [candidate]})
        previous_return_index = candidate["return_seg"]

    return {
        "segments": segments,
        "blocks": blocks,
        "global_min_voltage": None,
        "return_capacity_tolerance_pct": return_capacity_tolerance_pct,
        "return_capacity_tolerance_mAh": return_capacity_tolerance_mAh,
        "soc_setting_step_nos": sorted(soc_setting_step_nos),
        "grouping_method": "capacity_balance",
    }


def _interp_at(t_query, t_arr, values, max_gap_s=None):
    """Interpolate only inside measured bounds and across modest sample gaps.

    By default a gap over three times the median positive interval is rejected.
    Exact observed points always remain valid. No extrapolation is permitted.
    """
    t_arr = np.asarray(t_arr, dtype=float)
    values = np.asarray(values, dtype=float)
    mask = np.isfinite(t_arr) & np.isfinite(values)
    t_arr, values = t_arr[mask], values[mask]
    if len(t_arr) == 0 or not np.isfinite(t_query):
        return np.nan
    order = np.argsort(t_arr, kind="stable")
    t_arr, values = t_arr[order], values[order]
    if t_query < t_arr[0] or t_query > t_arr[-1]:
        return np.nan
    exact = np.flatnonzero(np.isclose(t_arr, t_query, atol=1e-9, rtol=0))
    if len(exact):
        return float(values[exact[-1]])
    right = int(np.searchsorted(t_arr, t_query, side="right"))
    if right == 0 or right >= len(t_arr):
        return np.nan
    intervals = np.diff(np.unique(t_arr))
    positive = intervals[intervals > 0]
    gap_limit = float(max_gap_s) if max_gap_s is not None else (
        float(np.median(positive) * 3.0) if len(positive) else 0.0
    )
    if t_arr[right] - t_arr[right - 1] > gap_limit + 1e-9:
        return np.nan
    return float(np.interp(t_query, t_arr, values))


def _segment_rows(df: pd.DataFrame, segment: dict, ordered=None) -> pd.DataFrame:
    if ordered is not None:
        values = ordered["_row"].to_numpy()
        start = int(np.searchsorted(values, segment["start_row"], side="left"))
        end = int(np.searchsorted(values, segment["end_row"], side="right"))
        return ordered.iloc[start:end]
    return df[(df["_row"] >= segment["start_row"]) & (df["_row"] <= segment["end_row"])].sort_values("_row")


def _round_current_mA(value: float) -> float:
    """표시 단위 기준으로 소수점 셋째 자리에서 반올림한 mA 전류를 반환한다.

    1 A 이상은 A 단위에서 소수 둘째 자리까지 반올림한 뒤 mA로 되돌린다. 예를 들어
    14.999 A는 계산에도 15.00 A(15,000 mA)로 사용한다. 1 A 미만은 mA 단위에서
    소수 둘째 자리까지 반올림한다.
    """
    if not np.isfinite(value):
        return np.nan
    value = float(value)
    if abs(value) >= 1000.0:
        rounded_a = Decimal(str(value / 1000.0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(rounded_a * Decimal("1000"))
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _pulse_step_context(df: pd.DataFrame, segment: dict, ordered=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """펄스의 물리적 Step 행과 그 직전 행을 반환한다.

    PNE의 분리 용량 데이터에서는 같은 Step의 0.0초 행이 용량 0 때문에 REST로 분류될 수
    있다. 분석용 활성 세그먼트만 보면 그 행을 잃으므로 StepNo가 같은 인접 원시 행까지
    확장한다. 이를 통해 V0는 실제 펄스 0.0초에서, REST 전류는 그 Step *이전*에서 읽는다.
    """
    ordered = df.sort_values("_row", kind="stable") if ordered is None else ordered
    if ordered.empty:
        return ordered, ordered
    row_values = ordered["_row"].to_numpy()
    start = int(np.searchsorted(row_values, segment["start_row"], side="left"))
    stop = int(np.searchsorted(row_values, segment["end_row"], side="right"))
    if start >= stop:
        return ordered.iloc[0:0], ordered.iloc[0:0]
    end = stop - 1

    if "PhysicalStepID" in ordered.columns and segment.get("physical_step_id") is not None:
        physical = str(segment["physical_step_id"])
        source = ordered["Source_File"].iloc[start] if "Source_File" in ordered.columns else None
        while start > 0 and str(ordered.iloc[start - 1].get("PhysicalStepID")) == physical:
            if ordered.iloc[start - 1]["_row"] + 1 != ordered.iloc[start]["_row"]:
                break
            if source is not None and ordered.iloc[start - 1].get("Source_File") != source:
                break
            start -= 1
        while end + 1 < len(ordered) and str(ordered.iloc[end + 1].get("PhysicalStepID")) == physical:
            if ordered.iloc[end]["_row"] + 1 != ordered.iloc[end + 1]["_row"]:
                break
            if source is not None and ordered.iloc[end + 1].get("Source_File") != source:
                break
            end += 1
    elif "StepNo" in ordered.columns and segment.get("step_no") is not None:
        step_no = str(segment["step_no"])
        cycle = ordered["Cycle"].iloc[start] if "Cycle" in ordered.columns else None
        source_file = ordered["Source_File"].iloc[start] if "Source_File" in ordered.columns else None
        while start > 0:
            previous = ordered.iloc[start - 1]
            same_cycle = cycle is None or previous.get("Cycle") == cycle
            same_source = source_file is None or previous.get("Source_File") == source_file
            if str(previous.get("StepNo")) != step_no or not same_cycle or not same_source:
                break
            # StepNo가 재사용될 수 있으므로, 시간 리셋 전의 다른 물리 Step은 넘지 않는다.
            if "StepTime_s" in ordered.columns:
                previous_t = pd.to_numeric(previous.get("StepTime_s"), errors="coerce")
                current_t = pd.to_numeric(ordered.iloc[start].get("StepTime_s"), errors="coerce")
                if np.isfinite(previous_t) and np.isfinite(current_t) and previous_t > current_t + 1e-9:
                    break
            start -= 1
        while end + 1 < len(ordered):
            following = ordered.iloc[end + 1]
            same_cycle = cycle is None or following.get("Cycle") == cycle
            same_source = source_file is None or following.get("Source_File") == source_file
            if str(following.get("StepNo")) != step_no or not same_cycle or not same_source:
                break
            if "StepTime_s" in ordered.columns:
                following_t = pd.to_numeric(following.get("StepTime_s"), errors="coerce")
                current_t = pd.to_numeric(ordered.iloc[end].get("StepTime_s"), errors="coerce")
                if np.isfinite(following_t) and np.isfinite(current_t) and following_t < current_t - 1e-9:
                    break
            end += 1
    previous = ordered.iloc[:start]
    return ordered.iloc[start:end + 1], previous


def _last_actual_rest_current_mA(previous_rows: pd.DataFrame, fallback_rest_rows: pd.DataFrame | None, source_file=None) -> float:
    """펄스 Step 이전의 마지막 REST 전류를 반환한다.

    같은 펄스 Step에 속한 0.0초 전환행은 절대로 REST 전류 기준으로 쓰지 않는다.
    """
    if not previous_rows.empty and "TypeStd" in previous_rows.columns:
        types = previous_rows["TypeStd"].to_numpy()
        currents = previous_rows["Current_mA"].to_numpy(dtype=float)
        sources = previous_rows["Source_File"].to_numpy() if "Source_File" in previous_rows else None
        for index in range(len(types) - 1, -1, -1):
            if source_file is not None and sources is not None and sources[index] != source_file:
                break
            if types[index] == TYPE_REST and np.isfinite(currents[index]):
                return _round_current_mA(currents[index])
    if fallback_rest_rows is not None:
        values = fallback_rest_rows["Current_mA"].dropna()
        if not values.empty:
            return _round_current_mA(values.iloc[-1])
    return 0.0


def _is_constant_power_step(step_mode: str) -> bool:
    """보존된 원본 Step 표기로 CP(정전력)인지 판별한다."""
    mode = str(step_mode).lower()
    return bool(re.search(r"(?<![a-z])cp(?![a-z])", mode) or "constant power" in mode or "정전력" in mode)


def _stable_pulse_current_mA(t: np.ndarray, current_mA: np.ndarray, step_mode: str) -> tuple[float, bool]:
    """DCIR 계산에 쓸 펄스 전류와 CC 여부를 반환한다.

    CC는 스텝 초반의 0.0초/상승구간 전류 대신 후반 안정 구간의 중앙값을 사용한다. CP는
    전류가 시간에 따라 변하므로 호출부에서 각 시간시점의 보간 전류를 사용한다.
    """
    if len(current_mA) == 0:
        return np.nan, False
    finite = np.isfinite(t) & np.isfinite(current_mA)
    t_valid, i_valid = t[finite], current_mA[finite]
    if not len(i_valid):
        return np.nan, False
    non_transition = i_valid[t_valid > np.nanmin(t_valid) + 1e-9]
    if not len(non_transition):
        non_transition = i_valid
    # 후반 1/3은 CC의 전류 상승·전환 구간을 피하면서 짧은 펄스도 처리한다.
    tail_count = max(1, int(np.ceil(len(non_transition) / 3)))
    tail = non_transition[-tail_count:]
    stable_current = _round_current_mA(np.nanmedian(tail))

    if _is_constant_power_step(step_mode):
        return stable_current, False
    if len(tail) <= 1:
        return stable_current, True
    tail_spread = float(np.nanpercentile(tail, 90) - np.nanpercentile(tail, 10))
    # 0.02 mA 또는 중심 전류의 0.2% 안이면 CC 안정 구간으로 본다.
    tolerance = max(0.02, abs(stable_current) * 0.002)
    return stable_current, tail_spread <= tolerance


def compute_pulse_result(df: pd.DataFrame, segments: list, pulse_ref: dict, time_points_s: list, ordered=None) -> dict:
    """한 펄스의 시점별 DCIR과 원자료를 계산한다.

    V0는 펄스의 정확한 0.0초 원시값을 최우선으로 사용한다. 이 값은 스텝 전환 순간의
    값이므로, 이미 전류와 과전압이 반영될 수 있는 첫 0.1초 값보다 SOC 기준에 적합하다.
    0.0초 행이 없을 때만 직전 REST의 마지막 유효 전압을 쓰고, 둘 다 없을 때는 첫 유효
    펄스 행으로 안전하게 대체한다. I0는 CC의 안정화 펄스 전류(소수 둘째 자리)로 표시한다.
    이로써 0.0초의 0 A 전환값이나 초반 전류 상승값이 ``ΔI``를 작게 만들어 DCIR을 과대
    계산하는 문제를 막는다. CP는 각 시간시점의 전류를 사용한다.
    """
    seg = segments[pulse_ref["seg"]]
    rest_seg = segments[pulse_ref["rest_seg"]] if pulse_ref.get("rest_seg") is not None else None
    ordered = df.sort_values("_row", kind="stable") if ordered is None else ordered
    pulse_rows = _segment_rows(ordered, seg, ordered=ordered)
    physical_step_rows, previous_rows = _pulse_step_context(ordered, seg, ordered=ordered)

    t = pulse_rows["StepTime_s"].values.astype(float)
    v = pulse_rows["Voltage_V"].values.astype(float)
    i_mA = pulse_rows["Current_mA"].values.astype(float)
    mask = np.isfinite(t) & np.isfinite(v) & np.isfinite(i_mA)
    t, v, i_mA = t[mask], v[mask], i_mA[mask]
    order = np.argsort(t)
    t, v, i_mA = t[order], v[order], i_mA[order]

    v0, rest_current_mA = np.nan, 0.0
    v0_source = ""
    rest_v = None
    fallback_rest_rows = _segment_rows(ordered, rest_seg, ordered=ordered) if rest_seg is not None else None
    if rest_seg is not None:
        rest_v = fallback_rest_rows["Voltage_V"].dropna()
    rest_current_mA = _last_actual_rest_current_mA(previous_rows, fallback_rest_rows, seg.get("source_file"))

    # 활성 세그먼트에 없더라도 같은 StepNo의 원시 0.0초 행을 우선 사용한다.
    physical_t0 = physical_step_rows[
        np.isclose(pd.to_numeric(physical_step_rows.get("StepTime_s"), errors="coerce"), 0.0, atol=1e-6)
    ] if not physical_step_rows.empty and "StepTime_s" in physical_step_rows else physical_step_rows.iloc[0:0]
    physical_t0_v = physical_t0["Voltage_V"].dropna() if not physical_t0.empty else pd.Series(dtype=float)
    if not physical_t0_v.empty:
        v0 = float(physical_t0_v.iloc[0])
        v0_source = "pulse_t0"
    elif rest_seg is not None and not rest_v.empty:
        v0 = float(rest_v.iloc[-1])
        v0_source = "previous_rest"
    elif len(v):
        v0 = float(v[0])
        rest_current_mA = _round_current_mA(i_mA[0])
        v0_source = "first_valid_pulse_sample"

    pulse_current_mA, is_cc = _stable_pulse_current_mA(t, i_mA, seg.get("step_mode", ""))

    r_values, v_values, applied_currents, delta_currents, time_status = {}, {}, {}, {}, {}
    for tp in time_points_s:
        vt = _interp_at(tp, t, v)
        it = _interp_at(tp, t, i_mA)
        applied_current_mA = pulse_current_mA if is_cc else (_round_current_mA(it) if np.isfinite(it) else np.nan)
        delta_i_mA = applied_current_mA - rest_current_mA if np.isfinite(applied_current_mA) else np.nan
        applied_currents[tp] = applied_current_mA
        delta_currents[tp] = delta_i_mA
        if not np.isfinite(vt) or not np.isfinite(it):
            time_status[tp] = "측정 범위 밖 또는 큰 측정 공백"
        elif not np.isfinite(v0):
            time_status[tp] = "V0 없음"
        elif not np.isfinite(delta_i_mA) or delta_i_mA == 0:
            time_status[tp] = "유효 전류 변화 없음"
        else:
            time_status[tp] = "측정점" if np.isclose(t, tp, atol=1e-9, rtol=0).any() else "보간"
        if not np.isfinite(vt) or not np.isfinite(delta_i_mA) or delta_i_mA == 0 or not np.isfinite(v0):
            r_values[tp] = np.nan
        else:
            r_values[tp] = abs(v0 - vt) * 1_000_000.0 / abs(delta_i_mA)  # V / mA -> mOhm
        v_values[tp] = vt

    return dict(
        seg_index=seg["index"], type=seg["type"], v0=v0, i0_mA=pulse_current_mA, v0_source=v0_source,
        duration_s=seg["duration_s"], mean_current_mA=pulse_current_mA,
        r_by_t=r_values, v_by_t=v_values,
        rest_current_mA=rest_current_mA, control_mode="CC" if is_cc else "CP/variable",
        applied_current_by_t=applied_currents, delta_current_by_t=delta_currents, time_status=time_status,
        raw_time_s=t, raw_voltage_v=v, raw_current_mA=i_mA,
    )


def _match_soc(reference_voltage: float | None, soc_ocv_points: list | None):
    """가장 가까운 사용 입력 SOC-OCV 점을 반환: (SOC%, OCV V, 오차 mV)."""
    if reference_voltage is None or not soc_ocv_points:
        return None, None, None
    soc, voltage = min(soc_ocv_points, key=lambda point: abs(reference_voltage - point[1]))
    return float(soc), float(voltage), abs(reference_voltage - float(voltage)) * 1000.0


def compute_dcr_table(
    df: pd.DataFrame,
    detection: dict,
    time_points_s: list,
    rated_capacity_mAh: float | None = None,
    excluded_pulse_segs: set | None = None,
    soc_ocv_points: list | None = None,
    manual_block_by_seg: dict | None = None,
    manual_soc_by_seg: dict | None = None,
    cancel=None,
) -> dict:
    """전체 DCIR 요약 테이블과 펄스 V-t/I-t 원자료를 만든다.

    ``rated_capacity_mAh``는 이전 버전 호출 호환용이며, SOC 표기는 ``soc_ocv_points``의
    전압 매칭을 우선 사용한다. 매칭점을 주지 않으면 기준전압만 결과에 표시된다.
    """
    del rated_capacity_mAh
    excluded_pulse_segs = excluded_pulse_segs or set()
    manual_block_by_seg = manual_block_by_seg or {}
    manual_soc_by_seg = manual_soc_by_seg or {}
    segments = detection["segments"]
    ordered = df.sort_values("_row", kind="stable")
    rows = []
    raw_curves = []

    for block in detection["blocks"]:
        soc_pct, matched_ocv, match_delta_mv = _match_soc(block.get("reference_voltage"), soc_ocv_points)
        for group in block["pulse_groups"]:
            for pi, pulse_ref in enumerate(group["pulses"]):
                if cancel is not None and cancel():
                    raise InterruptedError("DCR 계산이 취소되었습니다.")
                if pulse_ref["seg"] in excluded_pulse_segs:
                    continue
                result = compute_pulse_result(ordered, segments, pulse_ref, time_points_s, ordered=ordered)
                seg_index = pulse_ref["seg"]
                block_number = manual_block_by_seg.get(seg_index, block["block_index"] + 1)
                manual_soc = manual_soc_by_seg.get(seg_index)
                row = dict(
                    Source_File=segments[seg_index].get("source_file") or "입력 파일",
                    Cycle=segments[seg_index].get("cycle"),
                    StepNo=segments[seg_index].get("step_no"),
                    PhysicalStepID=segments[seg_index].get("physical_step_id"),
                    Block=block_number,
                    Reference_Voltage_V=block["reference_voltage"],
                    SOC_pct=manual_soc if manual_soc is not None else soc_pct,
                    SOC_Matched_OCV_V=matched_ocv,
                    SOC_Match_Delta_mV=match_delta_mv,
                    Direction="discharge" if group["direction"] == TYPE_DISCHARGE else "charge",
                    Pulse_Index=pi + 1,
                    Current_mA=result["mean_current_mA"],
                    V0_V=result["v0"],
                    V0_Source=result["v0_source"],
                    I0_mA=result["i0_mA"],
                    Rest_Current_mA=result["rest_current_mA"],
                    Control_Mode=result["control_mode"],
                    Current_Source_Unit=df.attrs.get("current_source_unit"),
                    Pulse_Duration_s=result["duration_s"],
                    Return_Segment=segments[pulse_ref["return_seg"]]["index"],
                    Pulse_Capacity_mAh=pulse_ref.get("pulse_capacity_mAh"),
                    Return_Capacity_mAh=pulse_ref.get("return_capacity_mAh"),
                    Return_Capacity_Error_mAh=pulse_ref.get("return_capacity_error_mAh"),
                    Return_Capacity_Allowed_Error_mAh=pulse_ref.get("return_capacity_allowed_error_mAh"),
                    Return_Capacity_Balanced=pulse_ref.get("return_capacity_balanced"),
                    _seg=pulse_ref["seg"],
                )
                for tp in time_points_s:
                    row[f"R_{tp:g}s_mOhm"] = result["r_by_t"][tp]
                    row[f"V_{tp:g}s_V"] = result["v_by_t"][tp]
                    row[f"Applied_I_{tp:g}s_mA"] = result["applied_current_by_t"][tp]
                    row[f"Delta_I_{tp:g}s_mA"] = result["delta_current_by_t"][tp]
                    row[f"Status_{tp:g}s"] = result["time_status"][tp]
                rows.append(row)
                raw_curves.append(dict(
                    source_file=row["Source_File"], block=block["block_index"] + 1,
                    direction=row["Direction"], pulse_index=pi + 1,
                    current_mA=result["mean_current_mA"], time_s=result["raw_time_s"],
                    voltage_v=result["raw_voltage_v"], current_series_mA=result["raw_current_mA"],
                    seg_index=seg_index, physical_step_id=row["PhysicalStepID"],
                    v0=result["v0"], v0_source=result["v0_source"],
                    sampled_v_by_t=result["v_by_t"], rest_current_mA=result["rest_current_mA"],
                ))

    return dict(table=rows, raw_curves=raw_curves)
