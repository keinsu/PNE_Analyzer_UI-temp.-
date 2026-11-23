"""Disk cache for displayed derivative results, keyed by source and analysis settings."""
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile, gettempdir
from contextlib import suppress

import numpy as np

from .analysis import derivative_curves


_ARRAY_FIELDS = {
    "voltage_grid", "capacity_on_vgrid", "dqdv", "capacity_grid", "voltage_on_qgrid",
    "dvdq", "peak_voltage", "peak_dqdv", "peak_capacity", "peak_dvdq", "x", "y",
    "vq_x", "vq_y", "dvdq_x", "dvdq_y", "compact_voltage", "compact_capacity",
}
_ALIASES = {
    "x": "voltage_grid", "y": "dqdv", "vq_x": "capacity_on_vgrid", "vq_y": "voltage_grid",
    "dvdq_x": "capacity_grid", "dvdq_y": "dvdq",
}


_STABLE_DATA_ERRORS = {
    "유효한 연속 측정점이 5개 미만입니다.",
    "구간 내부에 결측 전압/용량이 있습니다. 결측 구간을 제외한 Step을 선택하세요.",
    "미분에 필요한 서로 다른 전압/용량 값이 부족합니다.",
    "CV 제외 후 유효한 미분 구간이 부족합니다.",
}


def cached_derivatives(channel, cycles, settings, cancel):
    """Reuse successful Cycles across different overlapping selections."""
    requested = list(map(int, cycles))
    key = json.dumps(["adaptive-derivatives-v2", channel.id, channel.fingerprint,
                      channel.name, settings], sort_keys=True)
    root = (Path(gettempdir()) / "BatteryAnalysisStudio3" / "derivatives" /
            hashlib.sha256(key.encode()).hexdigest())
    results = {}
    pending = []
    for cycle in dict.fromkeys(requested):
        _check_cancel(cancel)
        cached = _read_cycle(root / f"{cycle}.json.gz", cycle)
        if cached is None:
            pending.append(cycle)
        else:
            results[cycle] = cached
    if pending:
        for cycle, curves in _compute_missing(channel, pending, settings, cancel).items():
            _check_cancel(cancel)
            results[cycle] = curves
            if _cacheable_cycle(curves):
                _write_cycle(root / f"{cycle}.json.gz", curves)
    return [curve for cycle in requested for curve in results.get(cycle, ())]


def _cacheable_cycle(curves):
    errors = [curve.get("error") for curve in curves]
    return (any(not error for error in errors) and
            all(not error or error in _STABLE_DATA_ERRORS for error in errors))


def _check_cancel(cancel):
    if cancel and cancel():
        raise InterruptedError("미분 분석 취소")


def _compute_missing(channel, cycles, settings, cancel):
    try:
        curves = derivative_curves(channel, cycles, cancel=cancel, **settings)
    except ValueError as exc:
        # Selection can include a Cycle missing from this channel or entirely
        # excluded from analysis. Keep independent, usable Cycles available.
        message = str(exc)
        if message.startswith("선택 Step "):
            return {cycle: _cycle_error(channel, cycle, exc) for cycle in cycles}
        if not message.startswith("없는 Cycle:"):
            raise
        if len(cycles) == 1:
            return {cycles[0]: _cycle_error(channel, cycles[0], exc)}
        result = {}
        for cycle in cycles:
            _check_cancel(cancel)
            try:
                result[cycle] = derivative_curves(channel, [cycle], cancel=cancel, **settings)
            except ValueError as cycle_error:
                result[cycle] = _cycle_error(channel, cycle, cycle_error)
        return result

    result = {}
    for curve in curves:
        result.setdefault(curve["cycle"], []).append(curve)
    return result


def _cycle_error(channel, cycle, error):
    return [dict(cycle=cycle, error=f"{channel.name} · Cycle {cycle}: {error}")]


def _read_cycle(path, cycle):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            curves = json.load(stream)
        if not _cacheable_cycle(curves) or any(curve.get("cycle") != cycle for curve in curves):
            return None
        for curve in curves:
            if curve.get("error"):
                continue
            for name in _ARRAY_FIELDS.intersection(curve):
                curve[name] = np.asarray(curve[name], dtype=float)
            for alias, field in _ALIASES.items():
                curve[alias] = curve[field]
        return curves
    except (OSError, EOFError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _write_cycle(path, curves):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with gzip.GzipFile(fileobj=stream, mode="wb", compresslevel=1) as compressed:
                payload = [{name: value for name, value in curve.items()
                            if name not in _ALIASES and name != "raw_indices"}
                           for curve in curves]
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_array)
                compressed.write(encoded.encode("utf-8"))
        temporary.replace(path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _json_array(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Unsupported derivative cache value: {type(value).__name__}")
