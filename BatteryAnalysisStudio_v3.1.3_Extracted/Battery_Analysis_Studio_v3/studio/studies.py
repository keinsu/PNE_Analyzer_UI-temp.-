"""Persistent, offline studies. Original measurements stay in indexed channel stores."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

import numpy as np
import pandas as pd

from .analysis import cycle_aggregates
from .models import ChannelData
from .raw_store import RawStore

_VOLATILE = {"_row", "Source_File", "TotalTime_s"}
_MAPPED = {"Cycle", "OriginalCycle", "TotalCycle", "StepNo", "StepMode",
           "PhysicalStepID", "TypeStd", "MatchStatus", "StateCode"}
_FIELDS = ("representative", "step_sum")
_COMPARE_BATCH_ROWS = 120_000


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path, data):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        staging = Path(handle.name)
        try:
            json.dump(data, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            staging.unlink(missing_ok=True)
            raise
    try:
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def _row_hashes(frame, excluded):
    columns = [column for column in frame.columns if column not in excluded]
    label = json.dumps(columns).encode()
    hashes = pd.util.hash_pandas_object(frame[columns], index=False).to_numpy()
    return label, hashes


def _digest(label, hashes):
    digest = hashlib.sha256(label)
    digest.update(hashes.tobytes())
    return digest.hexdigest()


def _compare_batches(channel, cycles, old_signatures, cancel):
    """Read bounded batches in Cycle order and retain the existing hash format."""
    last = max(map(int, old_signatures)) if old_signatures else None
    batch, size = [], 0
    for cycle in cycles:
        ranges = channel.cycle_ranges[cycle]
        count = sum(part.stop - part.start for part in ranges)
        if batch and size + count > _COMPARE_BATCH_ROWS:
            yield from _hash_batch(channel, batch, old_signatures, last, cancel)
            batch, size = [], 0
        batch.append((cycle, ranges, count))
        size += count
    if batch:
        yield from _hash_batch(channel, batch, old_signatures, last, cancel)


def _hash_batch(channel, batch, old_signatures, last, cancel):
    if cancel():
        raise InterruptedError("Cycle 비교 취소")
    parts = []
    for _, ranges, _ in batch:
        for part in ranges:
            if parts and parts[-1].stop == part.start:
                parts[-1] = slice(parts[-1].start, part.stop)
            else:
                parts.append(part)
    frame = channel.read_rows(parts)
    label, hashes = _row_hashes(frame, _VOLATILE)
    if cancel():
        raise InterruptedError("Cycle 비교 취소")
    start = 0
    raw = None
    for cycle, _, size in batch:
        stop = start + size
        previous = old_signatures.get(str(cycle))
        checksum = _digest(label, hashes[start:stop])
        same = previous is not None and previous["hash"] == checksum
        same_prefix = False
        if same:
            entry = previous
        else:
            if raw is None:
                raw = _row_hashes(frame, _VOLATILE | _MAPPED)
            raw_label, raw_hashes = raw
            entry = {"hash": checksum, "rows": size, "raw_hash": _digest(raw_label, raw_hashes[start:stop])}
            if previous and cycle == last and size >= previous["rows"]:
                same_prefix = _digest(raw_label, raw_hashes[start:start + previous["rows"]]) == previous["raw_hash"]
        yield cycle, entry, same, same_prefix
        start = stop


def _segments_for(cell, entry_id=None):
    return (entry for entry in cell["segments"] if entry["id"] != entry_id)


class Study:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.path = self.directory / "study.json"
        self.data = self.load()

    @classmethod
    def create(cls, root, name):
        name = name.strip()
        if not name:
            raise ValueError("시험 이름을 입력하세요.")
        directory = Path(root) / uuid.uuid4().hex
        directory.mkdir(parents=True)
        _write_json(directory / "study.json", dict(schema=1, name=name, modified=_now(),
                    revision=0, cells=[], rpt=[], history=[], settings={}, undo=None))
        return cls(directory)

    @property
    def cache_dir(self):
        return self.directory / "channels"

    def load(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema") != 1:
            raise ValueError("지원하지 않는 시험 파일 형식입니다.")
        return data

    def commit(self, candidate, *, undoable=True):
        current = self.load()
        if candidate["revision"] != current["revision"]:
            raise ValueError("다른 창에서 시험이 바뀌었습니다. 다시 연 후 적용하세요.")
        candidate = deepcopy(candidate)
        if undoable:
            candidate["undo"] = {name: current[name] for name in ("cells", "rpt")}
        candidate["revision"] += 1
        candidate["modified"] = _now()
        _write_json(self.path, candidate)
        self.data = candidate

    def save_settings(self, state):
        if state != self.data["settings"]:
            candidate = dict(self.data, settings=state)
            self.commit(candidate, undoable=False)

    def undo(self):
        previous = self.data.get("undo")
        if not previous:
            raise ValueError("복원할 직전 변경이 없습니다.")
        candidate = deepcopy(self.data)
        candidate.update(previous, undo=None)
        candidate["history"].append(dict(at=_now(), text="직전 데이터 변경을 복원했습니다."))
        self.commit(candidate, undoable=False)

    def preview_rpt(self, paths, cancel):
        candidate = deepcopy(self.data)
        known = {item["sha256"] for item in candidate["rpt"]}
        messages = []
        for path in paths:
            digest = hashlib.sha256()
            with Path(path).open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    if cancel():
                        raise InterruptedError("RPT 등록 취소")
                    digest.update(block)
            checksum = digest.hexdigest()
            if checksum in known:
                messages.append(f"{Path(path).name}: 이미 등록한 RPT")
                continue
            known.add(checksum)
            candidate["rpt"].append(dict(name=Path(path).name, path=str(path), sha256=checksum, at=_now()))
            messages.append(f"{Path(path).name}: RPT 기록 추가")
        return self._preview(candidate, messages)

    def _preview(self, candidate, messages):
        changed = (candidate["cells"], candidate["rpt"]) != (self.data["cells"], self.data["rpt"])
        if changed:
            candidate["history"].append(dict(at=_now(), text="\n".join(messages)))
        return candidate, messages, changed

    def _store(self, channel, cancel):
        from .compact_data import prepare_channel
        stored = prepare_channel(channel, self.cache_dir, cancel)
        return stored.frame.path.relative_to(self.directory).as_posix()

    def preview_cycles(self, selections, cancel, auxiliary=()):
        """Build an all-or-nothing revision. Selections include explicit cell/segment IDs."""
        candidate = deepcopy(self.data)
        messages = []
        for channel, cell_id, cell_name, start, segment_id in selections:
            if cancel():
                raise InterruptedError("구간 비교 취소")
            cell = next((c for c in candidate["cells"] if c["id"] == cell_id), None)
            if cell is None:
                cell_name = cell_name.strip()
                if not cell_name or any(c["name"].casefold() == cell_name.casefold() for c in candidate["cells"]):
                    raise ValueError("새 셀 이름이 비어 있거나 이미 사용 중입니다. 기존 셀을 선택하세요.")
                cell = dict(id=uuid.uuid4().hex, name=cell_name, segments=[])
                candidate["cells"].append(cell)
            old = next((s for s in cell["segments"] if s["id"] == segment_id), None)
            if segment_id and old is None:
                raise ValueError("선택한 갱신 구간이 없습니다.")
            cycles = channel.cycles
            if not cycles:
                raise ValueError(f"{channel.name}: 분석할 Cycle이 없습니다.")
            if old and cycles[0] != old["source_start"]:
                raise ValueError("갱신 파일의 첫 Cycle이 기존 구간과 다릅니다. 전체 구간 스냅샷을 선택하세요.")
            start = old["start"] if old else int(start)
            offset = start - cycles[0]
            if old and not set(map(int, old["signatures"])).issubset(cycles):
                messages.append(f"{cell['name']}: 이전 또는 일부 Cycle이 빠진 파일입니다. 기존 구간 유지")
                continue
            incoming = {cycle + offset for cycle in cycles}
            for other_cell in candidate["cells"]:
                duplicate = next((s for s in _segments_for(other_cell, segment_id)
                                  if s["digest"] == channel.metadata.get("sha256")), None)
                if duplicate:
                    raise ValueError(f"이미 {other_cell['name']}에 등록한 데이터입니다. 기존 구간 갱신을 선택하세요.")
            occupied = {int(cycle) + s["offset"] for s in _segments_for(cell, segment_id)
                        for cycle in s["signatures"]}
            overlaps = sorted(incoming & occupied)
            if overlaps:
                raise ValueError(f"{cell['name']}: 누적 Cycle 겹침 {', '.join(map(str, overlaps[:12]))}")
            if old and old["store"].endswith("-compact-v1") and old.get("parse_fingerprint") == channel.fingerprint:
                messages.append(f"{cell['name']}: 변경 없음")
                continue
            signatures, changed, revisions, extended = {}, [], [], []
            old_signatures = old["signatures"] if old else {}
            old_last = max(map(int, old_signatures)) if old_signatures else None
            for cycle, entry, same, same_prefix in _compare_batches(channel, cycles, old_signatures, cancel):
                signatures[str(cycle)] = entry
                if not same:
                    changed.append(cycle)
                if not same and str(cycle) in old_signatures:
                    if cycle == old_last and same_prefix:
                        extended.append(cycle + offset)
                    else:
                        revisions.append(cycle + offset)
            if (old_last == cycles[-1] and
                    signatures[str(old_last)]["rows"] < old["signatures"][str(old_last)]["rows"]):
                messages.append(f"{cell['name']}: 마지막 Cycle 기록이 짧은 파일입니다. 기존 구간 유지")
                continue
            rules = channel.metadata.get("import_rules")
            if old and old.get("import_rules") != rules:
                changed = list(cycles)
                messages.append(f"{cell['name']}: 용량 Step 지정 갱신")
            if not changed and old:
                if old["store"].endswith("-compact-v1") and (old["source"], old["channel"]) == (channel.source_path, channel.name):
                    messages.append(f"{cell['name']}: 변경 없음")
                    continue
                messages.append(f"{cell['name']}: 측정값 동일, 원본 위치 정보 갱신")
            tables = deepcopy(old["tables"]) if old else {}
            if changed:
                changed_set = set(changed)
                for review in (False, True):
                    label = str(int(review))
                    if review and not channel.metadata.get("status_counts", {}).get("REVIEW") and not tables.get(label):
                        continue
                    prepared = cycle_aggregates(channel, review, cancel=cancel, cycles=changed,
                                                allow_empty=True)
                    for mode, table in prepared.items():
                        existing = tables.setdefault(label, {}).get(mode, [])
                        rows = json.loads(table.to_json(orient="records", double_precision=15))
                        tables[label][mode] = [r for r in existing if r["Cycle"] not in changed_set] + rows
                if not tables.get("1"):
                    tables["1"] = deepcopy(tables.get("0", {}))
                elif not channel.metadata.get("status_counts", {}).get("REVIEW"):
                    tables["1"] = deepcopy(tables.get("0", {}))
            if not tables.get("0", {}).get("representative"):
                raise ValueError(f"{channel.name}: 정량분석에 사용할 확인된 Cycle이 없습니다.")
            path = self._store(channel, cancel)
            end = max(incoming)
            stored = RawStore(self.directory / path)
            last_row = stored.read([slice(stored.size - 1, stored.size)], ["_row"])
            record = dict(id=segment_id or uuid.uuid4().hex, start=start, end=end,
                          source_start=cycles[0], offset=offset, channel=channel.name,
                          source=channel.source_path, member=channel.metadata.get("source_member", ""),
                          digest=channel.metadata.get("sha256"), parse_fingerprint=channel.fingerprint, store=path,
                          signatures=signatures, tables=tables, import_rules=rules, at=_now(),
                          row_span=int(last_row["_row"].iloc[0]) + 1)
            if old:
                cell["segments"][cell["segments"].index(old)] = record
                messages.append(f"{cell['name']}: {old['start']}–{old['end']} → {start}–{end} Cycle")
            else:
                cell["segments"].append(record)
                messages.append(f"{cell['name']}: {start}–{end} Cycle 등록")
            if extended:
                messages.append("마지막 Cycle 보강: " + ", ".join(map(str, extended)))
            if revisions:
                suffix = f" 외 {len(revisions) - 20}개" if len(revisions) > 20 else ""
                messages.append("기존 Cycle 데이터 변경 · 승인 필요: " + ", ".join(map(str, revisions[:20])) + suffix)
                before = {r["Cycle"] + offset: r.get("Discharge_Capacity_mAh")
                          for r in old["tables"]["0"]["representative"]}
                after = {r["Cycle"] + offset: r.get("Discharge_Capacity_mAh")
                         for r in tables["0"]["representative"]}
                for cycle in revisions[:5]:
                    prior, newer = before.get(cycle), after.get(cycle)
                    if prior != newer:
                        old_text = f"{prior:.6g}" if prior is not None else "없음"
                        new_text = f"{newer:.6g}" if newer is not None else "없음"
                        messages.append(f"{cycle} Cycle 방전용량: {old_text} → {new_text} mAh")
            combined = sorted(incoming | occupied)
            gaps = [f"{a+1}–{b-1}" for a, b in zip(combined, combined[1:]) if b > a + 1]
            if gaps:
                messages.append("누락 Cycle: " + ", ".join(gaps[:12]))
        for channel in auxiliary:
            if cancel():
                raise InterruptedError("파일 등록 취소")
            kind = channel.metadata["import_kind"]
            source_id = channel.id
            old = next((record for record in candidate["rpt"] if record.get("id") == source_id), None)
            if old and old.get("fingerprint") == channel.fingerprint:
                messages.append(f"{channel.name}: 변경 없음")
                continue
            record = dict(id=source_id, name=channel.name, path=channel.source_path,
                          sha256=channel.metadata["sha256"], fingerprint=channel.fingerprint,
                          kind=kind, store=self._store(channel, cancel), at=_now())
            if old:
                candidate["rpt"][candidate["rpt"].index(old)] = record
            else:
                candidate["rpt"].append(record)
            messages.append(f"{channel.name}: {'RPT' if kind == 'rpt' else '기타'} 기록 저장")
        return self._preview(candidate, messages)

    def channels(self):
        channels = []
        for cell in self.data["cells"]:
            store = StudyCellStore(self.directory, cell["segments"])
            if not cell["segments"]:
                continue
            fingerprint = hashlib.sha256(json.dumps(
                [(s["id"], s["digest"], s["offset"], s["store"]) for s in cell["segments"]]).encode()).hexdigest()
            metadata = dict(input_type="study_cell", parse_fingerprint=fingerprint,
                            status_counts={"CSV": 1, "REVIEW": 1})
            channels.append(ChannelData(cell["id"], cell["name"], str(self.path), store,
                                        store.step_table(), metadata, store.warnings))
        from .compact_data import CompactStore
        from .ingestion import _cache_read
        for record in self.data["rpt"]:
            if "store" not in record:
                continue
            path = (self.directory / record["store"]).resolve()
            if not path.is_relative_to(self.directory):
                continue
            try:
                cached = _cache_read(path, "channel")
                if cached:
                    channels.append(ChannelData(record["id"], record["name"], record["path"],
                                                CompactStore(path), cached[1], cached[2]["metadata"], cached[3]))
            except (OSError, ValueError, KeyError):
                continue
        return channels


class StudyCellStore:
    """A virtual cell spanning immutable imports, with no full-frame merge."""

    def __init__(self, directory, segments):
        self.size, self.ranges, self.parts, self.warnings = 0, {}, [], []
        self.segments = sorted(segments, key=lambda s: s["start"])
        row_base = 0
        for segment in self.segments:
            base = row_base
            row_base += segment["row_span"]
            try:
                path = (directory / segment["store"]).resolve()
                if not path.is_relative_to(directory):
                    raise ValueError("잘못된 저장 경로")
                from .compact_data import CompactStore
                raw = CompactStore(path) if path.name.endswith('-compact-v1') else RawStore(path)
            except (OSError, KeyError, ValueError, TypeError):
                self.warnings.append(f"{segment['start']}–{segment['end']} Cycle의 상세 데이터 저장소를 찾을 수 없습니다.")
                continue
            self.parts.append((self.size, base, raw, segment))
            for cycle, runs in raw.ranges.items():
                shifted = cycle + segment["offset"] if cycle >= 0 else cycle
                self.ranges.setdefault(shifted, []).extend(
                    slice(self.size + run.start, self.size + run.stop) for run in runs)
            self.size += raw.size

    def tables(self, review):
        label = str(int(review))
        result = {}
        for mode in _FIELDS:
            frames = []
            for segment in self.segments:
                frame = pd.DataFrame(segment["tables"].get(label, {}).get(mode, []))
                if not frame.empty:
                    frame["Source_Channel"] = segment["channel"]
                    frame["Source_File"] = segment["source"]
                    frame["Source_Cycle"] = frame["Cycle"]
                    frame["Cycle"] += segment["offset"]
                    frame["Cycle_Status"] = np.where(frame["Cycle"] == segment["end"],
                                                     "완료 여부 미확인", "기록 있음")
                    frames.append(frame)
            result[mode] = (pd.concat(frames, ignore_index=True).sort_values("Cycle", kind="stable")
                            if frames else pd.DataFrame())
        return result

    def dcr(self, review):
        from .compact_data import CompactStore, _dcr_snapshot, merge_dcr
        sources = []
        for _, _, raw, segment in self.parts:
            if isinstance(raw, CompactStore):
                detection = raw.dcr(review)
            else:
                source = ChannelData(segment["id"], segment["channel"], segment["source"], raw)
                detection = _dcr_snapshot(source, review, None)
            sources.append((detection, segment["offset"], segment["id"]))
        return merge_dcr(sources)

    def step_table(self):
        from .ingestion import _cache_read
        frames = []
        for _, row_base, raw, segment in self.parts:
            cached = _cache_read(raw.path, "channel")
            if cached is None or cached[1].empty:
                continue
            steps = cached[1].copy()
            if "Cycle" in steps:
                steps["Source_Cycle"] = steps["Cycle"]
                steps["Cycle"] = steps["Cycle"].where(steps["Cycle"] < 0, steps["Cycle"] + segment["offset"])
            for field in ("FirstRecord", "LastRecord"):
                if field in steps:
                    steps[field] += row_base
            if "PhysicalStepID" in steps:
                steps["PhysicalStepID"] = segment["id"] + ":" + steps["PhysicalStepID"].astype(str)
            steps["Source_Channel"] = segment["channel"]
            steps["Source_File"] = segment["source"]
            frames.append(steps)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def read(self, parts=None, columns=None):
        parts = [slice(0, self.size)] if parts is None else parts
        frames = []
        for run in parts:
            for cursor, row_base, raw, segment in self.parts:
                a, b = max(run.start - cursor, 0), min(run.stop - cursor, raw.size)
                if a >= b:
                    continue
                frame = raw.read([slice(a, b)], columns)
                if "Cycle" in frame:
                    cycle = frame["Cycle"]
                    frame["Cycle"] = cycle.where(cycle < 0, cycle + segment["offset"])
                if "_row" in frame:
                    frame["_row"] = row_base + frame["_row"]
                if "PhysicalStepID" in frame:
                    frame["PhysicalStepID"] = segment["id"] + ":" + frame["PhysicalStepID"].astype(str)
                if "Source_File" in frame:
                    frame["Source_File"] = segment["id"] + "::" + frame["Source_File"].astype(str)
                frames.append(frame)
        if frames:
            return frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
        return self.parts[0][2].read([], columns) if self.parts else pd.DataFrame()
