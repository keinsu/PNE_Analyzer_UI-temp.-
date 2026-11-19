# -*- coding: utf-8 -*-
"""
pne_parser.py
PNE(PNE Solution) 충방전기 CSV 로그 파일을 읽고 표준화된 DataFrame으로 변환한다.

PNE CSV 포맷 특징
-----------------
- 인코딩: 보통 EUC-KR / CP949 (한글 컬럼값: 충전/방전/휴지 등)
- Type 컬럼   : '충전' / '방전' / '휴지'
- Code 컬럼   : 'STEP 전류 종료'(=CC->CV 종료, 전류컷오프) / 'STEP 전압 종료'(=전압컷오프, 보통 방전종료)
                / 'STEP 시간 종료' / '정상'
- StepNo      : 스텝 그룹 내 스텝 번호. 포뮬레이션 구간과 본 사이클 구간에서 서로 다른 번호를 
                재사용하므로 사이클 인덱스로 사용하면 안 됨.
- CurCycle    : 스텝 패턴 그룹 내부에서의 사이클 카운터 (그룹이 바뀌면 1로 리셋될 수 있음)
- TotCycle    : 시험 시작부터의 총 사이클 카운터. 단조증가. -> 이것을 실제 "Cycle" 인덱스로 사용.
- Capacity(mAh), wattHour(mWh) : 각 스텝(Type) 내부에서 0부터 누적되는 값. 스텝의 마지막 값이
                그 스텝의 총 용량/에너지.
- StepTime(H:M:S) : 스텝 시작부터 경과시간 (h:mm:ss.ss 형식)
"""

from __future__ import annotations
import re
import io
import pandas as pd
import numpy as np

# 후보 인코딩들 (앞에서부터 시도)
CANDIDATE_ENCODINGS = ["euc-kr", "cp949", "utf-8-sig", "utf-8", "cp1252"]

# 내부 표준 라벨 (한글 원문 -> 내부 영문 라벨)
TYPE_CHARGE = "CHARGE"
TYPE_DISCHARGE = "DISCHARGE"
TYPE_REST = "REST"

_TYPE_MAP_CANDIDATES = {
    "충전": TYPE_CHARGE,
    "방전": TYPE_DISCHARGE,
    "휴지": TYPE_REST,
    # 혹시 깨진 인코딩으로 읽혔을 때를 대비한 별칭들 (드문 경우)
}


def _looks_korean_ok(series: pd.Series) -> bool:
    """Type 컬럼이 정상적으로 한글로 디코딩되었는지 확인."""
    vals = set(series.dropna().astype(str).unique())
    return any(v in _TYPE_MAP_CANDIDATES for v in vals)


def _read_csv_auto_encoding(path: str) -> pd.DataFrame:
    last_err = None
    for enc in CANDIDATE_ENCODINGS:
        try:
            df = pd.read_csv(path, encoding=enc)
            if "Type" in df.columns and _looks_korean_ok(df["Type"]):
                return df
            # 컬럼은 읽혔지만 한글이 깨진 경우 -> 다음 인코딩 시도
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    # 마지막 fallback: 첫 인코딩으로라도 반환 (Type 매핑은 이후 실패할 수 있음)
    try:
        return pd.read_csv(path, encoding=CANDIDATE_ENCODINGS[0])
    except Exception:
        if last_err:
            raise last_err
        raise


def _parse_step_time_to_seconds(t) -> float:
    """'H:M:S(.ss)' 형식 문자열을 초(float) 단위로 변환."""
    if pd.isna(t):
        return np.nan
    s = str(t).strip()
    m = re.match(r"^(\d+):(\d+):(\d+(?:\.\d+)?)$", s)
    if not m:
        return np.nan
    h, mi, sec = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(sec)


def load_pne_csv(path: str) -> pd.DataFrame:
    """
    PNE CSV 파일을 읽어 표준화된 DataFrame으로 반환한다.

    반환 DataFrame에는 원본 컬럼에 더해 다음 컬럼이 추가된다:
        - TypeStd       : 'CHARGE' / 'DISCHARGE' / 'REST'
        - Cycle         : TotCycle 값 (분석에 사용할 사이클 인덱스)
        - StepTime_s    : StepTime(H:M:S)을 초로 변환한 값
        - TotTime_s     : TotTime(H:M:S)을 초로 변환한 값
        - Voltage_V     : Voltage(mV) / 1000
        - Current_mA    : Current(mA) 그대로 (부호: 충전 +, 방전 -)
    """
    df = _read_csv_auto_encoding(path)
    df.columns = [c.strip() for c in df.columns]

    if "Type" not in df.columns:
        raise ValueError("CSV에 'Type' 컬럼이 없습니다. PNE 포맷 CSV가 맞는지 확인해주세요.")

    df["TypeStd"] = df["Type"].astype(str).str.strip().map(_TYPE_MAP_CANDIDATES)
    if df["TypeStd"].isna().all():
        raise ValueError(
            "Type 컬럼('충전'/'방전'/'휴지')을 인식하지 못했습니다. "
            "인코딩이 EUC-KR/CP949가 아닌 다른 인코딩일 수 있습니다."
        )

    if "TotCycle" in df.columns:
        df["Cycle"] = df["TotCycle"].astype(int)
    elif "CurCycle" in df.columns:
        df["Cycle"] = df["CurCycle"].astype(int)
    else:
        raise ValueError("CSV에 'TotCycle' 또는 'CurCycle' 컬럼이 없습니다.")

    if "StepTime(H:M:S)" in df.columns:
        df["StepTime_s"] = df["StepTime(H:M:S)"].apply(_parse_step_time_to_seconds)
    if "TotTime(H:M:S)" in df.columns:
        df["TotTime_s"] = df["TotTime(H:M:S)"].apply(_parse_step_time_to_seconds)

    if "Voltage(mV)" in df.columns:
        df["Voltage_V"] = df["Voltage(mV)"].astype(float) / 1000.0
    if "Current(mA)" in df.columns:
        df["Current_mA"] = df["Current(mA)"].astype(float)
    if "Capacity(mAh)" in df.columns:
        df["Capacity_mAh"] = df["Capacity(mAh)"].astype(float)
    if "wattHour(mWh)" in df.columns:
        df["Energy_mWh"] = df["wattHour(mWh)"].astype(float)
    if "Temp('C)" in df.columns:
        df["Temp_C"] = df["Temp('C)"].astype(float)
    elif "Temp(C)" in df.columns:
        df["Temp_C"] = df["Temp(C)"].astype(float)

    # 파일 내 원래 행 순서를 보존하는 정렬 키
    df = df.reset_index(drop=True)
    df["_row"] = df.index

    return df


def get_cycle_list(df: pd.DataFrame) -> list[int]:
    return sorted(df["Cycle"].unique().tolist())
