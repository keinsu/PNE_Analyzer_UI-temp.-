# -*- coding: utf-8 -*-
"""PNE 내보내기 값의 단위를 분석용 표준 단위로 정규화한다.

표준 단위는 Voltage=V, Current=mA, Capacity=mAh, Power=W다. 일부 PNE 파일은 숫자는
m 단위로 내보내면서 헤더를 A/Ah로 남기므로, 헤더 판별뿐 아니라 전력·시간 정합도도 사용한다.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

import numpy as np
import pandas as pd


AUTO = "auto"


@dataclass(frozen=True)
class UnitDecision:
    factor: float
    label: str


def voltage_to_v(values: pd.Series, column_name: str) -> pd.Series:
    """V/mV 입력을 V로 정규화한다."""
    factor = 0.001 if "mv" in str(column_name).lower().replace(" ", "") else 1.0
    return pd.to_numeric(values, errors="coerce") * factor


def _header_decision(column_name: str, milli_token: str, base_token: str, display_base: str, display_milli: str) -> UnitDecision:
    name = str(column_name).lower().replace(" ", "")
    if milli_token in name:
        return UnitDecision(1.0, display_milli)
    if re.search(rf"(?:\(|\[|_|^){base_token}(?:\)|\]|_|$)", name) or "ampere" in name:
        return UnitDecision(1000.0, display_base)
    return UnitDecision(1.0, f"{display_milli} (assumed)")


def current_header_decision(column_name: str) -> UnitDecision:
    return _header_decision(column_name, "ma", "a", "A", "mA")


def capacity_header_decision(column_name: str) -> UnitDecision:
    return _header_decision(column_name, "mah", "ah", "Ah", "mAh")


def _override_decision(unit: str | None, base_unit: str, milli_unit: str) -> UnitDecision | None:
    if unit == base_unit:
        return UnitDecision(1000.0, f"{base_unit} (사용자 지정)")
    if unit == milli_unit:
        return UnitDecision(1.0, f"{milli_unit} (사용자 지정)")
    return None


def _power_column(headers: list[str]) -> str:
    for header in headers:
        name = str(header).lower().replace(" ", "")
        if ("power" in name or "전력" in name) and "hour" not in name and "wh" not in name:
            return str(header)
    return ""


def _fit_score(actual: pd.Series, predicted: pd.Series) -> float:
    valid = np.isfinite(actual) & np.isfinite(predicted) & (np.abs(actual) > 1e-12) & (np.abs(predicted) > 1e-12)
    if int(valid.sum()) < 2:
        return np.inf
    ratio = np.abs(actual[valid]) / np.abs(predicted[valid])
    return float(np.nanmedian(np.abs(np.log10(ratio))))


def normalize_current(
    raw: pd.DataFrame,
    current_column: str,
    voltage_v: pd.Series,
    override: str = AUTO,
) -> tuple[pd.Series, str]:
    """A/mA 전류를 mA로 정규화하고, 가능하면 전력으로 헤더 불일치를 보정한다."""
    decision = _override_decision(override, "A", "mA") or current_header_decision(current_column)
    current_raw = pd.to_numeric(raw[current_column], errors="coerce")
    if override != AUTO:
        return current_raw * decision.factor, decision.label

    power_column = _power_column(raw.columns.tolist())
    if not power_column:
        return current_raw * decision.factor, decision.label
    power_w = pd.to_numeric(raw[power_column], errors="coerce")
    if "mw" in power_column.lower().replace(" ", ""):
        power_w = power_w * 0.001

    alternate_factor = 1.0 if decision.factor == 1000.0 else 1000.0
    default_score = _fit_score(power_w, voltage_v * current_raw * decision.factor / 1000.0)
    alternate_score = _fit_score(power_w, voltage_v * current_raw * alternate_factor / 1000.0)
    if alternate_score + 1.0 < default_score:
        label = "mA (Power 검증)" if alternate_factor == 1.0 else "A (Power 검증)"
        return current_raw * alternate_factor, label
    return current_raw * decision.factor, decision.label


def _same_physical_step(raw: pd.DataFrame, mapping: dict) -> pd.Series:
    same = pd.Series(True, index=raw.index)
    for key in ("cycle", "stepNo"):
        column = mapping.get(key)
        if column:
            same &= raw[column].eq(raw[column].shift())
    return same


def normalize_capacity(
    raw: pd.DataFrame,
    capacity_column: str,
    current_mA: pd.Series,
    time_s: pd.Series | None,
    mapping: dict,
    override: str = AUTO,
) -> tuple[pd.Series, str]:
    """Ah/mAh 용량을 mAh로 정규화하고, 가능하면 전류×시간으로 헤더 불일치를 보정한다."""
    decision = _override_decision(override, "Ah", "mAh") or capacity_header_decision(capacity_column)
    capacity_raw = pd.to_numeric(raw[capacity_column], errors="coerce")
    if override != AUTO or time_s is None:
        return capacity_raw * decision.factor, decision.label

    delta_time = time_s.diff()
    expected_mAh = current_mA.abs() * delta_time / 3600.0
    delta_capacity = capacity_raw.diff().abs()
    same_step = _same_physical_step(raw, mapping)
    valid = same_step & (delta_time > 0) & (delta_capacity > 1e-12) & (expected_mAh > 1e-9)
    if int(valid.sum()) < 2:
        return capacity_raw * decision.factor, decision.label

    alternate_factor = 1.0 if decision.factor == 1000.0 else 1000.0
    default_score = _fit_score(expected_mAh[valid], delta_capacity[valid] * decision.factor)
    alternate_score = _fit_score(expected_mAh[valid], delta_capacity[valid] * alternate_factor)
    if alternate_score + 1.0 < default_score:
        label = "mAh (전류·시간 검증)" if alternate_factor == 1.0 else "Ah (전류·시간 검증)"
        return capacity_raw * alternate_factor, label
    return capacity_raw * decision.factor, decision.label
