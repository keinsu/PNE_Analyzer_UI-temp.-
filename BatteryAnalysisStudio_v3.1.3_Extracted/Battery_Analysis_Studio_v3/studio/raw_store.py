"""Local, column-addressable exact measurements for large imports."""
from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

PREVIEW_POINTS = 1600
_PREVIEW_AXES = (('StepTime_s', 'Voltage_V'), ('Voltage_V', 'Capacity_mAh'))


def cycle_ranges(values):
    ranges = {}
    if len(values):
        cuts = np.r_[0, np.flatnonzero(values[1:] != values[:-1]) + 1, len(values)]
        for a, b in zip(cuts[:-1], cuts[1:]):
            if pd.notna(values[a]):
                ranges.setdefault(int(values[a]), []).append(slice(int(a), int(b)))
    return ranges


class RawStore:
    """Read specific columns and contiguous row intervals without a full table."""

    def __init__(self, path):
        self.path = Path(path)
        manifest = json.loads((self.path / 'layout.json').read_text(encoding='utf-8'))
        self.size = manifest['size']
        self.schema = manifest['schema']
        self.ranges = {int(cycle): [slice(*bounds) for bounds in parts]
                       for cycle, parts in manifest['ranges'].items()}
        self.range_index = manifest['range_index']
        for i in range(len(self.schema)):
            if not (self.path / f'{i}.npy').is_file():
                raise ValueError('원본 캐시 열 누락')

    @classmethod
    def write(cls, path, frame, cancel, *, build_previews=True):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path.parent, prefix='raw-pending-') as pending:
            staging = Path(pending) / 'data'
            staging.mkdir()
            schema = []
            for i, (name, series) in enumerate(frame.items()):
                if cancel and cancel():
                    raise InterruptedError('원본 캐시 저장 취소')
                labels = None
                if isinstance(series.dtype, pd.CategoricalDtype):
                    labels = series.cat.categories.tolist()
                    values = series.cat.codes.to_numpy()
                    kind = 'category'
                elif pd.api.types.is_numeric_dtype(series.dtype):
                    values = series.to_numpy()
                    kind = 'numeric'
                else:
                    values = series.fillna('').to_numpy(dtype=str)
                    kind = 'text'
                np.save(staging / f'{i}.npy', values, allow_pickle=False)
                schema.append((name, kind, labels))
            canonical = isinstance(frame.index, pd.RangeIndex) and frame.index.equals(pd.RangeIndex(len(frame)))
            if not canonical:
                np.save(staging / 'index.npy', frame.index.to_numpy(), allow_pickle=False)
            ranges = cycle_ranges(frame['Cycle'].to_numpy())
            manifest = dict(size=len(frame), schema=schema, range_index=canonical,
                            ranges={str(cycle): [[part.start, part.stop] for part in parts]
                                    for cycle, parts in ranges.items()})
            (staging / 'layout.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
            if build_previews:
                cls._write_previews(staging, frame, cancel)
            try:
                staging.rename(path)
            except OSError:
                if not path.is_dir():
                    raise
            return cls(path)

    @staticmethod
    def _write_previews(staging, frame, cancel):
        from .analysis import _display_indices
        if 'PhysicalStepID' not in frame:
            return
        steps = frame['PhysicalStepID']
        values = steps.cat.codes.to_numpy() if isinstance(steps.dtype, pd.CategoricalDtype) else steps.to_numpy()
        rows = frame['_row'].to_numpy()
        cuts = np.r_[0, np.flatnonzero((values[1:] != values[:-1]) | (np.diff(rows) != 1)) + 1, len(values)]
        previews = {}
        for x_name, y_name in _PREVIEW_AXES:
            if x_name not in frame or y_name not in frame:
                continue
            x, y = frame[x_name].to_numpy(), frame[y_name].to_numpy()
            for a, b in zip(cuts[:-1], cuts[1:]):
                if cancel and cancel():
                    raise InterruptedError('그래프 미리보기 생성 취소')
                if b - a <= PREVIEW_POINTS or not (np.isfinite(x[a:b]).all() and np.isfinite(y[a:b]).all()):
                    continue
                key = f'{x_name}:{y_name}:{rows[a]}:{rows[b - 1]}:{b - a}'
                previews[key] = _display_indices(x[a:b], y[a:b], PREVIEW_POINTS).astype(np.int32)
        if previews:
            np.savez_compressed(staging / 'previews.npz', **previews)

    @cached_property
    def preview_path(self):
        target = self.path / 'previews.npz'
        return target if target.is_file() else None

    def read_previews(self, x_name, y_name, row_groups, budget):
        if budget != PREVIEW_POINTS or (x_name, y_name) not in _PREVIEW_AXES or self.preview_path is None:
            return {}
        try:
            with np.load(self.preview_path, allow_pickle=False) as previews:
                result = {}
                for i, rows in row_groups.items():
                    key = f'{x_name}:{y_name}:{rows[0]}:{rows[-1]}:{len(rows)}'
                    if key in previews:
                        result[i] = previews[key]
                return result
        except (OSError, ValueError, KeyError):
            return {}

    def read(self, parts=None, columns=None):
        parts = [slice(0, self.size)] if parts is None else parts

        def read_parts(path):
            values = np.load(path, mmap_mode='r', allow_pickle=False)
            if len(parts) == 1:
                return np.array(values[parts[0]])
            return np.concatenate([values[part] for part in parts]) if parts else np.array(values[:0])

        data = {}
        for i, (name, kind, labels) in enumerate(self.schema):
            if columns is not None and name not in columns:
                continue
            values = read_parts(self.path / f'{i}.npy')
            data[name] = pd.Categorical.from_codes(values, labels) if kind == 'category' else values
        if not self.range_index:
            index = read_parts(self.path / 'index.npy')
        elif len(parts) == 1:
            index = pd.RangeIndex(parts[0].start, parts[0].stop)
        else:
            index = np.concatenate([np.arange(part.start, part.stop) for part in parts]) if parts else pd.RangeIndex(0)
        return pd.DataFrame(data, index=index, copy=False)
