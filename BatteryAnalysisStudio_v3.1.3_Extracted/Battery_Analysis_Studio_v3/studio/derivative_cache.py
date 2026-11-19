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


def cached_derivatives(channel, cycles, settings, cancel):
    key = json.dumps(["adaptive-derivatives-v1", channel.id, channel.fingerprint,
                      channel.name, cycles, settings], sort_keys=True)
    root = Path(gettempdir()) / "BatteryAnalysisStudio3" / "derivatives"
    path = root / (hashlib.sha256(key.encode()).hexdigest() + ".json.gz")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            curves = json.load(stream)
        for curve in curves:
            for name in _ARRAY_FIELDS.intersection(curve):
                curve[name] = np.asarray(curve[name], dtype=float)
            for alias, field in _ALIASES.items():
                curve[alias] = curve[field]
        return curves
    except (OSError, EOFError, ValueError, TypeError, KeyError):
        pass

    curves = derivative_curves(channel, cycles, cancel=cancel, **settings)
    if not curves or any(curve["error"] for curve in curves):
        return curves
    if cancel and cancel():
        raise InterruptedError("미분 분석 취소")
    temporary = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            with gzip.GzipFile(fileobj=stream, mode="wb", compresslevel=1) as compressed:
                for chunk in json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), default=_json_array).iterencode(
                    [{name: value for name, value in curve.items() if name not in _ALIASES and name != "raw_indices"}
                     for curve in curves]
                ):
                    compressed.write(chunk.encode("utf-8"))
        temporary.replace(path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
    return curves


def _json_array(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Unsupported derivative cache value: {type(value).__name__}")
