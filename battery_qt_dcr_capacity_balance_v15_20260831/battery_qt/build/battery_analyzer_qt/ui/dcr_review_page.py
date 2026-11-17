# -*- coding: utf-8 -*-
"""
dcr_review_page.py
DCR 분석 2.5단계: 자동으로 탐지된 SOC 블록/펄스를 사람이 확인하고, 필요하면 설정을
조정해 다시 탐지하거나 잘못 잡힌 펄스를 제외한 뒤 분석을 시작하는 화면.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton, QLineEdit,
    QDoubleSpinBox, QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt, Signal
from . import theme
from .widgets import section_title


class DCRReviewPage(QWidget):
    redetectRequested = Signal(float, float, str)     # pulse_max_duration_s, return_capacity_tolerance_pct, setting StepNo text
    startAnalysisRequested = Signal(dict)              # {"time_points": [...], "manual_block_by_seg": {...}, "manual_soc_by_seg": {...}}
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pulse_rows = []  # 현재 표시 중인 펄스 행 정보(dict) 목록 (테이블과 순서 대응)

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        panel = QFrame()
        panel.setObjectName("panel")
        self.layout_ = QVBoxLayout(panel)
        self.layout_.setContentsMargins(24, 24, 24, 24)
        self.layout_.setSpacing(14)
        scroll.setWidget(panel)

        self.layout_.addWidget(section_title("DCR 펄스/SOC 블록 확인"))

        desc = QLabel(
            "휴지 → 짧은 측정 펄스 → 반대 방향 SOC return 패턴을 감지합니다. 펄스 용량과 return"
            " 용량의 차이는 검토 표시로 남기되, 구조가 맞는 펄스는 누락하지 않습니다. 중간에 별도"
            " 활성 Step이 있으면 다음 SOC 블록으로 묶습니다. 아래 표의 SOC 블록·SOC%는 직접 수정할 수 있습니다."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{theme.SUBTLE}; font-family:'{theme.FONT_MONO}'; font-size:11px;")
        self.layout_.addWidget(desc)

        # ---- 탐지 설정 ----
        settings_frame = QFrame()
        settings_frame.setObjectName("hairline")
        sl = QVBoxLayout(settings_frame)
        sl.addWidget(QLabel("탐지 설정"))

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("펄스 최대 지속시간(초)"))
        self.pulseMaxDurationSpin = QDoubleSpinBox()
        self.pulseMaxDurationSpin.setRange(1, 3600)
        self.pulseMaxDurationSpin.setValue(90)
        self.pulseMaxDurationSpin.setDecimals(0)
        row1.addWidget(self.pulseMaxDurationSpin)
        row1.addSpacing(20)
        row1.addWidget(QLabel("SOC return 용량 검토 기준(%)"))
        self.cutoffTolSpin = QDoubleSpinBox()
        self.cutoffTolSpin.setRange(0.0, 100.0)
        self.cutoffTolSpin.setSingleStep(1.0)
        self.cutoffTolSpin.setDecimals(1)
        self.cutoffTolSpin.setValue(10.0)
        row1.addWidget(self.cutoffTolSpin)
        row1.addSpacing(20)
        row1.addWidget(QLabel("SOC setting StepNo"))
        self.socSettingStepsInp = QLineEdit()
        self.socSettingStepsInp.setPlaceholderText("예: 2, 10, 20-22")
        self.socSettingStepsInp.setFixedWidth(190)
        row1.addWidget(self.socSettingStepsInp)
        btnRedetect = QPushButton("다시 탐지")
        btnRedetect.clicked.connect(
            lambda: self.redetectRequested.emit(
                self.pulseMaxDurationSpin.value(), self.cutoffTolSpin.value(), self.socSettingStepsInp.text(),
            )
        )
        row1.addWidget(btnRedetect)
        row1.addStretch(1)
        sl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("SOC-OCV 매칭(선택, SOC%:전압V)"))
        self.socOcvInp = QLineEdit()
        self.socOcvInp.setPlaceholderText("예: 100:4.20, 50:3.75, 0:3.00")
        self.socOcvInp.setFixedWidth(280)
        row2.addWidget(self.socOcvInp)
        row2.addSpacing(20)
        row2.addWidget(QLabel("확인할 시간 시점(초, 콤마로 구분)"))
        self.timePointsInp = QLineEdit("1,10,30")
        self.timePointsInp.setFixedWidth(160)
        row2.addWidget(self.timePointsInp)
        row2.addStretch(1)
        sl.addLayout(row2)
        self.layout_.addWidget(settings_frame)

        self.summaryLabel = QLabel("")
        self.summaryLabel.setStyleSheet(f"color:{theme.TEAL}; font-family:'{theme.FONT_MONO}'; font-size:12px;")
        self.layout_.addWidget(self.summaryLabel)

        # ---- 펄스 목록 테이블 ----
        self.table = QTableWidget()
        self.table.setMinimumHeight(360)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.layout_.addWidget(self.table)

        btn_row = QHBoxLayout()
        btnAllOn = QPushButton("전체 포함")
        btnAllOn.clicked.connect(lambda: self._set_all_checked(True))
        btnAllOff = QPushButton("전체 제외")
        btnAllOff.clicked.connect(lambda: self._set_all_checked(False))
        btn_row.addWidget(btnAllOn)
        btn_row.addWidget(btnAllOff)
        btn_row.addStretch(1)
        self.layout_.addLayout(btn_row)

        self.errorLabel = QLabel("")
        self.errorLabel.setProperty("role", "error")
        self.errorLabel.setWordWrap(True)
        self.layout_.addWidget(self.errorLabel)

        final_row = QHBoxLayout()
        btnCancel = QPushButton("취소하고 새로 업로드")
        btnCancel.clicked.connect(self.cancelRequested.emit)
        self.btnStart = QPushButton("이 설정으로 DCR 분석 시작 →")
        self.btnStart.setProperty("variant", "primary")
        self.btnStart.clicked.connect(self._emit_start)
        final_row.addWidget(btnCancel)
        final_row.addStretch(1)
        final_row.addWidget(self.btnStart)
        self.layout_.addLayout(final_row)
        self.layout_.addStretch(1)

    # ------------------------------------------------------------------
    def set_detection(self, detection: dict):
        """detection: backend.dcr_analysis.detect_dcr_blocks()의 반환값"""
        segments = detection["segments"]
        blocks = detection["blocks"]
        n_pulses = sum(len(g["pulses"]) for b in blocks for g in b["pulse_groups"])
        tolerance = detection.get("return_capacity_tolerance_pct", 10.0)
        settings = detection.get("soc_setting_step_nos", [])
        if settings:
            self.socSettingStepsInp.setText(", ".join(settings))
        unbalanced = sum(
            1 for b in blocks for g in b["pulse_groups"] for pulse in g["pulses"]
            if pulse.get("return_capacity_balanced") is False
        )
        self.summaryLabel.setText(
            f"감지된 SOC 블록: {len(blocks)}개 · 펄스: {n_pulses}개 · 용량 검토 필요: {unbalanced}개 "
            f"(검토 기준 {tolerance:.1f}% · setting 제외: {', '.join(settings) if settings else '없음'})"
        )

        self.pulse_rows = []
        for block in blocks:
            for group in block["pulse_groups"]:
                for pi, pulse_ref in enumerate(group["pulses"]):
                    seg = segments[pulse_ref["seg"]]
                    rest_seg = segments[pulse_ref["rest_seg"]] if pulse_ref.get("rest_seg") is not None else None
                    self.pulse_rows.append(dict(
                        block=block["block_index"] + 1,
                        source_file=seg.get("source_file") or "입력 파일",
                        direction="방전" if group["direction"] == "DISCHARGE" else "충전",
                        pulse_index=pi + 1,
                        seg_index=seg["index"],
                        start_row=seg["start_row"],
                        duration_s=seg["duration_s"],
                        mean_current_mA=seg["mean_current_mA"],
                        delta_cap_mAh=pulse_ref.get("pulse_capacity_mAh"),
                        return_capacity_mAh=pulse_ref.get("return_capacity_mAh"),
                        return_capacity_error_mAh=pulse_ref.get("return_capacity_error_mAh"),
                        return_capacity_balanced=pulse_ref.get("return_capacity_balanced"),
                        reference_voltage=block["reference_voltage"],
                        v0=rest_seg["end_voltage"] if rest_seg is not None else None,
                    ))

        self._render_table()
        self.errorLabel.setText("")

    def set_error(self, msg: str):
        self.errorLabel.setText(msg or "")

    def _render_table(self):
        cols = ["포함", "파일", "SOC블록*", "SOC(%)*", "방향", "펄스#", "시작전압(V0)", "지속시간(s)", "전류(mA)",
                "펄스용량(mAh)", "return용량(mAh)", "return오차(mAh)", "용량검증", "기준전압(V)"]
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(len(self.pulse_rows))
        for i, r in enumerate(self.pulse_rows):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(i, 0, chk_item)

            values = [
                r["source_file"], str(r["block"]), "", r["direction"], str(r["pulse_index"]),
                f"{r['v0']:.4f}" if r["v0"] is not None else "-",
                f"{r['duration_s']:.1f}",
                f"{r['mean_current_mA']:.1f}" if r["mean_current_mA"] is not None else "-",
                f"{r['delta_cap_mAh']:.2f}" if r["delta_cap_mAh"] is not None else "-",
                f"{r.get('return_capacity_mAh', float('nan')):.2f}" if r.get("return_capacity_mAh") is not None else "-",
                f"{r.get('return_capacity_error_mAh', float('nan')):.2f}" if r.get("return_capacity_error_mAh") is not None else "-",
                "균형" if r.get("return_capacity_balanced") is True else (
                    "검토" if r.get("return_capacity_balanced") is False else "확인 불가"
                ),
                f"{r['reference_voltage']:.4f}" if r["reference_voltage"] is not None else "-",
            ]
            for j, v in enumerate(values):
                item = QTableWidgetItem(v)
                if j in (1, 2):
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, j + 1, item)

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item:
                item.setCheckState(state)

    def _parse_time_points(self) -> list:
        out = []
        for part in self.timePointsInp.text().split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(float(part))
            except ValueError:
                continue
        return sorted(set(out)) if out else [1.0, 10.0, 30.0]

    def _parse_soc_ocv_points(self) -> list:
        """`SOC%:전압V` 형식의 사용 입력을 [(soc_pct, voltage_v), ...]로 변환."""
        points = []
        for part in self.socOcvInp.text().split(","):
            if ":" not in part:
                continue
            soc_text, voltage_text = part.split(":", 1)
            try:
                points.append((float(soc_text.strip()), float(voltage_text.strip())))
            except ValueError:
                continue
        return points

    def _emit_start(self):
        excluded = set()
        manual_blocks = {}
        manual_socs = {}
        for i, r in enumerate(self.pulse_rows):
            item = self.table.item(i, 0)
            if item and item.checkState() != Qt.CheckState.Checked:
                excluded.add(r["seg_index"])
                continue
            try:
                manual_blocks[r["seg_index"]] = int(self.table.item(i, 2).text().strip())
            except (AttributeError, ValueError):
                pass
            try:
                value = self.table.item(i, 3).text().strip()
                if value:
                    manual_socs[r["seg_index"]] = float(value)
            except (AttributeError, ValueError):
                pass

        self.startAnalysisRequested.emit({
            "time_points": self._parse_time_points(),
            "soc_ocv_points": self._parse_soc_ocv_points(),
            "excluded_segs": excluded,
            "manual_block_by_seg": manual_blocks,
            "manual_soc_by_seg": manual_socs,
            "pulse_max_duration_s": self.pulseMaxDurationSpin.value(),
            "return_capacity_tolerance_pct": self.cutoffTolSpin.value(),
        })
