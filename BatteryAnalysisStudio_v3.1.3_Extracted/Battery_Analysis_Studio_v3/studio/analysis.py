"""Traceable analysis adapters around the preserved Qt calculation routines.

Values use V, mA, mAh, mWh, s and degrees C.  Display filtering never mutates
the channel frame.  Physical steps are selected before legacy differentiation.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from backend.cycle_metrics import _compute_one_cycle_row, _cc_cv_split
from backend.dqdv import compute_dqdv, _cc_region_only

ANALYSIS_VERSION = "3.0.0-qt-compatible-physical-step"
_ACCEPTED = {"MATCHED", "CSV"}
_DIRECTIONS = {"charge": "CHARGE", "discharge": "DISCHARGE", "rest": "REST"}
_CYCLE_COLUMNS = ("Cycle", "Voltage_V", "Capacity_mAh", "TypeStd", "_row", "MatchStatus",
                  "StepTime_s", "PhysicalStepID", "Source_File", "StepNo", "OriginalCycle",
                  "Current_mA", "Energy_mWh", "Temp_C")
_AGGREGATIONS = ("representative", "step_sum")
_CURVE_COLUMNS = ("Cycle", "Voltage_V", "Capacity_mAh", "TypeStd", "_row", "MatchStatus",
                  "StepTime_s", "PhysicalStepID", "Source_File", "StepNo", "Current_mA", "_compact_curve")


def analysis_frame(channel: Any, include_review: bool = False, *, frame=None, columns=None) -> pd.DataFrame:
    """Return an isolated, source-ordered frame accepted for calculation."""
    if frame is None:
        reader = getattr(channel, "read_rows", None)
        frame = reader(columns=columns) if reader else channel.frame
    required = {"Cycle", "Voltage_V", "Capacity_mAh", "TypeStd", "_row"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError("분석 필수 컬럼 누락: " + ", ".join(sorted(missing)))
    accepted = _ACCEPTED | ({"REVIEW"} if include_review else set())
    status = frame.get("MatchStatus")
    if status is None:
        mask = np.ones(len(frame), dtype=bool)
    elif isinstance(status.dtype, pd.CategoricalDtype):
        accepted_labels = [label for label in status.cat.categories if str(label).upper() in accepted]
        mask = status.isin(accepted_labels)
    else:
        mask = status.astype(str).str.upper().isin(accepted)
    columns = list(frame.columns.intersection(columns, sort=False)) if columns is not None else slice(None)
    result = frame.loc[mask, columns]
    if not result["_row"].is_monotonic_increasing:
        result = result.sort_values("_row", kind="stable")
    result.attrs.update(frame.attrs)
    result.attrs["excluded_rows"] = int((~mask).sum())
    result.attrs["analysis_version"] = ANALYSIS_VERSION
    return result


def _step_segments(frame: pd.DataFrame) -> list[pd.DataFrame]:
    """Split on actual step, source, missing row, reset and large time gap."""
    if frame.empty:
        return []
    d = frame.sort_values("_row", kind="stable")
    boundary = np.zeros(len(d), dtype=bool)
    boundary[0] = True
    for field in ("PhysicalStepID", "Source_File", "Cycle", "TypeStd", "StepNo"):
        if field in d:
            values = d[field].astype(str).to_numpy()
            boundary[1:] |= values[1:] != values[:-1]
    rows = pd.to_numeric(d["_row"], errors="coerce").to_numpy(dtype=float)
    boundary[1:] |= np.diff(rows) != 1
    if "StepTime_s" in d:
        t = pd.to_numeric(d["StepTime_s"], errors="coerce").to_numpy(dtype=float)
        delta = np.diff(t)
        boundary[1:] |= (delta < -1e-8) | ~np.isfinite(delta)
        # Estimate gaps inside each already identified physical segment only.
        cuts = np.r_[np.flatnonzero(boundary), len(d)]
        for a, b in zip(cuts[:-1], cuts[1:]):
            if "_compact_curve" in d and d["_compact_curve"].iloc[a:b].eq(True).all():
                continue
            dt = np.diff(t[a:b])
            valid = dt[np.isfinite(dt) & (dt > 0)]
            if len(valid) >= 3:
                typical = float(np.median(valid))
                boundary[a + 1:b] |= dt > typical * 10
    cuts = np.r_[np.flatnonzero(boundary), len(d)]
    return [d.iloc[a:b].copy() for a, b in zip(cuts[:-1], cuts[1:])]


def _positive_number(value: Any) -> bool:
    try:
        return bool(np.isfinite(value) and float(value) > 0)
    except (TypeError, ValueError):
        return False


def _step_label(segment: pd.DataFrame) -> str:
    step = str(segment["StepNo"].iloc[0]) if "StepNo" in segment else "?"
    identity = str(segment["PhysicalStepID"].iloc[0]) if "PhysicalStepID" in segment else str(segment["_row"].iloc[0])
    return f"{step} [{identity}]"


def _representative(segments: list[pd.DataFrame], kind: str) -> pd.DataFrame | None:
    active = [s for s in segments if str(s["TypeStd"].iloc[0]).upper() == kind]
    if not active:
        return None
    def rank(s):
        cap = pd.to_numeric(s["Capacity_mAh"], errors="coerce")
        maximum = cap.max()
        return (float(maximum) if pd.notna(maximum) else -np.inf, len(s))
    return max(active, key=rank)


def cycle_summary(channel: Any, baseline_cycle: int | None = None,
                  include_review: bool = False, aggregation: str = "representative") -> pd.DataFrame:
    base = cycle_aggregates(channel, include_review, modes=(aggregation,))[aggregation]
    return apply_cycle_baseline(base, channel.name, baseline_cycle)


def _sum_step_metrics(row, segments):
    for kind, prefix in (("CHARGE", "Charge"), ("DISCHARGE", "Discharge")):
        directional = [s for s in segments if str(s["TypeStd"].iloc[0]) == kind]
        for field in ("Capacity_mAh", "Energy_mWh"):
            values = []
            for segment in directional:
                if field not in segment:
                    continue
                v = pd.to_numeric(segment[field], errors="coerce").dropna()
                if len(v) > 1:
                    values.append(abs(float(v.iloc[-1] - v.iloc[0])))
            row[f"{prefix}_{field}"] = sum(values) if values else np.nan
        cap = row[f"{prefix}_Capacity_mAh"]
        row[f"{prefix}_Avg_Voltage_V"] = row[f"{prefix}_Energy_mWh"] / cap if _positive_number(cap) else np.nan
    for output, field in (("Coulombic_Efficiency_pct", "Capacity_mAh"), ("Energy_Efficiency_pct", "Energy_mWh")):
        denominator = row[f"Charge_{field}"]
        row[output] = row[f"Discharge_{field}"] / denominator * 100 if _positive_number(denominator) else np.nan
    charge_splits = [_cc_cv_split(s, None) for s in segments if str(s["TypeStd"].iloc[0]) == "CHARGE"]
    for output, key in (("CC_Capacity_mAh", "cc_capacity_mAh"), ("CV_Capacity_mAh", "cv_capacity_mAh"),
                        ("CC_Time_s", "cc_time_s"), ("CV_Time_s", "cv_time_s")):
        values = [v[key] for v in charge_splits if np.isfinite(v[key])]
        row[output] = sum(values) if values else np.nan
    cap = row["Charge_Capacity_mAh"]
    row["CC_Capacity_Ratio_pct"] = row["CC_Capacity_mAh"] / cap * 100 if _positive_number(cap) else np.nan


def cycle_aggregates(channel, include_review=False, *, modes=_AGGREGATIONS, cancel=None, cycles=None,
                     allow_empty=False):
    """Prepare compact Cycle tables independently of the retention baseline."""
    if not set(modes).issubset(_AGGREGATIONS):
        raise ValueError("용량 집계는 representative 또는 step_sum이어야 합니다.")
    selected = None
    if cycles is not None:
        ranges = sorted((part for cycle in cycles for part in channel.cycle_ranges[cycle]), key=lambda r: r.start)
        selected = channel.read_rows(ranges, _CYCLE_COLUMNS)
    frame = analysis_frame(channel, include_review, frame=selected, columns=_CYCLE_COLUMNS)
    if frame.empty and not allow_empty:
        raise ValueError("계산에 사용할 확인된 데이터가 없습니다. 매칭 검토 상태를 확인하세요.")
    capacity_rules = getattr(channel, "metadata", {}).get("import_rules", {}).get("capacity", {})
    rows = {mode: [] for mode in modes}
    warnings = {mode: [] for mode in modes}
    for cycle, subset in frame.groupby("Cycle", sort=True, observed=True):
        if cancel and cancel():
            raise InterruptedError("Cycle 집계 취소")
        segments = _step_segments(subset)
        directions = ("CHARGE", "DISCHARGE")
        explicit = {kind: set(capacity_rules.get(kind, [])) for kind in directions}
        matching = {kind: [s for s in segments if str(s["TypeStd"].iloc[0]) == kind
                           and (not explicit[kind] or ("StepNo" in s and str(s["StepNo"].iloc[0]) in explicit[kind]))]
                    for kind in directions}
        chosen = [_representative(matching[kind], kind) for kind in directions]
        combined = [s for kind in directions for s in matching[kind]]
        rest = subset.loc[~subset["TypeStd"].isin(["CHARGE", "DISCHARGE"])]
        selected = pd.concat([rest] + [s for s in chosen if s is not None]).sort_values("_row")
        counts = {k: sum(str(s["TypeStd"].iloc[0]) == k for s in segments) for k in ("CHARGE", "DISCHARGE")}
        try:
            representative = _compute_one_cycle_row(selected, cycle, None, {})
        except Exception as exc:
            representative = {"Cycle": int(cycle), "AnalysisError": str(exc)}
        details = dict(Source_ID=channel.id, Source_File=channel.name,
                   OriginalCycle=subset["OriginalCycle"].iloc[0] if "OriginalCycle" in subset else cycle,
                   Charge_Step_Count=counts["CHARGE"], Discharge_Step_Count=counts["DISCHARGE"],
                   Charge_Step=", ".join(_step_label(s) for s in matching["CHARGE"]) if explicit["CHARGE"]
                               else (_step_label(chosen[0]) if chosen[0] is not None else ""),
                   Discharge_Step=", ".join(_step_label(s) for s in matching["DISCHARGE"]) if explicit["DISCHARGE"]
                                  else (_step_label(chosen[1]) if chosen[1] is not None else ""),
                   Included_Rows=len(subset), Review_Rows=int((subset["MatchStatus"] == "REVIEW").sum()) if "MatchStatus" in subset else 0)
        for mode in modes:
            row = representative.copy()
            message = []
            if any(count > 1 for count in counts.values()):
                message.append("복수 물리 Step: " + ("방향별 최대 용량 Step 사용" if mode == "representative" else "각 Step 증분 합계 사용"))
            for kind in directions:
                if explicit[kind] and not matching[kind]:
                    message.append(f"{kind} 지정 Step 없음: {', '.join(sorted(explicit[kind]))}")
            if (mode == "step_sum" or any(explicit.values())) and "AnalysisError" not in row:
                try:
                    selected_segments = combined if mode == "step_sum" else [
                        seg for kind, representative_step in zip(directions, chosen)
                        for seg in (matching[kind] if explicit[kind] else
                                    [representative_step] if representative_step is not None else [])]
                    _sum_step_metrics(row, selected_segments)
                except Exception as exc:
                    row = {"Cycle": int(cycle), "AnalysisError": str(exc)}
            if row.get("AnalysisError"):
                message.append(f"계산 실패: {row['AnalysisError']}")
            row.setdefault("AnalysisError", "")
            row.update(details, Aggregation=mode, Warnings="; ".join(message))
            rows[mode].append(row)
            warnings[mode].extend(f"Cycle {cycle}: {text}" for text in message)
    result = {mode: pd.DataFrame(rows[mode]) for mode in modes}
    for mode, table in result.items():
        table.attrs.update(warnings=warnings[mode], excluded_rows=frame.attrs.get("excluded_rows", 0))
    return result


def prepare_cycle_tables(channel, include_review, cache_dir, cancel=None):
    stored = getattr(channel.frame, "tables", None)
    if stored is not None:
        return stored(include_review)
    from .ingestion import _cache_read, _cache_write

    payload = json.dumps(["cycle-tables-v1", channel.id, channel.fingerprint, channel.name, include_review])
    key = hashlib.sha256(payload.encode()).hexdigest()
    cached = _cache_read(cache_dir, key)
    if cached is not None:
        representative, step_sum, attrs, _ = cached
        result = dict(representative=representative, step_sum=step_sum)
        for mode, table in result.items():
            table.attrs.update(attrs.get(mode, {}))
        return result
    result = cycle_aggregates(channel, include_review, cancel=cancel)
    _cache_write(cache_dir, key, (result["representative"], result["step_sum"],
                                {mode: table.attrs for mode, table in result.items()}, []), cancel)
    return result


def apply_cycle_baseline(base, channel_name, baseline_cycle=None, *, first_cycle=None):
    warnings = list(base.attrs.get("warnings", []))
    summary = base.copy()
    if "Discharge_Capacity_mAh" not in summary:
        raise ValueError("모든 Cycle 계산에 실패했습니다: " + "; ".join(warnings))
    capacities = pd.to_numeric(summary["Discharge_Capacity_mAh"], errors="coerce")
    if baseline_cycle is not None:
        baseline = summary.loc[summary["Cycle"] == baseline_cycle]
        if baseline.empty:
            raise ValueError(f"{channel_name}: 기준 Cycle {baseline_cycle}가 없습니다.")
        if not _positive_number(baseline["Discharge_Capacity_mAh"].iloc[0]):
            raise ValueError(f"{channel_name}: 기준 Cycle {baseline_cycle}의 유효 방전용량이 없습니다.")
    else:
        valid = (capacities > 0) & np.isfinite(capacities)
        if first_cycle is not None:
            valid &= summary["Cycle"] >= first_cycle
        baseline = summary.loc[valid].iloc[:1]
    if baseline.empty:
        reference, ref_energy, basis = np.nan, np.nan, None
        warnings.append("유효한 방전용량 기준이 없어 유지율을 계산하지 않았습니다.")
    else:
        reference = float(baseline["Discharge_Capacity_mAh"].iloc[0])
        ref_energy = baseline["Discharge_Energy_mWh"].iloc[0] if "Discharge_Energy_mWh" in baseline else np.nan
        basis = int(baseline["Cycle"].iloc[0])
    summary["Discharge_Capacity_Retention_pct"] = summary["Discharge_Capacity_mAh"] / reference * 100
    energies = summary.get("Discharge_Energy_mWh", pd.Series(np.nan, index=summary.index))
    summary["Discharge_Energy_Retention_pct"] = energies / ref_energy * 100 if _positive_number(ref_energy) else np.nan
    summary["Energy_Retention_pct"] = summary["Discharge_Energy_Retention_pct"]
    summary["Baseline_Cycle"] = basis
    summary["Analysis_Version"] = ANALYSIS_VERSION
    summary.attrs.update(warnings=warnings, baseline_cycle=basis)
    return summary


def _cycle_filter(frame: pd.DataFrame, cycles: list[int]) -> pd.DataFrame:
    available = set(frame["Cycle"].dropna().astype(int))
    missing = set(int(c) for c in cycles).difference(available)
    if missing:
        raise ValueError("없는 Cycle: " + ", ".join(map(str, sorted(missing))))
    return frame.loc[frame["Cycle"].isin(cycles)]


def _selected_cycle_frame(channel, cycles, include_review, columns=None):
    ranges = getattr(channel, "cycle_ranges", None)
    if ranges is None:
        return _cycle_filter(analysis_frame(channel, include_review, columns=columns), cycles)
    slices = sorted((part for cycle in set(cycles) for part in ranges.get(cycle, [])),
                    key=lambda part: part.start)
    selected = channel.read_rows(slices, columns)
    return _cycle_filter(analysis_frame(channel, include_review, frame=selected), cycles)


def _filter_step(frame: pd.DataFrame, step: str) -> pd.DataFrame:
    if not str(step).strip():
        return frame
    choices = {v.strip() for v in str(step).split(",") if v.strip()}
    mask = pd.Series(False, index=frame.index)
    for key in ("StepNo", "PhysicalStepID"):
        if key in frame:
            mask |= frame[key].astype(str).isin(choices)
    result = frame.loc[mask]
    if result.empty:
        raise ValueError(f"선택 Step {step}에 해당하는 데이터가 없습니다.")
    return result


def raw_curves(channel: Any, cycles: list[int], x: str = "StepTime_s", y: str = "Voltage_V",
               direction: str = "ALL", step: str = "", include_review: bool = False,
               *, display_budget: int | None = None, cancel=None) -> list[dict]:
    frame = _filter_step(_selected_cycle_frame(channel, cycles, include_review, (*_CURVE_COLUMNS, x, y)), step)
    if x not in frame or y not in frame:
        raise ValueError(f"선택한 축 컬럼이 없습니다: {x}, {y}")
    kind = direction.strip().lower()
    if kind not in ("all", "both", "charge", "discharge", "rest"):
        raise ValueError("충전/방전/Rest/전체 중 하나를 선택하세요.")
    if kind in _DIRECTIONS:
        frame = frame.loc[frame["TypeStd"] == _DIRECTIONS[kind]]
    elif kind == "both":
        frame = frame.loc[frame["TypeStd"].isin(["CHARGE", "DISCHARGE"])]
    curves, previews_needed = [], {}
    for segment in _step_segments(frame):
        if cancel and cancel():
            raise InterruptedError("Raw 분석 취소")
        xv = pd.to_numeric(segment[x], errors="coerce").to_numpy(dtype=float)
        yv = pd.to_numeric(segment[y], errors="coerce").to_numpy(dtype=float)
        # NaN positions remain NaN so renderers do not bridge missing samples.
        good = np.isfinite(xv) & np.isfinite(yv)
        xv = np.where(good, xv, np.nan)
        yv = np.where(good, yv, np.nan)
        if not good.any():
            continue
        cycle = int(segment["Cycle"].iloc[0])
        label = _step_label(segment)
        mode = str(segment["TypeStd"].iloc[0]).lower()
        curves.append(dict(x=xv, y=yv, name=f"{channel.name} · C{cycle} · Step {label} · {mode}",
                           cycle=cycle, direction=mode, step=label, source_id=channel.id, channel=channel.name,
                           raw_indices=segment["_row"].to_numpy(dtype=int), point_kind="interpolated" if "_compact_curve" in segment and segment["_compact_curve"].eq(True).all() else "measured",
                           x_column=x, y_column=y, settings={"include_review": include_review}))
        if display_budget and len(xv) > display_budget and good.all():
            previews_needed[len(curves) - 1] = curves[-1]["raw_indices"]
    if previews_needed:
        read_previews = getattr(channel.frame, 'read_previews', None)
        saved = read_previews(x, y, previews_needed, display_budget) if read_previews else {}
        for i in previews_needed:
            if cancel and cancel():
                raise InterruptedError("Raw 분석 취소")
            curve = curves[i]
            curve["plot_indices"] = saved[i] if i in saved else _display_indices(curve["x"], curve["y"], display_budget)
    return curves


def _display_indices(x, y, budget):
    """Keep ordered X/Y extrema in each bucket and the exact endpoints."""
    bins = max(1, (budget - 2) // 4)
    axis = x if x[-1] >= x[0] else -x
    if axis[-1] > axis[0] and np.all(axis[1:] >= axis[:-1]):
        cuts = np.unique(np.r_[0, np.searchsorted(axis, np.linspace(axis[0], axis[-1], bins + 1)[1:-1]), len(x)])
        indices = [0, len(x) - 1]
        for a, b in zip(cuts[:-1], cuts[1:]):
            values = y[a:b]
            indices.extend((a, b - 1, a + values.argmin(), a + values.argmax()))
        return np.unique(indices)
    width = int(np.ceil(len(x) / max(1, (budget - 2) // 4)))
    count = len(x) // width
    offsets = np.arange(count) * width
    indices = [np.array([0, len(x) - 1])]
    for axis in (x, y):
        blocks = axis[:count * width].reshape(count, width)
        indices.extend((offsets + blocks.argmin(axis=1), offsets + blocks.argmax(axis=1)))
        if count * width < len(x):
            tail = axis[count * width:]
            indices.append(count * width + np.array([tail.argmin(), tail.argmax()]))
    return np.unique(np.concatenate(indices))


def _derivative_settings(n_points, smooth, smooth_window, smooth_polyorder, prominence, minimum_distance):
    if isinstance(n_points, bool) or int(n_points) != n_points or not 50 <= n_points <= 20_000:
        raise ValueError("보간 포인트는 50~20,000 범위의 정수여야 합니다.")
    if not isinstance(smooth_polyorder, (int, np.integer)) or not 1 <= smooth_polyorder <= 5:
        raise ValueError("스무딩 다항식 차수는 1~5 정수여야 합니다.")
    actual_window = max(11, (int(n_points) // 25) | 1) if smooth_window is None else smooth_window
    if not isinstance(actual_window, (int, np.integer)) or actual_window % 2 != 1 or not 5 <= actual_window < n_points:
        raise ValueError("스무딩 윈도는 5 이상, 보간점 미만인 홀수여야 합니다.")
    if smooth_polyorder >= actual_window:
        raise ValueError("다항식 차수는 스무딩 윈도보다 작아야 합니다.")
    if not np.isfinite(prominence) or not 0 < prominence <= 1:
        raise ValueError("피크 민감도는 0 초과 100% 이하이어야 합니다.")
    if not isinstance(minimum_distance, (int, np.integer)) or minimum_distance < 1:
        raise ValueError("피크 최소 간격은 1 이상의 정수여야 합니다.")
    return dict(n_points=int(n_points), smooth=bool(smooth), smooth_window=int(actual_window),
                smooth_polyorder=int(smooth_polyorder), peak_prominence_pct=float(prominence),
                peak_min_distance=int(minimum_distance), analysis_version=ANALYSIS_VERSION)


def derivative_curves(channel: Any, cycles: list[int], direction: str = "both", n_points: int = 500,
                      smooth: bool = True, smooth_window: int | None = None, smooth_polyorder: int = 3,
                      peak_prominence_pct: float = .05, step: str = "", include_review: bool = False,
                      peak_min_distance: int = 5, peak_min_distance_mv: float | None = None,
                      upper_voltage_v: float | None = None, cancel=None, **optional) -> list[dict]:
    """Differentiate one selected contiguous physical Step per direction/Cycle.

    Settings errors raise immediately. A data-specific failure is returned as an
    error curve (empty arrays) so other successful curves remain available.
    """
    automatic_grid = n_points == 0
    settings = _derivative_settings(20000 if automatic_grid else n_points, smooth, smooth_window,
                                    smooth_polyorder, peak_prominence_pct, peak_min_distance)
    if upper_voltage_v is not None and not _positive_number(upper_voltage_v):
        raise ValueError("상한전압은 양의 유한값이어야 합니다.")
    if peak_min_distance_mv is not None and not _positive_number(peak_min_distance_mv):
        raise ValueError("피크 전압 간격은 양의 유한값이어야 합니다.")
    kind = direction.strip().lower()
    if kind not in {"charge", "discharge", "both", "all"}:
        raise ValueError("미분 방향은 charge/discharge/both 중 하나여야 합니다.")
    directions = ["charge", "discharge"] if kind in {"both", "all"} else [kind]
    frame = _filter_step(_selected_cycle_frame(channel, cycles, include_review, _CURVE_COLUMNS), step)
    grouped = frame.groupby(['Cycle', 'TypeStd'], observed=True, sort=False).indices
    output = []
    for cycle in cycles:
        if cancel and cancel():
            raise InterruptedError("미분 분석 취소")
        for mode in directions:
            candidates = []
            positions = grouped.get((cycle, _DIRECTIONS[mode]), [])
            for segment in _step_segments(frame.iloc[positions]):
                # Capacity resets must not become an artificial interpolated curve.
                q = pd.to_numeric(segment["Capacity_mAh"], errors="coerce").to_numpy(dtype=float)
                cuts = np.r_[0, np.flatnonzero(np.diff(q) < -max(1e-6, np.nanmax(np.abs(q)) * 1e-6)) + 1, len(segment)]
                candidates.extend(segment.iloc[a:b].copy() for a, b in zip(cuts[:-1], cuts[1:]) if b > a)
            selected = max(candidates, key=len) if candidates else None
            label = _step_label(selected) if selected is not None else str(step)
            options = dict(settings, include_review=include_review, upper_voltage_v=upper_voltage_v,
                           selected_step=label, selection="largest_contiguous_physical_step",
                           peak_min_distance_mv=peak_min_distance_mv)
            base = dict(name=f"{channel.name} · C{cycle} · Step {label} · {mode}", cycle=int(cycle), channel=channel.name,
                        direction=mode, step=label, source_id=channel.id, point_kind="calculated",
                        settings=options, warnings=[], error="")
            try:
                if selected is None or len(selected) < 5:
                    raise ValueError("유효한 연속 측정점이 5개 미만입니다.")
                finite = np.isfinite(pd.to_numeric(selected["Voltage_V"], errors="coerce")) & np.isfinite(pd.to_numeric(selected["Capacity_mAh"], errors="coerce"))
                if not finite.all():
                    raise ValueError("구간 내부에 결측 전압/용량이 있습니다. 결측 구간을 제외한 Step을 선택하세요.")
                vv = selected["Voltage_V"].to_numpy(dtype=float)
                qq = selected["Capacity_mAh"].to_numpy(dtype=float)
                if mode == "charge":
                    vv, qq = _cc_region_only(vv, qq, upper_voltage_v)
                if len(np.unique(vv)) < 5 or len(np.unique(qq)) < 5 or np.ptp(qq) <= 0:
                    raise ValueError("미분에 필요한 서로 다른 전압/용량 값이 부족합니다.")
                intervals = min(19_999, max(1, int(np.ceil(np.ptp(vv) * 1000)))) if automatic_grid else None
                points = min(20_000, max(5, intervals + 1)) if automatic_grid else n_points
                window = (max(11, (points // 25) | 1) if smooth_window is None else smooth_window)
                if automatic_grid and smooth_window is not None and smooth_window >= points:
                    raise ValueError('스무딩 윈도가 자동 보간점 수보다 작아야 합니다.')
                options.update(n_points=points, smooth_window=window, voltage_spacing_mv=1000 * np.ptp(vv) / (points - 1))
                effective_distance = peak_min_distance
                if peak_min_distance_mv is not None:
                    effective_distance = max(1, int(np.ceil(peak_min_distance_mv / 1000 / (np.ptp(vv) / (points - 1)))))
                result = compute_dqdv(selected, int(cycle), mode, n_points=points, smooth=smooth,
                                      smooth_window=window, smooth_polyorder=smooth_polyorder,
                                      peak_prominence_pct=peak_prominence_pct, peak_min_distance=effective_distance,
                                      upper_voltage_v=upper_voltage_v, compact_intervals=intervals)
                if result is None:
                    raise ValueError("CV 제외 후 유효한 미분 구간이 부족합니다.")
                if not np.isfinite(result["dqdv"]).all() or not np.isfinite(result["dvdq"]).all():
                    raise ValueError("미분 결과에 유한하지 않은 값이 있습니다.")
                options.update(effective_peak_min_distance=effective_distance, original_points=len(selected),
                               voltage_min=float(result["voltage_grid"].min()), voltage_max=float(result["voltage_grid"].max()))
                options["settings_id"] = hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()[:16]
                if len(candidates) > 1:
                    base["warnings"].append(f"{len(candidates)}개 물리 구간 중 최장 구간 선택: {label}")
                peaks = [dict(voltage=float(v), value=float(value), cycle=int(cycle), direction=mode,
                              step=label, source_id=channel.id, settings_id=options["settings_id"])
                         for v, value in zip(result["peak_voltage"], result["peak_dqdv"])]
                base.update(result)
                base.update(x=result["voltage_grid"], y=result["dqdv"],
                            vq_x=result["capacity_on_vgrid"], vq_y=result["voltage_grid"],
                            dvdq_x=result["capacity_grid"], dvdq_y=result["dvdq"], peaks=peaks,
                            peaks_dvdq=[dict(capacity=float(q), value=float(v)) for q, v in zip(result["peak_capacity"], result["peak_dvdq"])],
                            raw_indices=selected["_row"].to_numpy(dtype=int))
            except Exception as exc:
                base.update(error=str(exc), x=np.array([]), y=np.array([]), vq_x=np.array([]), vq_y=np.array([]),
                            dvdq_x=np.array([]), dvdq_y=np.array([]), peaks=[], peaks_dvdq=[], raw_indices=np.array([], dtype=int))
            output.append(base)
    return output


def track_peaks(curves: list[dict], max_shift_v: float = .05) -> pd.DataFrame:
    """Match peaks one-to-one by voltage within each source/direction.

    Missing peaks retain NaN rows; a newly detected peak receives a new ID.
    """
    if not _positive_number(max_shift_v):
        raise ValueError("피크 추적 허용 전압은 양수여야 합니다.")
    groups = {}
    for curve in curves:
        if not curve.get("error"):
            groups.setdefault((curve.get("source_id"), curve.get("direction")), []).append(curve)
    rows = []
    for (source, direction), group in groups.items():
        tracks, next_id = {}, 1
        for curve in sorted(group, key=lambda c: c["cycle"]):
            peaks = sorted(curve.get("peaks", []), key=lambda p: p["voltage"])
            proposals = sorted((abs(value - p["voltage"]), identity, i)
                               for identity, value in tracks.items() for i, p in enumerate(peaks)
                               if abs(value - p["voltage"]) <= max_shift_v)
            used_tracks, used_peaks, assignments = set(), set(), {}
            for distance, identity, i in proposals:
                if identity not in used_tracks and i not in used_peaks:
                    assignments[i] = identity
                    used_tracks.add(identity)
                    used_peaks.add(i)
            for identity in tracks:
                if identity not in used_tracks:
                    rows.append(dict(Source_ID=source, Direction=direction, Cycle=curve["cycle"], Peak_ID=identity,
                                     Voltage_V=np.nan, Value=np.nan, State="missing"))
            for i, peak in enumerate(peaks):
                identity = assignments.get(i)
                state = "matched"
                if identity is None:
                    identity, next_id, state = next_id, next_id + 1, "new"
                tracks[identity] = peak["voltage"]
                rows.append(dict(Source_ID=source, Direction=direction, Cycle=curve["cycle"], Peak_ID=identity,
                                 Voltage_V=peak["voltage"], Value=peak["value"], State=state, Step=curve.get("step")))
    return pd.DataFrame(rows)


def merge_same_cell(channels: list, offsets: list[int], name: str = "연결 시험"):
    """Explicit, reversible same-cell concatenation; overlapping cycles rejected.

    The caller is responsible for the user's same-cell confirmation. Original
    channel frames are never edited. A continuous cycle split between files is
    deliberately rejected rather than guessed or silently double-counted.
    """
    from .models import ChannelData
    if len(channels) < 2 or len(channels) != len(offsets):
        raise ValueError("연결할 채널 2개 이상과 각 채널의 Cycle Offset이 필요합니다.")
    if len({channel.id for channel in channels}) != len(channels):
        raise ValueError("동일한 채널을 중복 연결할 수 없습니다.")
    if any(isinstance(value, bool) or not isinstance(value, (int, np.integer)) for value in offsets):
        raise ValueError("Cycle Offset은 정수여야 합니다.")
    frames, steps, components, used, row_cursor, time_cursor = [], [], [], set(), 0, 0.0
    for channel, offset in zip(channels, offsets):
        reader = getattr(channel, "read_rows", None)
        d = (reader() if reader else channel.frame).copy(deep=True)
        numeric_cycle = pd.to_numeric(d["Cycle"], errors="coerce")
        if numeric_cycle.isna().any():
            raise ValueError(f"{channel.name}: Offset 적용 후 Cycle이 유효하지 않습니다.")
        known_cycle = numeric_cycle >= 0
        shifted = numeric_cycle.where(~known_cycle, numeric_cycle + int(offset))
        if (shifted.loc[known_cycle] < 0).any():
            raise ValueError(f"{channel.name}: Offset 적용 후 실제 Cycle이 음수입니다.")
        # -1 is an unknown-cycle sentinel (INITIAL/UNMATCHED), not a real
        # cycle to shift, validate against other files, or silently fabricate.
        shifted.loc[~known_cycle] = -1
        cycle_set = set(shifted.loc[known_cycle].astype(int))
        overlap = cycle_set & used
        if overlap:
            raise ValueError("연결 후 Cycle 중복: " + ", ".join(map(str, sorted(overlap))) + ". Offset을 조정하세요.")
        used |= cycle_set
        if "OriginalCycle" not in d:
            d["OriginalCycle"] = d["Cycle"]
        if "OriginalSource_ID" not in d:
            d["OriginalSource_ID"] = channel.id
        if "OriginalRow" not in d:
            d["OriginalRow"] = d["_row"]
        d["MergedComponent_ID"] = channel.id
        d["CycleOffset"] = int(offset)
        d["Cycle"] = shifted.astype(int)
        d["_row"] = row_cursor + d["_row"].to_numpy(dtype=int) - int(d["_row"].iloc[0])
        row_cursor = int(d["_row"].iloc[-1]) + 2
        if "PhysicalStepID" in d:
            d["PhysicalStepID"] = channel.id + ":" + d["PhysicalStepID"].astype(str)
        if "TotalTime_s" in d:
            t = pd.to_numeric(d["TotalTime_s"], errors="coerce")
            valid = t.dropna()
            if not valid.empty:
                d["TotalTime_s"] = t - float(valid.iloc[0]) + time_cursor
                time_cursor = float(d["TotalTime_s"].max())
        frames.append(d)
        if not channel.steps.empty:
            s = channel.steps.copy(deep=True)
            if "OriginalSource_ID" not in s:
                s["OriginalSource_ID"] = channel.id
            s["MergedComponent_ID"] = channel.id
            s["CycleOffset"] = int(offset)
            if "Cycle" in s:
                step_cycle = pd.to_numeric(s["Cycle"], errors="coerce")
                if "OriginalCycle" not in s:
                    s["OriginalCycle"] = s["Cycle"]
                valid_step_cycle = step_cycle.notna() & (step_cycle >= 0)
                s["Cycle"] = np.where(valid_step_cycle, step_cycle + int(offset), -1).astype(int)
            if "PhysicalStepID" in s:
                s["PhysicalStepID"] = channel.id + ":" + s["PhysicalStepID"].astype(str)
            steps.append(s)
        components.append(dict(id=channel.id, source_path=channel.source_path, name=channel.name,
                               offset=int(offset), metadata=channel.metadata))
    identity = "merged-" + hashlib.sha256(json.dumps([(c.id, int(o)) for c, o in zip(channels, offsets)]).encode()).hexdigest()[:20]
    return ChannelData(id=identity, name=name, source_path=channels[0].source_path,
                       frame=pd.concat(frames, ignore_index=True),
                       steps=pd.concat(steps, ignore_index=True) if steps else pd.DataFrame(),
                       metadata=dict(input_type="same_cell_merge", components=components, offsets=list(map(int, offsets)),
                                     analysis_version=ANALYSIS_VERSION),
                       warnings=["사용자가 동일 셀로 확인하여 연결한 시험입니다. 원본 Cycle/파일을 별도 보존합니다."])
