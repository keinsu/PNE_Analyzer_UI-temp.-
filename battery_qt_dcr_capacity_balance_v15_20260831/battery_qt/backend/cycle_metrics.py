# -*- coding: utf-8 -*-
"""
cycle_metrics.py
사이클별 요약 지표 계산 (충/방전 용량, 에너지, 쿨롱효율, CC/CV 구간 분리,
충/방전 시작전압, DCIR(온셋 저항) 추정 등)
"""

from __future__ import annotations
import numpy as np
import pandas as pd

from .pne_parser import TYPE_CHARGE, TYPE_DISCHARGE, TYPE_REST


def _segment(df: pd.DataFrame, cycle: int, type_std: str) -> pd.DataFrame:
    seg = df[(df["Cycle"] == cycle) & (df["TypeStd"] == type_std)].sort_values("_row")
    return seg


def _cc_cv_split(seg: pd.DataFrame, upper_voltage_v: float | None, tol_v: float = 0.002):
    """
    충전 세그먼트를 CC/CV로 분리한다.

    upper_voltage_v: None이면 세그먼트 내 최대 전압을 상한전압으로 자동 감지.
                      값을 주면 그 전압(V)을 상한전압 기준으로 사용.
    tol_v: 상한전압 도달 판정 허용오차 (V)

    반환: dict(cc_capacity_mAh, cv_capacity_mAh, cc_time_s, cv_time_s,
               transition_voltage_v, transition_idx_row)
    """
    if seg.empty:
        return dict(cc_capacity_mAh=np.nan, cv_capacity_mAh=np.nan,
                     cc_time_s=np.nan, cv_time_s=np.nan,
                     transition_voltage_v=np.nan, transition_row=None)

    v = seg["Voltage_V"].values
    cap = seg["Capacity_mAh"].values
    t = seg["StepTime_s"].values if "StepTime_s" in seg.columns else np.arange(len(seg))

    target_v = upper_voltage_v if upper_voltage_v is not None else np.nanmax(v)

    idx_candidates = np.where(v >= (target_v - tol_v))[0]
    if len(idx_candidates) == 0:
        # 상한전압에 도달하지 못한 경우(예: 상한값을 잘못 지정) 전부 CC로 처리
        trans_i = len(seg) - 1
    else:
        trans_i = idx_candidates[0]

    cap_start = cap[0] if len(cap) else 0.0
    t_start = t[0] if len(t) else 0.0

    cc_capacity = cap[trans_i] - cap_start
    cv_capacity = cap[-1] - cap[trans_i]
    cc_time = t[trans_i] - t_start
    cv_time = t[-1] - t[trans_i]

    return dict(
        cc_capacity_mAh=cc_capacity,
        cv_capacity_mAh=cv_capacity,
        cc_time_s=cc_time,
        cv_time_s=cv_time,
        transition_voltage_v=v[trans_i],
        transition_row=int(seg["_row"].values[trans_i]),
    )


def _onset_resistance(df_all: pd.DataFrame, seg: pd.DataFrame, sign: int):
    """
    스텝 시작 시점의 IR(온셋 저항) 추정.
    직전 REST 구간의 마지막 전압(OCV)과, 전류가 인가된 직후(첫 유의미한 전류) 전압의
    차이를 이용해 R = |dV| / |dI| 로 근사 (DC internal resistance, onset 기준).

    sign: 충전=+1, 방전=-1 (전압 변화 방향 해석용, 현재는 절대값 사용이라 미사용이지만 확장 여지를 위해 남김)
    """
    if seg.empty:
        return np.nan, np.nan, np.nan

    first_row = int(seg["_row"].values[0])
    ocv = np.nan
    prev = df_all[(df_all["_row"] < first_row) & (df_all["TypeStd"] == TYPE_REST)]
    if not prev.empty:
        ocv = prev.sort_values("_row")["Voltage_V"].values[-1]
    else:
        # 직전 rest가 없다면 세그먼트 첫 행(전류 인가 전, 보통 0A) 전압을 OCV로 사용
        if seg["Current_mA"].abs().values[0] < 1e-6:
            ocv = seg["Voltage_V"].values[0]

    cur = seg["Current_mA"].values
    volt = seg["Voltage_V"].values
    # 유의미한 전류(설정전류의 절반 이상)가 처음 인가된 지점을 onset으로 정의
    max_abs_i = np.nanmax(np.abs(cur)) if len(cur) else np.nan
    if not np.isfinite(max_abs_i) or max_abs_i == 0:
        return ocv, np.nan, np.nan

    onset_candidates = np.where(np.abs(cur) >= 0.5 * max_abs_i)[0]
    if len(onset_candidates) == 0:
        return ocv, np.nan, np.nan
    onset_i = onset_candidates[0]
    v_onset = volt[onset_i]
    i_onset_A = cur[onset_i] / 1000.0

    if np.isnan(ocv) or i_onset_A == 0:
        r_mohm = np.nan
    else:
        r_mohm = abs((v_onset - ocv) / i_onset_A) * 1000.0  # ohm -> mohm

    return ocv, v_onset, r_mohm


def _compute_one_cycle_row(df, cyc, upper_voltage_v, per_cycle_upper_voltage) -> dict:
    chg = _segment(df, cyc, TYPE_CHARGE)
    dis = _segment(df, cyc, TYPE_DISCHARGE)

    charge_cap = chg["Capacity_mAh"].max() if not chg.empty else np.nan
    discharge_cap = dis["Capacity_mAh"].max() if not dis.empty else np.nan
    charge_e = chg["Energy_mWh"].max() if not chg.empty and "Energy_mWh" in chg else np.nan
    discharge_e = dis["Energy_mWh"].max() if not dis.empty and "Energy_mWh" in dis else np.nan

    ce = (discharge_cap / charge_cap * 100.0) if charge_cap else np.nan
    ee = (discharge_e / charge_e * 100.0) if charge_e else np.nan

    charge_avg_v = (charge_e / charge_cap) if charge_cap else np.nan  # mWh/mAh = V
    discharge_avg_v = (discharge_e / discharge_cap) if discharge_cap else np.nan

    uv = per_cycle_upper_voltage.get(cyc, upper_voltage_v)
    cc_cv = _cc_cv_split(chg, uv)
    cc_ratio = (
        cc_cv["cc_capacity_mAh"] / charge_cap * 100.0
        if charge_cap and np.isfinite(cc_cv["cc_capacity_mAh"])
        else np.nan
    )

    c_ocv, c_onset_v, c_onset_r = _onset_resistance(df, chg, sign=+1)
    d_ocv, d_onset_v, d_onset_r = _onset_resistance(df, dis, sign=-1)

    charge_start_v = chg["Voltage_V"].values[0] if not chg.empty else np.nan
    charge_end_v = chg["Voltage_V"].values[-1] if not chg.empty else np.nan
    discharge_start_v = dis["Voltage_V"].values[0] if not dis.empty else np.nan
    discharge_end_v = dis["Voltage_V"].values[-1] if not dis.empty else np.nan

    temp_vals = pd.concat([chg.get("Temp_C", pd.Series(dtype=float)),
                            dis.get("Temp_C", pd.Series(dtype=float))])
    avg_temp = temp_vals.mean() if len(temp_vals) else np.nan
    min_temp = temp_vals.min() if len(temp_vals) else np.nan
    max_temp = temp_vals.max() if len(temp_vals) else np.nan
    temp_rise = (max_temp - min_temp) if (pd.notna(max_temp) and pd.notna(min_temp)) else np.nan

    chg_temp_vals = chg.get("Temp_C", pd.Series(dtype=float))
    dis_temp_vals = dis.get("Temp_C", pd.Series(dtype=float))
    avg_chg_temp = chg_temp_vals.mean() if len(chg_temp_vals) else np.nan
    avg_dis_temp = dis_temp_vals.mean() if len(dis_temp_vals) else np.nan

    return dict(
        Cycle=cyc,
        Charge_Capacity_mAh=charge_cap,
        Discharge_Capacity_mAh=discharge_cap,
        Charge_Energy_mWh=charge_e,
        Discharge_Energy_mWh=discharge_e,
        Coulombic_Efficiency_pct=ce,
        Energy_Efficiency_pct=ee,
        Charge_Avg_Voltage_V=charge_avg_v,
        Discharge_Avg_Voltage_V=discharge_avg_v,
        CC_Capacity_mAh=cc_cv["cc_capacity_mAh"],
        CV_Capacity_mAh=cc_cv["cv_capacity_mAh"],
        CC_Time_s=cc_cv["cc_time_s"],
        CV_Time_s=cc_cv["cv_time_s"],
        CC_Capacity_Ratio_pct=cc_ratio,
        Transition_Voltage_V=cc_cv["transition_voltage_v"],
        Charge_OCV_Before_V=c_ocv,
        Charge_Onset_Voltage_V=c_onset_v,
        Charge_Onset_R_mOhm=c_onset_r,
        Discharge_OCV_Before_V=d_ocv,
        Discharge_Onset_Voltage_V=d_onset_v,
        Discharge_Onset_R_mOhm=d_onset_r,
        Charge_Start_Voltage_V=charge_start_v,
        Discharge_Start_Voltage_V=discharge_start_v,
        Charge_End_Voltage_V=charge_end_v,
        Discharge_End_Voltage_V=discharge_end_v,
        Avg_Temp_C=avg_temp,
        Min_Temp_C=min_temp,
        Max_Temp_C=max_temp,
        Temp_Rise_C=temp_rise,
        Charge_Avg_Temp_C=avg_chg_temp,
        Discharge_Avg_Temp_C=avg_dis_temp,
    )


def compute_cycle_summary(
    df: pd.DataFrame,
    upper_voltage_v: float | None = None,
    per_cycle_upper_voltage: dict[int, float] | None = None,
) -> pd.DataFrame:
    """
    사이클별 요약 테이블 생성.

    upper_voltage_v: 전체 사이클에 공통으로 적용할 CC/CV 구분 상한전압(V). None이면
                      사이클마다 자동감지(그 사이클 충전 세그먼트의 최대전압).
    per_cycle_upper_voltage: {cycle: 전압(V)} 형태로 특정 사이클만 다른 상한전압을 쓰고
                      싶을 때 사용 (지정 안 된 사이클은 upper_voltage_v 규칙을 따름).

    반환 컬럼:
        Cycle, Charge_Capacity_mAh, Discharge_Capacity_mAh,
        Charge_Energy_mWh, Discharge_Energy_mWh,
        Coulombic_Efficiency_%, Energy_Efficiency_%,
        Charge_Avg_Voltage_V, Discharge_Avg_Voltage_V,
        CC_Capacity_mAh, CV_Capacity_mAh, CC_Time_s, CV_Time_s,
        CC_Capacity_Ratio_%, Transition_Voltage_V,
        Charge_OCV_Before_V, Charge_Onset_Voltage_V, Charge_Onset_R_mOhm,
        Discharge_OCV_Before_V, Discharge_Onset_Voltage_V, Discharge_Onset_R_mOhm,
        Charge_Start_Voltage_V, Discharge_Start_Voltage_V,
        Charge_End_Voltage_V, Discharge_End_Voltage_V,
        Avg_Temp_C,
        Discharge_Capacity_Retention_%

    각 사이클은 독립적으로 계산되며, 데이터 구조가 예상과 달라 특정 사이클에서 계산이
    실패하더라도(예: 전처리 방식이 다른 CSV) 그 사이클만 빈 값(NaN)으로 건너뛰고 나머지는
    정상적으로 계산됩니다 (전체 분석이 중단되지 않음).
    """
    per_cycle_upper_voltage = per_cycle_upper_voltage or {}
    rows = []
    cycles = sorted(df["Cycle"].unique().tolist())
    skipped = []

    for cyc in cycles:
        try:
            rows.append(_compute_one_cycle_row(df, cyc, upper_voltage_v, per_cycle_upper_voltage))
        except Exception:  # noqa: BLE001
            skipped.append(cyc)
            rows.append(dict(Cycle=cyc))  # 빈 행이라도 넣어서 사이클 번호 연속성 유지

    summary = pd.DataFrame(rows)
    if skipped:
        summary.attrs["skipped_cycles"] = skipped

    # 용량유지율: 필터링 후 남은 첫 번째 유효 방전용량을 기준으로 계산한다.
    # 과거에는 무조건 두 번째 행을 기준으로 삼아, 전처리 Cycle이 이미 제외된 파일에서
    # 첫 분석 Cycle의 유지율이 100%가 아닌 문제가 있었다.
    if len(summary) > 0 and "Discharge_Capacity_mAh" in summary.columns:
        valid_caps = summary["Discharge_Capacity_mAh"].dropna()
        valid_caps = valid_caps[valid_caps > 0]
        ref_cap = valid_caps.iloc[0] if not valid_caps.empty else np.nan
        summary["Discharge_Capacity_Retention_pct"] = (
            summary["Discharge_Capacity_mAh"] / ref_cap * 100.0
            if pd.notna(ref_cap) and ref_cap > 0 else np.nan
        )

    return summary
