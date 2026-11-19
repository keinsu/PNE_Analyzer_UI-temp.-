# -*- coding: utf-8 -*-
"""
upload_page.py
1단계: CSV 파일 업로드 (드래그앤드롭 + 클릭 업로드), 인코딩/무시 행 수 지정.
단일 파일 업로드와, 하나의 셀을 여러 파일로 나눠 기록한 경우를 위한 다중 파일
업로드(이어붙이기용)를 모두 지원한다.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QComboBox, QSpinBox,
    QFileDialog, QPushButton
)
from PySide6.QtCore import Qt, Signal
from . import theme


class DropZone(QFrame):
    filesDropped = Signal(list)
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(220)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("CSV 파일을 드래그하거나 클릭해서 업로드")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {theme.TEAL}; font-family: '{theme.FONT_DISPLAY}'; font-size: 17px; font-weight: 600;")
        self.sub = QLabel("PNE 사이클러 raw data (.csv) 지원 · 컬럼은 업로드 후 자동으로 인식됩니다")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub.setStyleSheet(f"color: {theme.SUBTLE}; font-family: '{theme.FONT_MONO}'; font-size: 11.5px;")
        self.sub2 = QLabel("여러 파일을 한 번에 드래그하면 '하나의 셀을 이어붙이기'로 처리됩니다 (중단 후 재시작 등)")
        self.sub2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub2.setStyleSheet(f"color: {theme.SUBTLE}; font-family: '{theme.FONT_MONO}'; font-size: 10.5px;")
        layout.addWidget(title)
        layout.addSpacing(6)
        layout.addWidget(self.sub)
        layout.addWidget(self.sub2)

    def set_dcr_mode(self):
        self.sub.setText("[저장된 모든 Data] DCR Raw Data (.csv) 지원 · 컬럼은 업로드 후 자동으로 인식됩니다")
        self.sub2.setText("여러 파일을 한 번에 드래그하면 파일별 DCR 결과를 하나의 정리표에 병합합니다")

    def mousePressEvent(self, event):
        self.clicked.emit()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self.setProperty("dragActive", "true")
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.setProperty("dragActive", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event):
        self.setProperty("dragActive", "false")
        self.style().unpolish(self)
        self.style().polish(self)
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
        if paths:
            self.filesDropped.emit(paths)


class UploadPage(QWidget):
    """
    fileSelected(path, encoding, skip_lines): 단일 파일 업로드
    multiFilesSelected(paths, encoding, skip_lines): 여러 파일 이어붙이기 업로드
    """
    fileSelected = Signal(str, str, int)
    multiFilesSelected = Signal(list, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(28, 28, 28, 28)

        self.dropZone = DropZone()
        self.dropZone.filesDropped.connect(self._on_files_dropped)
        self.dropZone.clicked.connect(self._browse)
        panel_layout.addWidget(self.dropZone)

        multi_row = QHBoxLayout()
        self.multiHint = QLabel("하나의 셀인데 파일이 여러 개로 나뉘어 있나요? (중단 후 재시작 등)")
        self.multiHint.setStyleSheet(f"color: {theme.SUBTLE}; font-family: '{theme.FONT_MONO}'; font-size: 11.5px;")
        self.btnMultiUpload = QPushButton("여러 파일 이어붙이기 업로드")
        self.btnMultiUpload.clicked.connect(self._browse_multi)
        multi_row.addWidget(self.multiHint)
        multi_row.addStretch(1)
        multi_row.addWidget(self.btnMultiUpload)
        panel_layout.addSpacing(12)
        panel_layout.addLayout(multi_row)

        opts = QHBoxLayout()
        opts.setSpacing(10)
        lbl_enc = QLabel("인코딩")
        lbl_enc.setStyleSheet(f"color: {theme.SUBTLE}; font-family: '{theme.FONT_MONO}'; font-size: 12px;")
        self.encodingCombo = QComboBox()
        self.encodingCombo.addItem("EUC-KR / CP949 (PNE 기본값)", "euc-kr")
        self.encodingCombo.addItem("UTF-8", "utf-8")

        lbl_skip = QLabel("상단 무시 행 수")
        lbl_skip.setStyleSheet(f"color: {theme.SUBTLE}; font-family: '{theme.FONT_MONO}'; font-size: 12px;")
        self.skipSpin = QSpinBox()
        self.skipSpin.setRange(0, 100)

        opts.addWidget(lbl_enc)
        opts.addWidget(self.encodingCombo)
        opts.addSpacing(16)
        opts.addWidget(lbl_skip)
        opts.addWidget(self.skipSpin)
        opts.addStretch(1)
        panel_layout.addSpacing(18)
        panel_layout.addLayout(opts)

        self.errorLabel = QLabel("")
        self.errorLabel.setProperty("role", "error")
        self.errorLabel.setWordWrap(True)
        panel_layout.addWidget(self.errorLabel)

        outer.addWidget(panel)
        outer.addStretch(1)

    def set_dcr_mode(self):
        """공용 업로드 화면을 DCR의 다중 파일 병합 안내로 전환한다."""
        self.dropZone.set_dcr_mode()
        self.multiHint.setText("서로 다른 전압 조건의 DCR 파일을 함께 분석할 수 있습니다.")
        self.btnMultiUpload.setText("여러 DCR 파일 업로드")

    def set_error(self, msg: str):
        self.errorLabel.setText(msg or "")

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "CSV 파일 선택", "", "CSV Files (*.csv);;All Files (*.*)")
        if path:
            self._on_file_path(path)

    def _browse_multi(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "이어붙일 CSV 파일들 선택 (2개 이상)", "",
                                                 "CSV Files (*.csv);;All Files (*.*)")
        if paths:
            self._on_files_dropped(paths)

    def _on_file_path(self, path: str):
        enc = self.encodingCombo.currentData()
        skip = self.skipSpin.value()
        self.fileSelected.emit(path, enc, skip)

    def _on_files_dropped(self, paths: list):
        enc = self.encodingCombo.currentData()
        skip = self.skipSpin.value()
        if len(paths) == 1:
            self.fileSelected.emit(paths[0], enc, skip)
        else:
            self.multiFilesSelected.emit(paths, enc, skip)
