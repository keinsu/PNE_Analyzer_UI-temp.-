# 기존 Go 해독기와 Python 해독기 실행 비교

검증일: 2026-11-18

- 기존 Go 소스 기준 커밋: `13766d27bc73b1e82ef06c7ea259d5609e1d313a`
- 도구: `go1.27.4 windows/amd64` (공식 ZIP SHA256 확인, 임시 폴더 실행)
- 기존 Go 단위 테스트: 18개 통과 / 0개 실패
- 시스템 Go 설치·레지스트리 변경 없이 휴대용 도구를 사용했다.

## 실행 결과

| 합성 입력 | 채널 | Raw 행 | Step 구간 | 비교 결과 |
|---|---:|---:|---:|---|
| v3_demo | 2 | 924 | 56 | 일치 |
| original_go_fixture | 3 | 45 | 12 | 일치 |
| initial_cycle_restart | 1 | 10 | 4 | 일치 |

## 비교 항목과 허용오차

- Raw 18개 수치 필드: 전압·전류·용량·에너지·시간·온도·저항·미검증 날짜 후보 등.
- 원본 StepNo·CurCycle·TotalCycle·StepType·StateCode·매칭 상태와 Step별 원본 행 경계를 대조했다.
- Go CSV는 소수점 6자리로 저장한다. Raw 단위에서 반올림 허용오차 0.500001e-6와 부동소수점 변환 오차를 적용했다.
- Python 수치값은 별도의 struct.unpack_from('<f') 파서로 CYC 바이트를 직접 읽은 값과 정확히 비교했다. Go CSV 결과의 일치를 비트 단위 동등성으로 표현하지 않는다.
- Go의 `INITIALIZATION` 표시는 Python의 `INITIAL`과 같은 제외 상태로 정규화하여 비교했다.
- 반복 CurCycle의 사용자 표시 번호는 Python에서 별도 발생 순서로 관리한다. 이 비교에서는 변경하지 않은 원본 CurCycle·TotalCycle을 대조했다.

## 검증 범위

**모든 입력은 합성 데이터이다. 실제 PNE 장비 데이터 검증은 수행하지 않았다.**

이 결과는 해독·StepEnd 결합의 실행 결과 비교이며, Go와 Qt의 모든 분석식이 동일하다는 의미는 아니다. Cycle 에너지 집계·DCR 휴지 전류 처리 등 분석 정의의 차이는 별도로 관리한다.

상세 결과: `validation/go_comparison.json`. 기존 Go 테스트 로그: `validation/go_tests.jsonl`.
재실행: `python validation/compare_go_reference.py --go-exe validation/pne_go_reference.exe`.
