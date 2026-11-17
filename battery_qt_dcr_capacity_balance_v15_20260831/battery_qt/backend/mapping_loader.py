# -*- coding: utf-8 -*-
"""
mapping_loader.py
사용자가 화면에서 지정한(또는 자동 추정된) 컬럼 매핑을 이용해 임의의 PNE류 CSV를
cycle_metrics.py / dqdv.py가 기대하는 표준 스키마(DataFrame)로 변환한다.

표준 스키마 (cycle_metrics.py, dqdv.py 와 동일):
    Cycle, TypeStd('CHARGE'/'DISCHARGE'/'REST'), Voltage_V, Current_mA,
    Capacity_mAh, Energy_mWh, StepTime_s, StepNo(optional), Temp_C(optional), _row
"""
from __future__ import annotations
import io
import re
import numpy as np
import pandas as pd

from .unit_normalization import AUTO, normalize_capacity, normalize_current, voltage_to_v

TYPE_CHARGE = "CHARGE"
TYPE_DISCHARGE = "DISCHARGE"
TYPE_REST = "REST"

KEYWORDS = {
    # totcycle(총 누적 사이클, 단조증가)을 curcycle(스텝그룹 내부 카운터, 리셋될 수 있음)보다 우선
    "cycle": ["totcycle", "curcycle", "cycle", "사이클", "싸이클"],
    "stepType": ["type", "구분", "step type", "steptype", "stepname", "step name"],
    "stepNo": ["stepno", "step no", "step_no", "step number", "스텝번호", "스텝 번호"],
    "current": ["current", "전류"],
    "time": ["steptime", "step time", "time", "시간", "passtime"],
}


def _guess_column(headers, keys):
    lower = [str(h).lower() for h in headers]
    for k in keys:
        for i, h in enumerate(lower):
            if k.lower() in h:
                return headers[i]
    return ""


def _guess_voltage_column(headers):
    lower = [str(h).lower() for h in headers]
    for i, h in enumerate(lower):
        if ("voltage" in h or "전압" in h) and "mv" not in h:
            return headers[i]
    for i, h in enumerate(lower):
        if "voltage" in h or "volt" in h or "전압" in h:
            return headers[i]
    return ""


def _guess_capacity_column(headers, kind):
    lower = [str(h).lower() for h in headers]
    for i, h in enumerate(lower):
        if "cap" not in h:
            continue
        looks_dis = ("dis" in h) or ("dchg" in h) or ("방전" in h)
        looks_chg = (("chg" in h) or ("char" in h) or ("충전" in h)) and not looks_dis
        if kind == "chg" and looks_chg:
            return headers[i]
        if kind == "dchg" and looks_dis:
            return headers[i]
    return ""


def _guess_generic_capacity_column(headers):
    lower = [str(h).lower() for h in headers]
    for i, h in enumerate(lower):
        if ("capacity" in h or "용량" in h) and "sum" not in h and "capa." not in h:
            return headers[i]
    for i, h in enumerate(lower):
        if "cap" in h:
            return headers[i]
    return ""


def _guess_energy_column(headers):
    lower = [str(h).lower() for h in headers]
    for k in ["watthour", "energy", "에너지", "전력량"]:
        for i, h in enumerate(lower):
            if k in h:
                return headers[i]
    for i, h in enumerate(lower):
        if "wh" in h:
            return headers[i]
    return ""


def _guess_temp_column(headers):
    lower = [str(h).lower() for h in headers]
    for i, h in enumerate(lower):
        if "temp" in h or "온도" in h:
            return headers[i]
    return ""


def guess_mapping(headers: list[str]) -> dict:
    chg_cap = _guess_capacity_column(headers, "chg")
    dchg_cap = _guess_capacity_column(headers, "dchg")
    mapping = {
        "cycle": _guess_column(headers, KEYWORDS["cycle"]),
        "stepType": _guess_column(headers, KEYWORDS["stepType"]),
        "stepNo": _guess_column(headers, KEYWORDS["stepNo"]),
        "voltage": _guess_voltage_column(headers),
        "current": _guess_column(headers, KEYWORDS["current"]),
        "chgCap": chg_cap,
        "dchgCap": dchg_cap,
        "capacity": _guess_generic_capacity_column(headers),
        "time": _guess_column(headers, KEYWORDS["time"]),
        "energy": _guess_energy_column(headers),
        "temp": _guess_temp_column(headers),
    }
    cap_mode = "split" if (chg_cap and dchg_cap) else "single"
    return {"mapping": mapping, "capMode": cap_mode}


def _is_charge(v) -> bool:
    if v is None:
        return False
    s = str(v).lower()
    if "dchg" in s or "discharge" in s or "방전" in s:
        return False
    return "chg" in s or "charge" in s or "충전" in s


def _is_discharge(v) -> bool:
    if v is None:
        return False
    s = str(v).lower()
    return "dchg" in s or "discharge" in s or "방전" in s


def _parse_time_value(raw):
    """'H:M:S(.ss)' 또는 'D d H:M:S' 또는 순수 숫자(초) 문자열을 초로 변환."""
    if pd.isna(raw):
        return np.nan
    s = str(raw).strip()
    if not s:
        return np.nan
    days = 0
    m = re.match(r"^(\d+)\s*d\s+(.*)$", s, re.IGNORECASE)
    if m:
        days = int(m.group(1))
        s = m.group(2)
    parts = s.split(":")
    if len(parts) >= 3:
        try:
            h, mi, sec = float(parts[0]), float(parts[1]), float(parts[2])
            return days * 86400 + h * 3600 + mi * 60 + sec
        except ValueError:
            return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse_headers_from_text(text: str, skip_lines: int = 0):
    """CSV 텍스트(이미 올바른 인코딩으로 디코딩된 문자열)에서 헤더와 미리보기, 추정 매핑을 반환."""
    lines = text.splitlines()
    sliced = "\n".join(lines[skip_lines:])
    df = pd.read_csv(io.StringIO(sliced))
    headers = [str(c) for c in df.columns.tolist()]
    guess = guess_mapping(headers)
    return {
        "headers": headers,
        "mapping": guess["mapping"],
        "capMode": guess["capMode"],
        "rowCount": len(df),
    }


def detect_pretreat_steps(text: str, skip_lines: int, cycle_col: str, stepno_col: str) -> list:
    """
    대부분의 사이클에는 나타나지 않는(전체 사이클의 50% 미만에서만 등장하는) Step 번호를
    "전처리(포메이션) 구간"으로 간주해 후보 목록을 반환한다.
    """
    lines = text.splitlines()
    sliced = "\n".join(lines[skip_lines:])
    raw = pd.read_csv(io.StringIO(sliced), usecols=lambda c: c in (cycle_col, stepno_col))
    if cycle_col not in raw.columns or stepno_col not in raw.columns:
        return []

    total_cycles = raw[cycle_col].nunique()
    if total_cycles == 0:
        return []

    grouped = raw.groupby(stepno_col)[cycle_col].nunique()
    steps = [str(step) for step, n_cyc in grouped.items() if (n_cyc / total_cycles) < 0.5]
    return steps


def build_df_from_mapping(
    text: str,
    skip_lines: int,
    mapping: dict,
    cap_mode: str,
    exclude_steps: set | None = None,
    exclude_cycles: set | None = None,
    unit_overrides: dict | None = None,
) -> pd.DataFrame:
    """
    매핑 정보를 이용해 표준 스키마 DataFrame을 생성한다.
    """
    exclude_steps = exclude_steps or set()
    exclude_cycles = exclude_cycles or set()
    unit_overrides = unit_overrides or {}

    lines = text.splitlines()
    sliced = "\n".join(lines[skip_lines:])
    raw = pd.read_csv(io.StringIO(sliced))
    raw.columns = [str(c) for c in raw.columns]

    cyc_col = mapping.get("cycle")
    volt_col = mapping.get("voltage")
    if not cyc_col or not volt_col:
        raise ValueError("Cycle, Voltage 컬럼은 반드시 매핑되어야 합니다.")

    # step / cycle 제외 필터
    if mapping.get("stepNo") and exclude_steps:
        raw = raw[~raw[mapping["stepNo"]].astype(str).isin(exclude_steps)]
    if exclude_cycles:
        raw = raw[~raw[cyc_col].astype(str).isin(exclude_cycles)]

    raw = raw.reset_index(drop=True)
    n = len(raw)
    if n == 0:
        raise ValueError("필터링 후 남은 데이터가 없습니다. 제외 조건을 확인해주세요.")

    out = pd.DataFrame()
    out["Cycle"] = pd.to_numeric(raw[cyc_col], errors="coerce")

    out["Voltage_V"] = voltage_to_v(raw[volt_col], volt_col)

    if mapping.get("current"):
        out["Current_mA"], current_source_unit = normalize_current(
            raw, mapping["current"], out["Voltage_V"], unit_overrides.get("current", AUTO),
        )
    else:
        out["Current_mA"] = np.nan
        current_source_unit = "unmapped"

    if mapping.get("time"):
        out["StepTime_s"] = raw[mapping["time"]].apply(_parse_time_value)

    # CC/CP 원본 표기를 보존한다. DCR은 CC의 안정화 전류와 CP의 시점별 전류를 다르게 처리한다.
    if mapping.get("stepType"):
        out["StepMode"] = raw[mapping["stepType"]].astype(str)

    # DCR에서는 같은 충전/방전 방향이 연속되어도 개별 시험 Step을 구분해야 한다.
    # Cycle 분석에는 선택 항목이지만, 매핑되어 있으면 원본 Step 번호를 보존한다.
    if mapping.get("stepNo"):
        out["StepNo"] = raw[mapping["stepNo"]].astype(str)

    if mapping.get("temp"):
        out["Temp_C"] = pd.to_numeric(raw[mapping["temp"]], errors="coerce")

    if mapping.get("energy"):
        e_col = mapping["energy"]
        e_val = pd.to_numeric(raw[e_col], errors="coerce")
        # 내부적으로 mWh 단위로 통일 (mAh * V = mWh 정합)
        out["Energy_mWh"] = e_val if "mwh" in e_col.lower() else e_val * 1000.0

    if cap_mode == "split":
        chg_col, dchg_col = mapping.get("chgCap"), mapping.get("dchgCap")
        if not chg_col or not dchg_col:
            raise ValueError("용량 분리 모드에서는 충전/방전 용량 컬럼을 모두 매핑해야 합니다.")
        capacity_override = unit_overrides.get("capacity", AUTO)
        chg_val, capacity_source_unit = normalize_capacity(
            raw, chg_col, out["Current_mA"], out.get("StepTime_s"), mapping, capacity_override,
        )
        dchg_val, dchg_source_unit = normalize_capacity(
            raw, dchg_col, out["Current_mA"], out.get("StepTime_s"), mapping, capacity_override,
        )
        # 분리 용량 컬럼은 단위가 같아야 한다. 값이 있는 쪽에서 검증된 결과를 대표 단위로 남긴다.
        if "검증" in dchg_source_unit and "검증" not in capacity_source_unit:
            capacity_source_unit = dchg_source_unit
        chg_val = chg_val.fillna(0.0)
        dchg_val = dchg_val.fillna(0.0)
        type_std = np.where(chg_val > 0, TYPE_CHARGE, np.where(dchg_val > 0, TYPE_DISCHARGE, TYPE_REST))
        out["TypeStd"] = type_std
        out["Capacity_mAh"] = np.where(type_std == TYPE_CHARGE, chg_val, dchg_val)
    else:
        step_col, cap_col = mapping.get("stepType"), mapping.get("capacity")
        if not step_col or not cap_col:
            raise ValueError("단일 용량 모드에서는 Step 구분, 용량 컬럼을 모두 매핑해야 합니다.")
        type_std = raw[step_col].apply(
            lambda v: TYPE_CHARGE if _is_charge(v) else (TYPE_DISCHARGE if _is_discharge(v) else TYPE_REST)
        )
        out["TypeStd"] = type_std.values
        capacity, capacity_source_unit = normalize_capacity(
            raw, cap_col, out["Current_mA"], out.get("StepTime_s"), mapping,
            unit_overrides.get("capacity", AUTO),
        )
        out["Capacity_mAh"] = capacity.abs().fillna(0.0)

    out = out.dropna(subset=["Cycle", "Voltage_V"]).reset_index(drop=True)
    out["Cycle"] = out["Cycle"].astype(int)
    out["_row"] = out.index
    out.attrs["current_source_unit"] = current_source_unit
    out.attrs["capacity_source_unit"] = capacity_source_unit

    return out


# ---------------------------------------------------------------------------
# 여러 파일 이어붙이기 (하나의 셀을 중단 후 재시작하며 나눠 기록한 경우)
# ---------------------------------------------------------------------------
def suggest_cycle_offsets(cycle_ranges: list[tuple[int, int]]) -> list[int]:
    """
    각 파일의 (min_cycle, max_cycle)을 순서대로 받아, 이어붙였을 때 사이클 번호가
    자연스럽게 이어지도록(중복/역행 없이) 각 파일에 더할 오프셋을 제안한다.

    규칙: 파일이 이전 파일 사이클 범위와 겹치거나 그보다 낮은 번호로 다시 시작하면
    (재시작 케이스) 이전 파일의 마지막 사이클 다음부터 이어지도록 오프셋을 계산한다.
    이미 이전 파일보다 큰 번호로 시작하면(정상적으로 이어지는 경우) 오프셋 0.
    """
    offsets = []
    running_max = None
    for mn, mx in cycle_ranges:
        if running_max is None:
            offset = 0
        elif mn <= running_max:
            offset = running_max - mn + 1
        else:
            offset = 0
        offsets.append(offset)
        running_max = mx + offset
    return offsets


def combine_multi_file_dfs(dfs: list) -> "pd.DataFrame":
    """
    이미 각각 build_df_from_mapping()으로 표준화된(그리고 필요시 Cycle에 오프셋이 적용된)
    DataFrame들을 순서대로 이어붙여 하나의 연속된 데이터셋으로 만든다.
    (_row는 파일 순서를 그대로 유지하도록 이어붙인 뒤 다시 매겨서, 사이클 내부 정렬/온셋저항
    계산 등 _row 순서에 의존하는 로직이 그대로 정상 동작한다.)
    """
    if not dfs:
        raise ValueError("합칠 데이터가 없습니다.")
    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.reset_index(drop=True)
    combined["_row"] = combined.index
    return combined
