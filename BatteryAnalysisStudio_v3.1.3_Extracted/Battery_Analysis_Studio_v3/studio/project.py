"""Portable JSON projects: settings and provenance, never executable objects."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1


def default_output_dir() -> str:
    """Prefer requested company path, then a usable Windows Downloads folder."""
    candidates = [Path("D:/Downloads")] if os.name == "nt" else []
    candidates.extend([Path.home() / "Downloads", Path.home()])
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            # TemporaryFile retries PermissionError many thousands of times on
            # Windows when ACLs/managed folders disagree with os.access().
            # One explicit exclusive probe gives a bounded, quick fallback.
            probe = candidate / ('.bas_write_probe_' + uuid.uuid4().hex)
            descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            probe.unlink()
            return str(candidate)
        except OSError:
            continue
    return str(Path.cwd())


def _json_value(value: Any):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(v) for v in value]
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    if hasattr(value, "item"):
        return _json_value(value.item())
    raise TypeError(f"프로젝트에 저장할 수 없는 값: {type(value).__name__}")


def save_project(path: str, channels: list, ui_state: dict) -> dict:
    """Atomically save source references and all supplied session settings."""
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    sources = []
    for channel in channels:
        metadata = channel.metadata or {}
        sources.append(dict(id=channel.id, name=channel.name, source_path=str(channel.source_path),
                            sha256=metadata.get("sha256", metadata.get("source_sha256", metadata.get("fingerprint"))),
                            metadata=_json_value(metadata), warnings=list(channel.warnings or [])))
    _save_compact_sources(destination, channels, sources)
    from . import APP_VERSION
    payload = dict(schema_version=SCHEMA_VERSION, application="Battery Analysis Studio", version=APP_VERSION,
                   saved_utc=datetime.now(timezone.utc).isoformat(), sources=sources, ui_state=_json_value(ui_state))
    handle, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return payload


def load_project(path: str) -> dict:
    """Read a versioned project; caller reloads sources and verifies identity."""
    source = Path(path).expanduser().resolve()
    if source.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("프로젝트 설정 파일이 32MB를 초과합니다.")
    with source.open("r", encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("지원하지 않는 프로젝트 형식/버전입니다.")
    if payload.get("application") != "Battery Analysis Studio":
        raise ValueError("Battery Analysis Studio 프로젝트가 아닙니다.")
    if not isinstance(payload.get("sources"), list) or not isinstance(payload.get("ui_state"), dict):
        raise ValueError("프로젝트의 원본 목록 또는 설정이 올바르지 않습니다.")
    for entry in payload["sources"]:
        if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("id", "name", "source_path")):
            raise ValueError("프로젝트 원본 경로/식별 정보가 올바르지 않습니다.")
    payload["project_path"] = str(source)
    return payload


def _save_compact_sources(destination, channels, sources):
    from shutil import copytree
    from .compact_data import CompactStore
    if not channels or not all(isinstance(channel.frame, CompactStore) for channel in channels):
        return
    parent = destination.with_name(destination.name + ".data")
    parent.mkdir(exist_ok=True)
    for channel, entry in zip(channels, sources):
        target = parent / channel.frame.path.name
        if not target.is_dir():
            staging = parent / ("pending-" + uuid.uuid4().hex)
            try:
                copytree(channel.frame.path, staging)
                try:
                    staging.rename(target)
                except OSError:
                    if not target.is_dir():
                        raise
            finally:
                from shutil import rmtree
                rmtree(staging, ignore_errors=True)
        entry["curve_store"] = target.relative_to(destination.parent).as_posix()


def project_channels(project):
    """Return prepared channels directly, or None for a source-based project."""
    from .compact_data import CompactStore
    from .ingestion import _cache_read
    from .models import ChannelData
    parent = Path(project["project_path"]).parent
    entries = project["sources"]
    if not entries or not all(entry.get("curve_store") for entry in entries):
        return None
    result = []
    for entry in entries:
        path = (parent / entry["curve_store"]).resolve()
        if not path.is_relative_to(parent):
            raise ValueError("프로젝트 데이터 저장 경로가 잘못되었습니다.")
        cached = _cache_read(path, "channel")
        if cached is None:
            raise ValueError(f"{entry['name']}: 프로젝트 축소 데이터 폴더를 찾을 수 없습니다. .basproj 파일과 .data 폴더를 함께 보관하세요.")
        result.append(ChannelData(entry["id"], entry["name"], entry["source_path"],
                                  CompactStore(path), cached[1], entry["metadata"], entry.get("warnings", [])))
    return result
