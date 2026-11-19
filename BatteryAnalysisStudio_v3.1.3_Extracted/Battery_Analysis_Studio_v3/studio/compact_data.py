"""Build the shared curve data and fixed DCR snapshots from a source import."""
from dataclasses import replace
import json
from pathlib import Path
import uuid

import numpy as np
import pandas as pd

from .analysis import _step_segments, analysis_frame, cycle_aggregates
from .raw_store import RawStore
from .project import _json_value
from backend.dcr_analysis import compute_pulse_result, detect_dcr_blocks

DCR_TIMES = (0.1, 0.5, 1.0, 10.0, 30.0, 60.0)
CURVE_FIELDS = ("Voltage_V", "Capacity_mAh", "StepTime_s", "Current_mA")
INTERPOLATED_FIELDS = (*CURVE_FIELDS, "Energy_mWh", "Temp_C", "TotalTime_s")


def _curve_positions(values):
    voltage, capacity, time, current = values.T
    positions = np.arange(len(voltage))
    # Interpolate a crossing between adjacent observations of each 1 mV level.
    # A sparse recorded jump keeps its measured endpoints.
    bins = np.floor(voltage * 1000).astype(np.int64)
    changes = np.flatnonzero(np.diff(bins))
    single = changes[np.abs(np.diff(bins)[changes]) == 1]
    levels = np.maximum(bins[single], bins[single + 1]) / 1000
    crossings = single + np.clip((levels - voltage[single]) /
                                  (voltage[single + 1] - voltage[single]), 0, 1)
    jumps = changes[np.abs(np.diff(bins)[changes]) > 1]
    timed = np.flatnonzero(np.diff(np.floor(time / 30))) + 1
    kept = np.unique(np.r_[0, len(values) - 1, crossings, jumps, jumps + 1, timed])
    # Keep measured vertices where the voltage grid alone misses a change
    # in voltage, current or capacity as a function of time.
    tolerances = (0.00025, max(np.ptp(capacity) * 1e-4, 1e-6), 0,
                  max(np.ptp(current) * 0.005, 0.02))
    for _ in range(4):
        score = np.zeros(len(values))
        for column, tolerance in enumerate(tolerances):
            if tolerance:
                field = values[:, column]
                approximation = np.interp(time, np.interp(kept, positions, time),
                                          np.interp(kept, positions, field))
                score = np.maximum(score, np.abs(field - approximation) / tolerance)
        bad = np.flatnonzero(score > 1.0)
        if not len(bad):
            break
        cells = np.searchsorted(kept, bad)
        ranked = np.argsort(-score[bad], kind="stable")
        _, first = np.unique(cells[ranked], return_index=True)
        refined = np.union1d(kept, bad[ranked[first]])
        if len(refined) == len(kept):
            break
        kept = refined
    return kept if len(kept) < len(positions) else positions


def compact_curves(frame, cancel=None):
    if frame.empty or not set(CURVE_FIELDS).issubset(frame.columns):
        return frame.assign(_compact_curve=True)
    parts = []
    cursor = 0
    for segment in _step_segments(frame):
        if cancel and cancel():
            raise InterruptedError("곡선 준비 취소")
        values = segment.loc[:, list(CURVE_FIELDS)].to_numpy(dtype=float)
        splits = np.zeros(len(segment), dtype=bool)
        status = segment["MatchStatus"].astype(str).to_numpy()
        if len(segment) > 1:
            finite = np.isfinite(values).all(axis=1)
            splits[1:] |= finite[1:] != finite[:-1]
            splits[1:] |= np.diff(values[:, 1]) < -max(1e-6, np.nan_to_num(np.nanmax(
                np.abs(values[:, 1]))) * 1e-6)
            splits[1:] |= status[1:] != status[:-1]
        cuts = np.r_[0, np.flatnonzero(splits), len(segment)]
        for start, stop in zip(cuts[:-1], cuts[1:]):
            run = segment.iloc[start:stop]
            samples = values[start:stop]
            if len(run) < 16 or not np.isfinite(samples).all() or np.any(np.diff(samples[:, 2]) <= 0):
                result = run.copy()
            else:
                positions = _curve_positions(samples)
                result = run.iloc[np.floor(positions).astype(int)].copy()
                for field in INTERPOLATED_FIELDS:
                    if field in run:
                        data = run[field].to_numpy(dtype=float)
                        result[field] = np.interp(positions, np.arange(len(run)), data)
            result["_row"] = np.arange(cursor, cursor + len(result), dtype=np.int64)
            cursor += len(result) + 1
            parts.append(result)
    return pd.concat(parts, ignore_index=True).assign(_compact_curve=True)


def _dcr_snapshot(channel, review, cancel):
    frame = analysis_frame(channel, review,
                           columns=("Cycle", "_row", "Voltage_V", "Capacity_mAh", "Current_mA",
                                    "TypeStd", "MatchStatus", "StepTime_s", "StepNo", "StepMode",
                                    "PhysicalStepID", "Source_File"))
    if frame.empty or "StepTime_s" not in frame or not frame["StepTime_s"].notna().any():
        return {"segments": [], "blocks": []}
    detection = detect_dcr_blocks(frame, cancel=cancel)
    for block in detection["blocks"]:
        for group in block["pulse_groups"]:
            for pulse in group["pulses"]:
                if cancel and cancel():
                    raise InterruptedError("DCR 준비 취소")
                result = compute_pulse_result(frame, detection["segments"], pulse, DCR_TIMES, ordered=frame)
                times = result["raw_time_s"]
                for target in DCR_TIMES[:2]:
                    right = np.searchsorted(times, target)
                    exact = np.isclose(times, target, rtol=0, atol=1e-9).any()
                    bracket = 0 < right < len(times) and times[right] - times[right - 1] <= target + 1e-9
                    if not (exact or bracket):
                        for field in ("r_by_t", "v_by_t", "applied_current_by_t", "delta_current_by_t"):
                            result[field][target] = np.nan
                        result["time_status"][target] = "측정점 부족"
                for field in ("raw_time_s", "raw_voltage_v", "raw_current_mA"):
                    result.pop(field)
                pulse.update(v0=result["v0"], v0_source=result["v0_source"], fixed_dcr=result)
    return _json_value(detection)


class CompactStore(RawStore):
    """Curve data plus summaries computed before reducing the source."""
    def tables(self, review):
        from .ingestion import _cache_read
        cached = _cache_read(self.path, f"summary-{int(review)}")
        if cached is None:
            raise ValueError("프로젝트 Cycle 요약을 읽을 수 없습니다.")
        tables = {"representative": cached[0], "step_sum": cached[1]}
        for name, table in tables.items():
            table.attrs.update(cached[2].get(name, {}))
        return tables

    def dcr(self, review):
        from .ingestion import _cache_read
        cached = _cache_read(self.path, "channel")
        if cached is None:
            raise ValueError("프로젝트 DCR 데이터를 읽을 수 없습니다.")
        return cached[2]["fixed_dcr"][str(int(review))]


def prepare_channel(channel, root, cancel=None, *, components=()):
    from .ingestion import _cache_read, _cache_write
    root = Path(root)
    if isinstance(channel.frame, CompactStore):
        return channel
    path = root / (channel.fingerprint + "-compact-v1")
    try:
        cached = _cache_read(path, "channel")
        if cached is not None:
            return replace(channel, frame=CompactStore(path), metadata=cached[2]["metadata"], warnings=cached[3])
    except (OSError, ValueError, KeyError):
        pass
    staged = root / (path.name + "-" + uuid.uuid4().hex)
    from shutil import rmtree
    try:
        fixed_dcr = {}
        for review in (False, True):
            label = str(int(review))
            has_review = bool(channel.metadata.get("status_counts", {}).get("REVIEW"))
            if not review or has_review or components:
                fixed_dcr[label] = merge_dcr([(part.frame.dcr(review), offset, part.id) for part, offset in components]) if components else _dcr_snapshot(channel, review, cancel)
                tables = _merge_tables(components, review) if components else cycle_aggregates(channel, review, cancel=cancel, allow_empty=True)
            else:
                fixed_dcr[label] = fixed_dcr["0"]
            _cache_write(staged, f"summary-{label}", (tables["representative"], tables["step_sum"],
                             {name: _json_value(table.attrs) for name, table in tables.items()}, []), cancel)
        # RawStore publishes its own immutable directory. Write it below staging
        # and move the metadata into it before the final atomic rename.
        data = staged / "curves"
        RawStore.write(data, channel.read_rows() if components else compact_curves(channel.read_rows(), cancel), cancel, build_previews=False)
        for file in staged.glob("summary-*"):
            file.replace(data / file.name)
        if any(_cache_read(data, f"summary-{label}") is None for label in (0, 1)):
            raise OSError("Cycle 요약을 저장할 수 없습니다.")
        metadata = dict(channel.metadata, compact_curves=True, original_rows=channel.row_count,
                        parse_fingerprint=channel.fingerprint + '-compact-v1')
        _cache_write(data, "channel", (pd.DataFrame(), channel.steps,
                                     {"metadata": metadata, "fixed_dcr": fixed_dcr}, channel.warnings), cancel)
        if _cache_read(data, "channel") is None:
            raise OSError("축소 프로젝트 정보를 저장할 수 없습니다.")
        try:
            data.rename(path)
        except OSError:
            if _cache_read(path, "channel") is None:
                raise
        return replace(channel, frame=CompactStore(path), metadata=metadata)
    finally:
        rmtree(staged, ignore_errors=True)

def _merge_tables(components, review):
    groups = {"representative": [], "step_sum": []}
    for part, offset in components:
        for name, table in part.frame.tables(review).items():
            if not table.empty:
                shifted = table.copy()
                shifted["Cycle"] += offset
                groups[name].append(shifted)
    return {name: (pd.concat(frames, ignore_index=True).sort_values("Cycle", kind="stable")
                   if frames else pd.DataFrame()) for name, frames in groups.items()}


def merge_dcr(sources):
    """Combine independent detections without joining pulses across files."""
    from copy import deepcopy
    result = {"segments": [], "blocks": []}
    for detection, cycle_offset, prefix in sources:
        segments, blocks = deepcopy(detection["segments"]), deepcopy(detection["blocks"])
        segment_offset, block_offset = len(result["segments"]), len(result["blocks"])
        for segment in segments:
            segment["index"] += segment_offset
            if segment.get("cycle") is not None and segment["cycle"] >= 0:
                segment["cycle"] += cycle_offset
            if segment.get("physical_step_id") is not None:
                segment["physical_step_id"] = prefix + ":" + str(segment["physical_step_id"])
        for block in blocks:
            block["block_index"] += block_offset
            for group in block["pulse_groups"]:
                for pulse in group["pulses"]:
                    for name in ("seg", "rest_seg", "return_seg"):
                        if pulse.get(name) is not None:
                            pulse[name] += segment_offset
                    pulse["fixed_dcr"]["seg_index"] += segment_offset
        result["segments"].extend(segments)
        result["blocks"].extend(blocks)
    return result
