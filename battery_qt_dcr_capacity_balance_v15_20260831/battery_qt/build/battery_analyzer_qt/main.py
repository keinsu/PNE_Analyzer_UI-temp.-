# -*- coding: utf-8 -*-
"""
main.py
배터리 충방전 사이클 분석기 — 순수 네이티브 Qt 데스크톱 앱 진입점.

브라우저 엔진(WebView2/Chromium)을 전혀 사용하지 않습니다. 화면은 전부 PySide6 위젯 +
PyQtGraph 차트이고, 계산(pandas/scipy)과 화면이 같은 프로세스에서 직접 함수 호출로
연결되어 있어 웹 브리지가 필요 없습니다.
"""
from __future__ import annotations

import csv
import os
import sys
import traceback

import numpy as np
import pandas as pd

if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QLabel, QPushButton, QMessageBox, QFileDialog, QInputDialog
)
from PySide6.QtCore import Qt

from ui import theme
from ui.home_page import HomePage
from ui.upload_page import UploadPage
from ui.mapping_page import MappingPage
from ui.report_page import ReportPage
from ui.merge_page import MergePage
from ui.compare_page import ComparePage
from ui.dcr_review_page import DCRReviewPage
from ui.dcr_report_page import DCRReportPage
from ui.widgets import parse_cycle_spec

from backend.mapping_loader import (
    parse_headers_from_text, build_df_from_mapping, detect_pretreat_steps,
    suggest_cycle_offsets, combine_multi_file_dfs,
)
from backend.cycle_metrics import compute_cycle_summary
from backend.dqdv import compute_dqdv, compute_all_cycle_peaks, compute_rest_dvdt, compute_multi_cycle_dqdv, compute_multi_cycle_rest_dvdt
from backend.dcr_analysis import detect_dcr_blocks, compute_dcr_table


def _read_text(path: str, encoding: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()

    # PNE 계열 CSV의 한글 메타데이터는 EUC-KR 또는 그 상위 호환인 CP949로 저장되는
    # 경우가 많다. CP949를 먼저 쓰면 EUC-KR 파일도 함께 읽을 수 있고, 대체문자(�)가
    # 섞여 Type/Step 명칭이 깨지는 일을 줄일 수 있다.
    codecs = ["cp949", "euc_kr"] if encoding == "euc-kr" else ["utf-8-sig", "utf-8"]
    last_error = None
    for codec in codecs:
        try:
            return raw.decode(codec)
        except UnicodeDecodeError as exc:
            last_error = exc

    label = "EUC-KR / CP949" if encoding == "euc-kr" else "UTF-8"
    raise ValueError(f"파일을 {label} 인코딩으로 읽을 수 없습니다. 인코딩 선택을 확인해주세요.") from last_error


def _clean(v):
    """CSV로 내보낼 때 NaN을 빈 문자열로."""
    if v is None:
        return ""
    try:
        if isinstance(v, float) and np.isnan(v):
            return ""
    except TypeError:
        pass
    return v


def _parse_step_no_spec(text: str) -> set[str]:
    """쉼표 목록과 숫자 범위를 SOC setting StepNo 집합으로 변환한다."""
    result = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start, end = (int(value.strip()) for value in part.split("-", 1))
                result.update(str(value) for value in range(min(start, end), max(start, end) + 1))
                continue
            except ValueError:
                pass
        result.add(part)
    return result


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("배터리 충방전 사이클 분석기")
        self.resize(1440, 940)
        self.setMinimumSize(1100, 720)

        self.csv_text = ""
        self.file_path = ""
        self.file_name = ""
        self.encoding = "euc-kr"
        self.skip_lines = 0
        self.df = None
        self.summary_df = None
        self.peak_sensitivity_pct = 0.05
        self.upper_voltage = None

        # 여러 파일 이어붙이기(하나의 셀, 중단 후 재시작) 대기 상태
        self.pending_multi = False
        self.multi_file_paths = []
        self.multi_file_texts = []   # 원본 순서 그대로의 텍스트 리스트
        self.multi_file_names = []
        self.multi_mapping_params = None  # 병합 확인 화면에서 쓸, 매핑 확정 시점의 params
        self._pending_multi_dfs = []

        # DCR 분석용 상태 (Cycle 분석과 독립)
        self.dcr_csv_text = ""
        self.dcr_file_path = ""
        self.dcr_file_name = ""
        self.dcr_files = []  # [{"path": str, "name": str, "text": str}] — 같은 매핑으로 병합 분석
        self.dcr_encoding = "euc-kr"
        self.dcr_skip_lines = 0
        self.dcr_df = None
        self.dcr_detection = None
        self.dcr_results = None
        self.dcr_time_points = []
        self.dcr_mapping_params = None
        self.dcr_review_params = None

        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self.outerStack = QStackedWidget()
        outer_layout.addWidget(self.outerStack)

        self.homePage = HomePage()
        self.homePage.cycleAnalysisSelected.connect(self.show_cycle_app)
        self.homePage.dcrAnalysisSelected.connect(self.show_dcr_app)
        self.outerStack.addWidget(self.homePage)

        cycleAppWidget = QWidget()
        root = QVBoxLayout(cycleAppWidget)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        eyebrow = QLabel("PNE · CYCLE TESTER")
        eyebrow.setProperty("role", "eyebrow")
        title = QLabel("충방전 데이터 자동 분석기")
        title.setStyleSheet(f"font-family:'{theme.FONT_DISPLAY}'; font-size:22px; font-weight:700;")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        header.addLayout(title_box)
        header.addStretch(1)
        self.btnHome = QPushButton("🏠 홈으로")
        self.btnHome.clicked.connect(self.go_home)
        header.addWidget(self.btnHome)
        self.btnAddToCompare = QPushButton("📊 이 셀 비교에 추가")
        self.btnAddToCompare.clicked.connect(self.on_add_to_compare)
        self.btnAddToCompare.setVisible(False)
        header.addWidget(self.btnAddToCompare)
        self.btnShowCompare = QPushButton("셀 비교 보기 (0)")
        self.btnShowCompare.clicked.connect(self.show_compare_page)
        header.addWidget(self.btnShowCompare)
        self.btnReset = QPushButton("새 파일 업로드")
        self.btnReset.clicked.connect(self.reset_all)
        self.btnReset.setVisible(False)
        header.addWidget(self.btnReset)
        root.addLayout(header)

        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        self.uploadPage = UploadPage()
        self.mappingPage = MappingPage()
        self.mergePage = MergePage()
        self.reportPage = ReportPage()
        self.comparePage = ComparePage()
        self.stack.addWidget(self.uploadPage)
        self.stack.addWidget(self.mappingPage)
        self.stack.addWidget(self.mergePage)
        self.stack.addWidget(self.reportPage)
        self.stack.addWidget(self.comparePage)

        self.uploadPage.fileSelected.connect(self.on_file_selected)
        self.uploadPage.multiFilesSelected.connect(self.on_multi_files_selected)
        self.mappingPage.reparseRequested.connect(self.on_reparse_requested)
        self.mappingPage.pretreatDetectRequested.connect(self.on_pretreat_detect_requested)
        self.mappingPage.startAnalysis.connect(self.on_start_analysis)
        self.mergePage.mergeConfirmed.connect(self.on_merge_confirmed)
        self.mergePage.cancelRequested.connect(self.reset_all)

        self.reportPage.cycleChanged.connect(self.on_cycle_changed)
        self.reportPage.peakSensitivityChanged.connect(self.on_peak_sensitivity_changed)
        self.reportPage.exportVQRequested.connect(self.export_vq)
        self.reportPage.exportDQDVRequested.connect(self.export_dqdv)
        self.reportPage.exportTableRequested.connect(self.export_table)
        self.reportPage.exportExcelRequested.connect(self.export_excel)
        self.reportPage.exportAllRequested.connect(self.export_all)
        self.reportPage.exportCapacityRequested.connect(self.export_capacity)
        self.reportPage.exportEnergyRequested.connect(self.export_energy)
        self.reportPage.exportTempRequested.connect(self.export_temp)
        self.reportPage.exportCCCVRequested.connect(self.export_cccv)
        self.reportPage.exportVTrendRequested.connect(self.export_vtrend)
        self.reportPage.exportPeaksTrendRequested.connect(self.export_peaks_trend)
        self.reportPage.exportDVDQRequested.connect(self.export_dvdq)
        self.reportPage.exportRestDvdtRequested.connect(self.export_rest_dvdt)
        self.reportPage.multiDqdvApplyRequested.connect(self.on_multi_dqdv_apply)
        self.reportPage.exportMultiDqdvRequested.connect(self.export_multi_dqdv)
        self.reportPage.multiRestApplyRequested.connect(self.on_multi_rest_apply)
        self.reportPage.exportMultiRestRequested.connect(self.export_multi_rest)

        self.comparePage.addCellRequested.connect(self.on_add_cell_requested)
        self.comparePage.addBatchCellsRequested.connect(self.on_add_batch_cells)
        self.comparePage.cellsChanged.connect(self.update_compare_button)
        self.comparePage.exportCapacityRequested.connect(lambda: self.export_compare_chart("Discharge_Capacity_mAh", "compare_capacity.csv"))
        self.comparePage.exportRetentionRequested.connect(lambda: self.export_compare_chart("Discharge_Capacity_Retention_pct", "compare_retention.csv"))
        self.comparePage.exportCERequested.connect(lambda: self.export_compare_chart("Coulombic_Efficiency_pct", "compare_ce.csv"))
        self.comparePage.exportResistanceRequested.connect(lambda: self.export_compare_chart("Discharge_Onset_R_mOhm", "compare_resistance.csv"))
        self.comparePage.exportTempRequested.connect(lambda: self.export_compare_chart("Avg_Temp_C", "compare_temp.csv"))
        self.comparePage.exportTableRequested.connect(self.export_compare_table)

        self.stack.setCurrentWidget(self.uploadPage)

        self.outerStack.addWidget(cycleAppWidget)

        # ---------------- DCR 분석 앱 ----------------
        dcrAppWidget = QWidget()
        dcrRoot = QVBoxLayout(dcrAppWidget)
        dcrRoot.setContentsMargins(28, 24, 28, 24)
        dcrRoot.setSpacing(16)

        dcrHeader = QHBoxLayout()
        dcrTitleBox = QVBoxLayout()
        dcrEyebrow = QLabel("PNE · DCR PULSE TEST")
        dcrEyebrow.setProperty("role", "eyebrow")
        dcrTitle = QLabel("DCR(저항) 분석")
        dcrTitle.setStyleSheet(f"font-family:'{theme.FONT_DISPLAY}'; font-size:22px; font-weight:700;")
        dcrTitleBox.addWidget(dcrEyebrow)
        dcrTitleBox.addWidget(dcrTitle)
        dcrHeader.addLayout(dcrTitleBox)
        dcrHeader.addStretch(1)
        self.btnDcrHome = QPushButton("🏠 홈으로")
        self.btnDcrHome.clicked.connect(self.go_home)
        dcrHeader.addWidget(self.btnDcrHome)
        self.btnDcrReset = QPushButton("새 파일 업로드")
        self.btnDcrReset.clicked.connect(self.reset_dcr)
        self.btnDcrReset.setVisible(False)
        dcrHeader.addWidget(self.btnDcrReset)
        dcrRoot.addLayout(dcrHeader)

        dcrInputNotice = QLabel(
            "입력 데이터 형식: [저장된 모든 Data] Raw Data  ·  시간별 전압·전류·용량 원시 데이터가 필요합니다 "
            "([Step 종료값] 형식은 추후 지원 예정)"
        )
        dcrInputNotice.setWordWrap(True)
        dcrInputNotice.setStyleSheet(
            f"color:{theme.TEAL}; font-family:'{theme.FONT_MONO}'; font-size:11px; "
            "border:1px solid #1f6b68; border-radius:5px; padding:8px 10px;"
        )
        dcrRoot.addWidget(dcrInputNotice)

        self.dcrStack = QStackedWidget()
        dcrRoot.addWidget(self.dcrStack)

        self.dcrUploadPage = UploadPage()
        self.dcrUploadPage.set_dcr_mode()
        self.dcrMappingPage = MappingPage()
        self.dcrReviewPage = DCRReviewPage()
        self.dcrReportPage = DCRReportPage()
        self.dcrStack.addWidget(self.dcrUploadPage)
        self.dcrStack.addWidget(self.dcrMappingPage)
        self.dcrStack.addWidget(self.dcrReviewPage)
        self.dcrStack.addWidget(self.dcrReportPage)

        self.dcrUploadPage.fileSelected.connect(self.on_dcr_file_selected)
        self.dcrUploadPage.multiFilesSelected.connect(self.on_dcr_multi_files_selected)
        self.dcrMappingPage.reparseRequested.connect(self.on_dcr_reparse_requested)
        self.dcrMappingPage.pretreatDetectRequested.connect(self.on_dcr_pretreat_detect_requested)
        self.dcrMappingPage.startAnalysis.connect(self.on_dcr_mapping_confirmed)
        self.dcrReviewPage.redetectRequested.connect(self.on_dcr_redetect)
        self.dcrReviewPage.startAnalysisRequested.connect(self.on_dcr_review_confirmed)
        self.dcrReviewPage.cancelRequested.connect(self.reset_dcr)
        self.dcrReportPage.exportTableRequested.connect(self.export_dcr_table)
        self.dcrReportPage.exportChartRequested.connect(self.export_dcr_chart)
        self.dcrReportPage.exportSummaryRequested.connect(self.export_dcr_summary)
        self.dcrReportPage.addFilesRequested.connect(self.on_dcr_result_add_files)
        self.dcrReportPage.exportAllRequested.connect(self.export_dcr_all)

        self.dcrStack.setCurrentWidget(self.dcrUploadPage)
        self.outerStack.addWidget(dcrAppWidget)

        self.outerStack.setCurrentWidget(self.homePage)

    # ------------------------------------------------------------------
    def show_cycle_app(self):
        self.outerStack.setCurrentIndex(1)

    def show_dcr_app(self):
        self.outerStack.setCurrentIndex(2)

    def go_home(self):
        self.outerStack.setCurrentWidget(self.homePage)

    # ------------------------------------------------------------------
    def reset_all(self):
        self.csv_text = ""
        self.file_path = ""
        self.df = None
        self.summary_df = None
        self.pending_multi = False
        self.multi_file_paths = []
        self.multi_file_texts = []
        self.multi_file_names = []
        self.multi_mapping_params = None
        self._pending_multi_dfs = []
        self.uploadPage.set_error("")
        self.btnReset.setVisible(False)
        self.btnAddToCompare.setVisible(False)
        self.stack.setCurrentWidget(self.uploadPage)

    def _show_error(self, msg: str):
        QMessageBox.critical(self, "오류", msg)

    # ------------------------------------------------------------------
    # stage 1 -> 2
    # ------------------------------------------------------------------
    def on_file_selected(self, path: str, encoding: str, skip_lines: int):
        try:
            self.pending_multi = False
            self.multi_file_texts = []
            self.multi_file_names = []
            self.file_path = path
            self.file_name = os.path.basename(path)
            self.encoding = encoding
            self.skip_lines = skip_lines
            self.csv_text = _read_text(path, encoding)

            result = parse_headers_from_text(self.csv_text, skip_lines=skip_lines)
            self.mappingPage.set_file_badge(self.file_name)
            self.mappingPage.set_encoding_skip(encoding, skip_lines)
            self.mappingPage.set_headers_and_mapping(
                result["headers"], result["mapping"], result["capMode"], result.get("rowCount")
            )
            self.mappingPage.set_error("")
            self.btnReset.setVisible(True)
            self.stack.setCurrentWidget(self.mappingPage)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.uploadPage.set_error(f"파일을 처리하지 못했습니다: {e}")

    def on_multi_files_selected(self, paths: list, encoding: str, skip_lines: int):
        """하나의 셀을 여러 파일로 나눠 기록한 경우: 모든 파일을 읽어두고, 첫 파일 기준으로
        컬럼 매핑을 확인받은 뒤(mapping 단계) 병합 확인 화면으로 넘어간다."""
        try:
            if len(paths) < 2:
                self.on_file_selected(paths[0], encoding, skip_lines)
                return
            self.pending_multi = True
            self.multi_file_paths = list(paths)
            self.multi_file_texts = [_read_text(p, encoding) for p in paths]
            self.multi_file_names = [os.path.basename(p) for p in paths]
            self.encoding = encoding
            self.skip_lines = skip_lines
            self.file_name = f"{self.multi_file_names[0]} 외 {len(paths)-1}개 파일"

            # 첫 파일 기준으로 컬럼 매핑 추정
            result = parse_headers_from_text(self.multi_file_texts[0], skip_lines=skip_lines)
            self.mappingPage.set_file_badge(self.file_name)
            self.mappingPage.set_encoding_skip(encoding, skip_lines)
            self.mappingPage.set_headers_and_mapping(
                result["headers"], result["mapping"], result["capMode"], result.get("rowCount")
            )
            self.mappingPage.set_error("")
            self.btnReset.setVisible(True)
            self.stack.setCurrentWidget(self.mappingPage)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.uploadPage.set_error(f"파일을 처리하지 못했습니다: {e}")

    def on_reparse_requested(self, encoding: str, skip_lines: int):
        try:
            self.encoding = encoding
            self.skip_lines = skip_lines
            if self.pending_multi:
                self.multi_file_texts = [_read_text(p, encoding) for p in self.multi_file_paths]
                result = parse_headers_from_text(self.multi_file_texts[0], skip_lines=skip_lines)
            else:
                self.csv_text = _read_text(self.file_path, encoding)
                result = parse_headers_from_text(self.csv_text, skip_lines=skip_lines)
            # 기존 선택을 유지하되, 더 이상 존재하지 않는 헤더는 재추정값으로 대체
            headers = result["headers"]
            merged = {}
            keys = ["cycle", "stepType", "voltage", "current", "time", "stepNo", "energy", "temp",
                    "chgCap", "dchgCap", "capacity"]
            for k in keys:
                cur = self.mappingPage.mapping.get(k, "")
                merged[k] = cur if cur in headers else result["mapping"].get(k, "")
            self.mappingPage.set_headers_and_mapping(headers, merged, result["capMode"], result.get("rowCount"))
            self.mappingPage.set_error("")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.mappingPage.set_error(f"다시 파싱하지 못했습니다: {e}")

    def on_pretreat_detect_requested(self, cycle_col: str, stepno_col: str):
        try:
            text = self.multi_file_texts[0] if self.pending_multi else self.csv_text
            steps = detect_pretreat_steps(text, self.skip_lines, cycle_col, stepno_col)
            self.mappingPage.set_detected_pretreat(steps)
        except Exception:  # noqa: BLE001
            pass  # 보조 기능이므로 실패해도 조용히 넘어감

    # ------------------------------------------------------------------
    # stage 2 -> 3 (단일 파일) 또는 2 -> 2.5 (다중 파일 -> 병합 확인)
    # ------------------------------------------------------------------
    def on_start_analysis(self, params: dict):
        if self.pending_multi:
            self._prepare_merge_stage(params)
            return
        try:
            self.mappingPage.set_busy(True)
            df = build_df_from_mapping(
                self.csv_text, self.skip_lines, params["mapping"], params["capMode"],
                set(params["excludeSteps"]), set(params["excludeCycles"]), params.get("unitOverrides"),
            )
            df["Source_File"] = self.file_name or "입력 파일"
            self._finalize_analysis(df, params.get("upperVoltage"))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.mappingPage.set_error(f"분석 중 오류가 발생했습니다: {e}")
        finally:
            self.mappingPage.set_busy(False)

    def _prepare_merge_stage(self, params: dict):
        try:
            self.mappingPage.set_busy(True)
            self.multi_mapping_params = params
            dfs = []
            for name, text in zip(self.multi_file_names, self.multi_file_texts):
                df = build_df_from_mapping(
                    text, self.skip_lines, params["mapping"], params["capMode"],
                    set(params["excludeSteps"]), set(params["excludeCycles"]), params.get("unitOverrides"),
                )
                df["Source_File"] = name
                dfs.append(df)

            ranges = [(int(d["Cycle"].min()), int(d["Cycle"].max())) for d in dfs]
            offsets = suggest_cycle_offsets(ranges)
            self._pending_multi_dfs = dfs  # 병합 확인 화면에서 순서/오프셋 적용 후 사용

            file_infos = []
            for i, (name, (mn, mx), off) in enumerate(zip(self.multi_file_names, ranges, offsets)):
                file_infos.append({
                    "original_index": i, "name": name, "row_count": len(dfs[i]),
                    "min_cycle": mn, "max_cycle": mx, "offset": off,
                })
            self.mergePage.set_files(file_infos)
            self.mergePage.set_error("")
            self.stack.setCurrentWidget(self.mergePage)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.mappingPage.set_error(f"파일을 확인하지 못했습니다: {e}")
        finally:
            self.mappingPage.set_busy(False)

    def on_merge_confirmed(self, order: list):
        """order: [{"original_index":int, "offset":int}, ...] 최종 순서대로"""
        try:
            dfs_in_order = []
            for item in order:
                df = self._pending_multi_dfs[item["original_index"]].copy()
                df["Cycle"] = df["Cycle"] + int(item["offset"])
                dfs_in_order.append(df)
            combined = combine_multi_file_dfs(dfs_in_order)
            upper_voltage = (self.multi_mapping_params or {}).get("upperVoltage")
            self._finalize_analysis(combined, upper_voltage)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.mergePage.set_error(f"병합 중 오류가 발생했습니다: {e}")

    def _finalize_analysis(self, df, upper_voltage):
        """단일 파일이든 여러 파일을 병합한 결과든, 표준화된 df가 준비된 이후의 공통 처리."""
        self.df = df
        self.upper_voltage = upper_voltage
        self.summary_df = compute_cycle_summary(df, upper_voltage_v=self.upper_voltage)

        # 여러 파일을 병합한 경우에도 각 요약 행의 원본 파일을 보존해 좌측 파일 필터에 사용한다.
        if "Source_File" in df.columns:
            cycle_sources = (
                df.sort_values("_row").groupby("Cycle")["Source_File"].first().to_dict()
            )
            self.summary_df["Source_File"] = self.summary_df["Cycle"].map(cycle_sources).fillna("입력 파일")
            source_files = list(dict.fromkeys(df["Source_File"].astype(str).tolist()))
        else:
            self.summary_df["Source_File"] = "입력 파일"
            source_files = ["입력 파일"]

        summary_rows = self.summary_df.to_dict(orient="records")
        cycles = sorted(df["Cycle"].unique().tolist())
        v_all = df["Voltage_V"].dropna()
        cap_all = df["Capacity_mAh"].dropna()
        voltage_domain = (float(v_all.min()), float(v_all.max())) if len(v_all) else (0, 5)
        capacity_domain = (0, float(cap_all.max()) * 1.02) if len(cap_all) else (0, 1000)

        self.reportPage.set_analysis(
            summary_rows, cycles[0], cycles[-1], voltage_domain, capacity_domain, source_files=source_files,
        )
        self.stack.setCurrentWidget(self.reportPage)
        self.btnAddToCompare.setVisible(True)
        self.on_cycle_changed(cycles[0])
        self.refresh_peaks_trend()

    # ------------------------------------------------------------------
    # cycle detail
    # ------------------------------------------------------------------
    def on_cycle_changed(self, cycle: int):
        try:
            df = self.df
            chg = df[(df["Cycle"] == cycle) & (df["TypeStd"] == "CHARGE")].sort_values("_row")
            dis = df[(df["Cycle"] == cycle) & (df["TypeStd"] == "DISCHARGE")].sort_values("_row")
            charge_pts = [{"v": float(v), "q": float(q)} for v, q in zip(chg["Voltage_V"], chg["Capacity_mAh"])
                          if not (np.isnan(v) or np.isnan(q))]
            discharge_pts = [{"v": float(v), "q": float(q)} for v, q in zip(dis["Voltage_V"], dis["Capacity_mAh"])
                              if not (np.isnan(v) or np.isnan(q))]

            rc = compute_dqdv(df, cycle, "charge", n_points=500, smooth=True,
                               peak_prominence_pct=self.peak_sensitivity_pct, upper_voltage_v=self.upper_voltage)
            rd = compute_dqdv(df, cycle, "discharge", n_points=500, smooth=True,
                               peak_prominence_pct=self.peak_sensitivity_pct, upper_voltage_v=self.upper_voltage)

            def pack(r):
                if r is None:
                    return None
                return {
                    "voltageGrid": r["voltage_grid"].tolist(),
                    "dqdv": r["dqdv"].tolist(),
                    "capacityGrid": r["capacity_grid"].tolist(),
                    "dvdq": r["dvdq"].tolist(),
                    "peakVoltage": r["peak_voltage"].tolist(),
                    "peakDqdv": r["peak_dqdv"].tolist(),
                    "peakCapacity": r["peak_capacity"].tolist(),
                    "peakDvdq": r["peak_dvdq"].tolist(),
                }

            def pack_rest(r):
                if r is None:
                    return None
                return {
                    "timeS": r["time_s"].tolist(),
                    "timeMin": r["time_min"].tolist(),
                    "voltageV": r["voltage_v"].tolist(),
                    "dvdtMvPerMin": r["dvdt_mV_per_min"].tolist(),
                    "restDurationS": r["rest_duration_s"],
                    "deltaVV": r["delta_v_v"],
                }

            chg_rest = compute_rest_dvdt(df, cycle, after="charge")
            dis_rest = compute_rest_dvdt(df, cycle, after="discharge")

            row = None
            if self.summary_df is not None:
                srow = self.summary_df[self.summary_df["Cycle"] == cycle]
                if len(srow):
                    row = srow.iloc[0].to_dict()

            self.reportPage.set_cycle_detail({
                "cycle": cycle,
                "chargePts": charge_pts,
                "dischargePts": discharge_pts,
                "dqdvCharge": pack(rc),
                "dqdvDischarge": pack(rd),
                "chargeRest": pack_rest(chg_rest),
                "dischargeRest": pack_rest(dis_rest),
                "summaryRow": row,
            })
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()

    def on_peak_sensitivity_changed(self, pct: float):
        self.peak_sensitivity_pct = pct
        if self.df is not None:
            self.on_cycle_changed(self.reportPage.selected_cycle)
            self.refresh_peaks_trend()

    def refresh_peaks_trend(self):
        if self.df is None:
            return
        try:
            cycles = sorted(self.df["Cycle"].unique().tolist())
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            peaks = compute_all_cycle_peaks(
                self.df, cycles, n_points=500, smooth=True,
                peak_prominence_pct=self.peak_sensitivity_pct, upper_voltage_v=self.upper_voltage,
            )
            self.reportPage.set_peaks_trend(peaks)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            QApplication.restoreOverrideCursor()

    def on_multi_dqdv_apply(self, spec_text: str, direction: str):
        if self.df is None:
            return
        try:
            all_cycles = set(self.df["Cycle"].unique().tolist())
            requested = parse_cycle_spec(spec_text)
            cycles = [c for c in requested if c in all_cycles]
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            curves = compute_multi_cycle_dqdv(
                self.df, cycles, direction, n_points=500, smooth=True,
                peak_prominence_pct=self.peak_sensitivity_pct, upper_voltage_v=self.upper_voltage,
            )
            self.reportPage.set_multi_dqdv(curves, direction, spec_text)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._show_error(f"사이클 비교 계산 중 오류가 발생했습니다: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def on_multi_rest_apply(self, spec_text: str, after: str):
        if self.df is None:
            return
        try:
            all_cycles = set(self.df["Cycle"].unique().tolist())
            requested = parse_cycle_spec(spec_text)
            cycles = [c for c in requested if c in all_cycles]
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            curves = compute_multi_cycle_rest_dvdt(self.df, cycles, after=after, n_points=200, smooth=True)
            self.reportPage.set_multi_rest(curves, after, spec_text)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._show_error(f"휴지구간 비교 계산 중 오류가 발생했습니다: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    # ------------------------------------------------------------------
    # exports
    # ------------------------------------------------------------------
    def _save_csv(self, default_name: str, header: list, rows: list):
        path, _ = QFileDialog.getSaveFileName(self, "CSV로 저장", default_name, "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                for row in rows:
                    writer.writerow([_clean(v) for v in row])
        except Exception as e:  # noqa: BLE001
            self._show_error(f"저장에 실패했습니다: {e}")

    def export_vq(self):
        d = self.reportPage.current_detail
        if not d:
            return
        cyc = self.reportPage.selected_cycle
        rows = [[cyc, "charge", p["v"], p["q"]] for p in (d.get("chargePts") or [])]
        rows += [[cyc, "discharge", p["v"], p["q"]] for p in (d.get("dischargePts") or [])]
        self._save_csv(f"cycle{cyc}_VQ.csv", ["cycle", "phase", "voltage", "capacity"], rows)

    def export_dqdv(self):
        d = self.reportPage.current_detail
        if not d:
            return
        cyc = self.reportPage.selected_cycle
        rows = []
        rc = d.get("dqdvCharge")
        rd = d.get("dqdvDischarge")
        if rc:
            rows += [[cyc, "charge", v, dq] for v, dq in zip(rc["voltageGrid"], rc["dqdv"])]
        if rd:
            rows += [[cyc, "discharge", v, dq] for v, dq in zip(rd["voltageGrid"], rd["dqdv"])]
        self._save_csv(f"cycle{cyc}_dQdV.csv", ["cycle", "phase", "voltage", "dqdv"], rows)

    def export_dvdq(self):
        d = self.reportPage.current_detail
        if not d:
            return
        cyc = self.reportPage.selected_cycle
        rows = []
        rc = d.get("dqdvCharge")
        rd = d.get("dqdvDischarge")
        if rc:
            rows += [[cyc, "charge", q, dv] for q, dv in zip(rc["capacityGrid"], rc["dvdq"])]
        if rd:
            rows += [[cyc, "discharge", q, dv] for q, dv in zip(rd["capacityGrid"], rd["dvdq"])]
        self._save_csv(f"cycle{cyc}_dVdQ.csv", ["cycle", "phase", "capacity", "dvdq"], rows)

    def export_rest_dvdt(self):
        d = self.reportPage.current_detail
        if not d:
            return
        cyc = self.reportPage.selected_cycle
        rows = []
        cr = d.get("chargeRest")
        dr = d.get("dischargeRest")
        if cr:
            rows += [[cyc, "after_charge", t, v, dvdt]
                     for t, v, dvdt in zip(cr["timeMin"], cr["voltageV"], cr["dvdtMvPerMin"])]
        if dr:
            rows += [[cyc, "after_discharge", t, v, dvdt]
                     for t, v, dvdt in zip(dr["timeMin"], dr["voltageV"], dr["dvdtMvPerMin"])]
        self._save_csv(f"cycle{cyc}_rest_dVdt.csv",
                        ["cycle", "rest_after", "time_min", "voltage_v", "dvdt_mV_per_min"], rows)

    def export_capacity(self):
        rows_dicts = self.reportPage._filtered()
        cols = ["Cycle", "Charge_Capacity_mAh", "Discharge_Capacity_mAh", "Coulombic_Efficiency_pct"]
        rows = [[r.get(c) for c in cols] for r in rows_dicts]
        self._save_csv("capacity_ce.csv", cols, rows)

    def export_energy(self):
        rows_dicts = self.reportPage._filtered()
        cols = ["Cycle", "Charge_Energy_mWh", "Discharge_Energy_mWh"]
        rows = []
        for r in rows_dicts:
            chg_wh = (r.get("Charge_Energy_mWh") or 0) / 1000.0 if r.get("Charge_Energy_mWh") is not None else None
            dchg_wh = (r.get("Discharge_Energy_mWh") or 0) / 1000.0 if r.get("Discharge_Energy_mWh") is not None else None
            rows.append([r.get("Cycle"), chg_wh, dchg_wh])
        self._save_csv("energy.csv", ["Cycle", "Charge_Energy_Wh", "Discharge_Energy_Wh"], rows)

    def export_temp(self):
        rows_dicts = self.reportPage._filtered()
        cols = ["Cycle", "Avg_Temp_C", "Min_Temp_C", "Max_Temp_C", "Temp_Rise_C"]
        rows = [[r.get(c) for c in cols] for r in rows_dicts]
        self._save_csv("temperature.csv", cols, rows)

    def export_cccv(self):
        rows_dicts = self.reportPage._filtered()
        cols = ["Cycle", "CC_Capacity_mAh", "CV_Capacity_mAh", "CC_Capacity_Ratio_pct",
                "CC_Time_s", "CV_Time_s", "Transition_Voltage_V"]
        rows = [[r.get(c) for c in cols] for r in rows_dicts]
        self._save_csv("cc_cv_split.csv", cols, rows)

    def export_vtrend(self):
        rows_dicts = self.reportPage._filtered()
        cols = ["Cycle", "Charge_Start_Voltage_V", "Discharge_Start_Voltage_V",
                "Charge_Onset_R_mOhm", "Discharge_Onset_R_mOhm"]
        rows = [[r.get(c) for c in cols] for r in rows_dicts]
        self._save_csv("voltage_trend_dcir.csv", cols, rows)

    def export_peaks_trend(self):
        peaks = self.reportPage.last_peaks
        if not peaks:
            return
        cols = ["cycle", "phase", "peak_voltage", "peak_dqdv"]
        rows = [[p.get(c) for c in cols] for p in peaks]
        self._save_csv("dqdv_peaks_trend.csv", cols, rows)

    def export_multi_dqdv(self):
        curves = self.reportPage.multi_curves
        if not curves:
            return
        direction = self.reportPage.multi_direction
        rows = []
        for c in curves:
            for v, dq in zip(c["voltage_grid"], c["dqdv"]):
                rows.append([c["cycle"], direction, v, dq])
        self._save_csv("multi_cycle_dqdv_compare.csv", ["cycle", "phase", "voltage", "dqdv"], rows)

    def export_multi_rest(self):
        curves = self.reportPage.multi_rest_curves
        if not curves:
            return
        after = self.reportPage.multi_rest_after
        rows = []
        for c in curves:
            for t, dvdt in zip(c["time_min"], c["dvdt_mV_per_min"]):
                rows.append([c["cycle"], after, t, dvdt])
        self._save_csv("multi_cycle_rest_dvdt_compare.csv",
                        ["cycle", "rest_after", "time_min", "dvdt_mV_per_min"], rows)

    def export_table(self):
        rows_dicts, cols = self.reportPage.table_export_rows()
        rows = [[r.get(c) for c in cols] for r in rows_dicts]
        self._save_csv("cycle_summary.csv", cols, rows)

    def export_excel(self):
        if self.summary_df is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "엑셀로 저장", "cycle_summary.xlsx", "Excel Files (*.xlsx)")
        if not path:
            return
        try:
            self.summary_df.to_excel(path, index=False)
        except Exception as e:  # noqa: BLE001
            self._show_error(f"저장에 실패했습니다: {e}")

    def export_all(self):
        """전체 사이클의 dQ/dV 원자료 + 피크 요약 + 사이클 요약을 한 Excel 파일(여러 시트)로 내보낸다.
        사이클마다 그래프를 하나씩 다운로드하지 않아도 피크 변화 등을 한 파일에서 비교할 수 있게 한다."""
        if self.df is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "전체 사이클 데이터 일괄 저장", "all_cycles_dqdv_and_peaks.xlsx", "Excel Files (*.xlsx)"
        )
        if not path:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            cycles = sorted(self.df["Cycle"].unique().tolist())

            curve_rows = []
            for cyc in cycles:
                for direction in ("charge", "discharge"):
                    try:
                        r = compute_dqdv(self.df, cyc, direction, n_points=500, smooth=True,
                                          peak_prominence_pct=self.peak_sensitivity_pct,
                                          upper_voltage_v=self.upper_voltage)
                    except Exception:  # noqa: BLE001
                        r = None
                    if r is None:
                        continue
                    for v, dq in zip(r["voltage_grid"], r["dqdv"]):
                        curve_rows.append({"Cycle": cyc, "Phase": direction, "Voltage_V": v, "dQdV_mAh_per_V": dq})

            peaks = compute_all_cycle_peaks(
                self.df, cycles, n_points=500, smooth=True, peak_prominence_pct=self.peak_sensitivity_pct,
                upper_voltage_v=self.upper_voltage,
            )
            peaks_df = pd.DataFrame(peaks).rename(columns={
                "cycle": "Cycle", "phase": "Phase", "peak_voltage": "Peak_Voltage_V", "peak_dqdv": "Peak_dQdV"
            })
            curves_df = pd.DataFrame(curve_rows)

            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                self.summary_df.to_excel(writer, sheet_name="cycle_summary", index=False)
                peaks_df.to_excel(writer, sheet_name="dqdv_peaks", index=False)
                curves_df.to_excel(writer, sheet_name="dqdv_raw_curves", index=False)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._show_error(f"저장에 실패했습니다: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    # ------------------------------------------------------------------
    # 셀 비교
    # ------------------------------------------------------------------
    def on_add_to_compare(self):
        if self.summary_df is None:
            return
        default_name = os.path.splitext(self.file_name)[0] if self.file_name else "Cell"
        name, ok = QInputDialog.getText(self, "셀 비교에 추가", "이 셀의 이름을 입력하세요:", text=default_name)
        if not ok or not name.strip():
            return
        self.comparePage.add_cell(name.strip(), self.summary_df.to_dict(orient="records"))
        QMessageBox.information(self, "추가 완료", f"'{name.strip()}' 셀이 비교 목록에 추가되었습니다.")

    def on_add_cell_requested(self):
        """비교 화면에서 '+ 새 셀 추가'를 눌렀을 때: 현재 작업 중이던 파일/분석 상태만 초기화하고
        (comparePage에 이미 저장된 셀들은 그대로 유지) 업로드 화면으로 이동한다."""
        self.reset_all()

    def on_add_batch_cells(self):
        """'+ 여러 셀 한번에 추가': 파일을 여러 개 골라서, 각 파일마다 컬럼을 자동 인식해
        바로 셀로 추가한다 (매핑 확인 화면을 거치지 않음). 파일 하나 = 셀 하나로 취급하며,
        여러 파일을 이어붙여야 하는 셀은 이 기능으로 처리하지 않는다."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "비교에 추가할 CSV 파일들 선택 (파일당 셀 1개)", "", "CSV Files (*.csv);;All Files (*.*)"
        )
        if not paths:
            return

        encodings = ["EUC-KR / CP949 (PNE 기본값)", "UTF-8"]
        choice, ok = QInputDialog.getItem(self, "인코딩 선택", "선택한 모든 파일에 적용할 인코딩:",
                                           encodings, 0, False)
        if not ok:
            return
        encoding = "euc-kr" if choice.startswith("EUC-KR") else "utf-8"

        added, failed = [], []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for path in paths:
                name = os.path.splitext(os.path.basename(path))[0]
                try:
                    text = _read_text(path, encoding)
                    result = parse_headers_from_text(text, skip_lines=0)
                    mapping, cap_mode = result["mapping"], result["capMode"]
                    exclude_steps = set()
                    if mapping.get("cycle") and mapping.get("stepNo"):
                        try:
                            exclude_steps = set(detect_pretreat_steps(text, 0, mapping["cycle"], mapping["stepNo"]))
                        except Exception:  # noqa: BLE001
                            exclude_steps = set()
                    df = build_df_from_mapping(text, 0, mapping, cap_mode, exclude_steps, set())
                    summary_df = compute_cycle_summary(df)
                    self.comparePage.add_cell(name, summary_df.to_dict(orient="records"))
                    added.append(name)
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    failed.append(f"{os.path.basename(path)} ({e})")
        finally:
            QApplication.restoreOverrideCursor()

        msg = f"{len(added)}개 셀이 추가되었습니다."
        if failed:
            msg += f"\n\n실패한 파일 {len(failed)}개:\n" + "\n".join(failed)
            msg += "\n\n실패한 파일은 '+ 새 셀 추가'로 컬럼 매핑을 직접 확인하며 추가해보세요."
        QMessageBox.information(self, "일괄 추가 결과", msg)
        if added:
            self.show_compare_page()

    def show_compare_page(self):
        self.stack.setCurrentWidget(self.comparePage)
        self.btnReset.setVisible(False)
        self.btnAddToCompare.setVisible(False)

    def update_compare_button(self):
        n = len(self.comparePage.cells)
        self.btnShowCompare.setText(f"셀 비교 보기 ({n})")

    def export_compare_chart(self, field: str, default_name: str):
        cols, rows = self.comparePage.chart_export_rows(field)
        self._save_csv(default_name, cols, rows)

    def export_compare_table(self):
        cols, rows = self.comparePage.table_export_rows()
        self._save_csv("cell_comparison_summary.csv", cols, rows)

    # ------------------------------------------------------------------
    # DCR 분석
    # ------------------------------------------------------------------
    def reset_dcr(self):
        self.dcr_csv_text = ""
        self.dcr_file_path = ""
        self.dcr_file_name = ""
        self.dcr_files = []
        self.dcr_df = None
        self.dcr_detection = None
        self.dcr_results = None
        self.dcr_mapping_params = None
        self.dcr_review_params = None
        self.dcrUploadPage.set_error("")
        self.btnDcrReset.setVisible(False)
        self.dcrStack.setCurrentWidget(self.dcrUploadPage)

    def on_dcr_file_selected(self, path: str, encoding: str, skip_lines: int):
        try:
            self.dcr_file_path = path
            self.dcr_file_name = os.path.basename(path)
            self.dcr_encoding = encoding
            self.dcr_skip_lines = skip_lines
            self.dcr_csv_text = _read_text(path, encoding)
            self.dcr_files = [{"path": path, "name": self.dcr_file_name, "text": self.dcr_csv_text}]

            result = parse_headers_from_text(self.dcr_csv_text, skip_lines=skip_lines)
            self.dcrMappingPage.set_file_badge(self.dcr_file_name)
            self.dcrMappingPage.set_encoding_skip(encoding, skip_lines)
            self.dcrMappingPage.set_headers_and_mapping(
                result["headers"], result["mapping"], result["capMode"], result.get("rowCount")
            )
            self.dcrMappingPage.set_error("")
            self.btnDcrReset.setVisible(True)
            self.dcrStack.setCurrentWidget(self.dcrMappingPage)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.dcrUploadPage.set_error(f"파일을 처리하지 못했습니다: {e}")

    def on_dcr_multi_files_selected(self, paths: list, encoding: str, skip_lines: int):
        try:
            if len(paths) < 2:
                self.on_dcr_file_selected(paths[0], encoding, skip_lines)
                return
            names = [os.path.basename(path) for path in paths]
            # 같은 이름이 여러 폴더에서 올라온 경우에도 좌측 필터가 구분되도록 번호를 붙인다.
            seen = {}
            unique_names = []
            for name in names:
                seen[name] = seen.get(name, 0) + 1
                unique_names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
            self.dcr_files = [
                {"path": path, "name": name, "text": _read_text(path, encoding)}
                for path, name in zip(paths, unique_names)
            ]
            self.dcr_file_path = paths[0]
            self.dcr_file_name = f"{unique_names[0]} 외 {len(paths) - 1}개 파일"
            self.dcr_encoding = encoding
            self.dcr_skip_lines = skip_lines
            self.dcr_csv_text = self.dcr_files[0]["text"]

            result = parse_headers_from_text(self.dcr_csv_text, skip_lines=skip_lines)
            self.dcrMappingPage.set_file_badge(self.dcr_file_name)
            self.dcrMappingPage.set_encoding_skip(encoding, skip_lines)
            self.dcrMappingPage.set_headers_and_mapping(
                result["headers"], result["mapping"], result["capMode"], result.get("rowCount")
            )
            self.dcrMappingPage.set_error("")
            self.btnDcrReset.setVisible(True)
            self.dcrStack.setCurrentWidget(self.dcrMappingPage)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.dcrUploadPage.set_error(f"파일을 처리하지 못했습니다: {e}")

    def on_dcr_reparse_requested(self, encoding: str, skip_lines: int):
        try:
            self.dcr_encoding = encoding
            self.dcr_skip_lines = skip_lines
            if self.dcr_files:
                for source in self.dcr_files:
                    source["text"] = _read_text(source["path"], encoding)
                self.dcr_csv_text = self.dcr_files[0]["text"]
            else:
                self.dcr_csv_text = _read_text(self.dcr_file_path, encoding)
            result = parse_headers_from_text(self.dcr_csv_text, skip_lines=skip_lines)
            headers = result["headers"]
            merged = {}
            keys = ["cycle", "stepType", "voltage", "current", "time", "stepNo", "energy", "temp",
                    "chgCap", "dchgCap", "capacity"]
            for k in keys:
                cur = self.dcrMappingPage.mapping.get(k, "")
                merged[k] = cur if cur in headers else result["mapping"].get(k, "")
            self.dcrMappingPage.set_headers_and_mapping(headers, merged, result["capMode"], result.get("rowCount"))
            self.dcrMappingPage.set_error("")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.dcrMappingPage.set_error(f"다시 파싱하지 못했습니다: {e}")

    def on_dcr_pretreat_detect_requested(self, cycle_col: str, stepno_col: str):
        try:
            steps = detect_pretreat_steps(self.dcr_csv_text, self.dcr_skip_lines, cycle_col, stepno_col)
            self.dcrMappingPage.set_detected_pretreat(steps)
        except Exception:  # noqa: BLE001
            pass

    def on_dcr_mapping_confirmed(self, params: dict):
        try:
            self.dcrMappingPage.set_busy(True)
            source_dfs = []
            sources = self.dcr_files or [{"name": self.dcr_file_name or "입력 파일", "text": self.dcr_csv_text}]
            for source in sources:
                df = build_df_from_mapping(
                    source["text"], self.dcr_skip_lines, params["mapping"], params["capMode"],
                    set(params["excludeSteps"]), set(params["excludeCycles"]), params.get("unitOverrides"),
                )
                df["Source_File"] = source["name"]
                source_dfs.append(df)
            df = pd.concat(source_dfs, ignore_index=True).reset_index(drop=True)
            df["_row"] = df.index
            # 계산 단위는 이미 각 파일마다 내부 표준으로 통일되어 있으므로, 표시용 속성만 대표값을 보존한다.
            df.attrs["current_source_unit"] = source_dfs[0].attrs.get("current_source_unit")
            df.attrs["capacity_source_unit"] = source_dfs[0].attrs.get("capacity_source_unit")
            self.dcr_df = df
            self.dcr_mapping_params = dict(params)
            detection = detect_dcr_blocks(
                df, pulse_max_duration_s=90.0, return_capacity_tolerance_pct=10.0
            )
            self.dcr_detection = detection
            self.dcrReviewPage.set_detection(detection)
            self.dcrReviewPage.set_error("")
            self.dcrStack.setCurrentWidget(self.dcrReviewPage)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.dcrMappingPage.set_error(f"분석 중 오류가 발생했습니다: {e}")
        finally:
            self.dcrMappingPage.set_busy(False)

    def on_dcr_redetect(
        self, pulse_max_duration_s: float, return_capacity_tolerance_pct: float, soc_setting_steps_text: str,
    ):
        if self.dcr_df is None:
            return
        try:
            detection = detect_dcr_blocks(
                self.dcr_df, pulse_max_duration_s=pulse_max_duration_s,
                return_capacity_tolerance_pct=return_capacity_tolerance_pct,
                soc_setting_step_nos=_parse_step_no_spec(soc_setting_steps_text),
            )
            self.dcr_detection = detection
            self.dcrReviewPage.set_detection(detection)
            self.dcrReviewPage.set_error("")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.dcrReviewPage.set_error(f"재탐지 중 오류가 발생했습니다: {e}")

    def on_dcr_review_confirmed(self, params: dict):
        if self.dcr_df is None or self.dcr_detection is None:
            return
        try:
            time_points = params["time_points"]
            result = compute_dcr_table(
                self.dcr_df, self.dcr_detection, time_points,
                excluded_pulse_segs=params.get("excluded_segs") or set(),
                soc_ocv_points=params.get("soc_ocv_points") or [],
                manual_block_by_seg=params.get("manual_block_by_seg") or {},
                manual_soc_by_seg=params.get("manual_soc_by_seg") or {},
            )
            self.dcr_results = result
            self.dcr_time_points = time_points
            self.dcr_review_params = dict(params)
            if not result["table"]:
                self.dcrReviewPage.set_error("계산된 펄스가 없습니다. 제외 목록이나 탐지 설정을 확인해주세요.")
                return
            self.dcrReportPage.set_results(result["table"], time_points)
            self.dcrStack.setCurrentWidget(self.dcrReportPage)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.dcrReviewPage.set_error(f"DCR 계산 중 오류가 발생했습니다: {e}")

    def export_dcr_table(self):
        if not self.dcr_results:
            return
        cols, rows = self.dcrReportPage.table_export_rows()
        self._save_csv("dcr_summary.csv", cols, rows)

    def export_dcr_chart(self):
        if not self.dcr_results:
            return
        cols, rows = self.dcrReportPage.chart_export_rows()
        self._save_csv("dcr_chart_data.csv", cols, rows)

    def _dcr_raw_curve_rows(self):
        """현재 선택한 파일의 펄스 시간시계열 Raw Data를 CSV/Excel 공용 형태로 만든다."""
        selected_sources = self.dcrReportPage.selected_source_files()
        rows = []
        for curve in self.dcr_results.get("raw_curves", []):
            if curve.get("source_file", "입력 파일") not in selected_sources:
                continue
            for t, v, i in zip(curve["time_s"], curve["voltage_v"], curve["current_series_mA"]):
                rows.append({
                    "Source_File": curve.get("source_file", "입력 파일"),
                    "Block": curve["block"], "Direction": curve["direction"],
                    "Pulse_Index": curve["pulse_index"], "Current_mA_set": curve["current_mA"],
                    "Time_s": t, "Voltage_V": v, "Current_mA": i,
                })
        return rows

    def export_dcr_summary(self, time_point_s: float):
        if not self.dcr_results:
            return
        cols, rows = self.dcrReportPage.summary_export_rows(time_point_s)
        self._save_csv(f"dcr_summary_{time_point_s:g}s.csv", cols, rows)

    def on_dcr_result_add_files(self, paths: list):
        """결과 화면에서 같은 매핑·설정으로 추가 DCR 파일을 즉시 분석해 현재 표에 합친다."""
        if not self.dcr_results or not self.dcr_mapping_params or not self.dcr_time_points:
            self._show_error("추가 분석에 필요한 기존 DCR 설정이 없습니다. 새 파일 업로드부터 다시 진행해주세요.")
            return

        existing_names = {source["name"] for source in self.dcr_files}
        new_sources = []
        for path in paths:
            base = os.path.basename(path)
            name, suffix = base, 2
            while name in existing_names:
                name = f"{base} ({suffix})"
                suffix += 1
            existing_names.add(name)
            new_sources.append({"path": path, "name": name})

        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            mapping = self.dcr_mapping_params
            tolerance = self.dcr_detection.get("return_capacity_tolerance_pct", 10.0) if self.dcr_detection else 10.0
            setting_steps = set(self.dcr_detection.get("soc_setting_step_nos", [])) if self.dcr_detection else set()
            review = self.dcr_review_params or {}
            added_tables, added_curves, added_dfs, failed = [], [], [], []
            for source in new_sources:
                try:
                    text = _read_text(source["path"], self.dcr_encoding)
                    df = build_df_from_mapping(
                        text, self.dcr_skip_lines, mapping["mapping"], mapping["capMode"],
                        set(mapping["excludeSteps"]), set(mapping["excludeCycles"]), mapping.get("unitOverrides"),
                    )
                    df["Source_File"] = source["name"]
                    detection = detect_dcr_blocks(
                        df, pulse_max_duration_s=90.0,
                        return_capacity_tolerance_pct=tolerance,
                        soc_setting_step_nos=setting_steps,
                    )
                    result = compute_dcr_table(
                        df, detection, self.dcr_time_points,
                        soc_ocv_points=review.get("soc_ocv_points") or [],
                    )
                    if not result["table"]:
                        raise ValueError("유효한 DCR 펄스를 찾지 못했습니다.")
                    source["text"] = text
                    added_tables.extend(result["table"])
                    added_curves.extend(result["raw_curves"])
                    added_dfs.append(df)
                    self.dcr_files.append(source)
                except Exception as exc:  # noqa: BLE001
                    failed.append(f"{source['name']}: {exc}")

            if added_tables:
                self.dcr_results["table"].extend(added_tables)
                self.dcr_results["raw_curves"].extend(added_curves)
                self.dcr_df = pd.concat([self.dcr_df, *added_dfs], ignore_index=True).reset_index(drop=True)
                self.dcr_df["_row"] = self.dcr_df.index
                self.dcrReportPage.set_results(self.dcr_results["table"], self.dcr_time_points)
            message = f"{len(added_dfs)}개 파일을 현재 결과에 추가했습니다." if added_dfs else "추가된 파일이 없습니다."
            if failed:
                message += "\n\n분석하지 못한 파일:\n" + "\n".join(failed)
            QMessageBox.information(self, "DCR 파일 추가 분석", message)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._show_error(f"파일 추가 분석에 실패했습니다: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def export_dcr_all(self):
        """요약 테이블 + 모든 펄스의 raw V-t, I-t 곡선을 Excel 한 파일(여러 시트)로 내보낸다."""
        if not self.dcr_results:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "DCR 전체 데이터 저장", "dcr_full_results.xlsx", "Excel Files (*.xlsx)"
        )
        if not path:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            raw_cols, raw_rows = self.dcrReportPage.table_export_rows()
            raw_results_df = pd.DataFrame(raw_rows, columns=raw_cols)

            curves_df = pd.DataFrame(self._dcr_raw_curve_rows())

            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                for tp in self.dcr_time_points:
                    summary_cols, summary_rows = self.dcrReportPage.summary_export_rows(tp)
                    pd.DataFrame(summary_rows, columns=summary_cols).to_excel(
                        writer, sheet_name=f"DCIR_{tp:g}s"[:31], index=False
                    )
                raw_results_df.to_excel(writer, sheet_name="dcr_raw_results", index=False)
                curves_df.to_excel(writer, sheet_name="pulse_raw_curves", index=False)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._show_error(f"저장에 실패했습니다: {e}")
        finally:
            QApplication.restoreOverrideCursor()


def main():
    app = QApplication(sys.argv)
    theme.load_fonts()
    app.setFont(theme.base_font(11))
    app.setStyleSheet(theme.STYLESHEET)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
