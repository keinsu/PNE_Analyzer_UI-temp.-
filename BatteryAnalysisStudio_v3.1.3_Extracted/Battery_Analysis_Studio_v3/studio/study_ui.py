"""Trial library and explicit channel-to-cell import workflow."""
from pathlib import Path
import os
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from .ingestion import load_inputs
from .studies import Study, _now


def _button(layout, name, action):
    button = QPushButton(name)
    button.clicked.connect(action)
    layout.addWidget(button)
    return button


class StudyWorkspace:
    def __init__(self, host, root=None):
        self.host = host
        self.root = Path(root) if root is not None else (
            Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "BatteryAnalysisStudio" / "Studies")
        self.study = None

    def _available(self):
        if self.host._threads:
            self.host.show_error("현재 작업을 마친 후 시험 목록을 변경하세요.")
            return False
        return True

    def _library(self):
        result = []
        for path in self.root.glob("*/study.json"):
            try:
                result.append(Study(path.parent))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return sorted(result, key=lambda s: s.data["modified"], reverse=True)

    def choose(self):
        if not self._available():
            return
        dialog = QDialog(self.host)
        dialog.setWindowTitle("저장된 시험")
        dialog.resize(560, 400)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        layout.addWidget(listing)
        for study in self._library():
            row = QListWidgetItem(f"{study.data['name']}   ·   셀 {len(study.data['cells'])}개")
            row.setData(Qt.ItemDataRole.UserRole, str(study.directory))
            row.setToolTip(study.data["modified"])
            listing.addItem(row)
        if listing.count():
            listing.setCurrentRow(0)
        else:
            layout.addWidget(QLabel("새 시험을 만들어 데이터를 누적하세요."))
        bar = QHBoxLayout()
        layout.addLayout(bar)

        def open_selected():
            item = listing.currentItem()
            if item:
                self.open(Study(item.data(Qt.ItemDataRole.UserRole)))
                dialog.accept()

        def create():
            name, ok = QInputDialog.getText(dialog, "새 시험", "시험 이름")
            if not ok:
                return
            try:
                if any(s.data["name"].casefold() == name.strip().casefold() for s in self._library()):
                    raise ValueError("같은 이름의 시험이 이미 있습니다.")
                self.open(Study.create(self.root, name))
                dialog.accept()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(dialog, "시험 생성", str(exc))

        _button(bar, "새 시험", create)
        _button(bar, "시험 열기", open_selected).setEnabled(bool(listing.count()))
        _button(bar, "닫기", dialog.close)
        listing.itemDoubleClicked.connect(lambda _: open_selected())
        dialog.exec()

    def save(self):
        if self.study:
            self.study.save_settings(self.host.ui_state())

    def open(self, study):
        if self.study:
            self.save()
        elif self.host.channels and QMessageBox.question(
                self.host, "시험 열기", "현재 임시 분석 화면을 저장된 시험으로 전환할까요?") != QMessageBox.StandardButton.Yes:
            return
        study.data = study.load()
        self.study = study
        self._display(study.data["settings"], reopening=True)

    def leave(self):
        if self.study:
            self.save()
            self.study = None
            self.host.study_button.setText("시험 목록")
            self.host.study_details_button.hide()
            self.host.merge_button.show()

    def _display(self, state=None, *, reopening=False):
        state = deepcopy(state if state is not None else self.host.ui_state())
        state["page"] = 0
        if not reopening:
            state["graphs"] = {}
        self.host.show_study_channels(self.study.channels(), state)
        self.host.study_button.setText(self.study.data["name"])

    def add(self, paths=None, target=None, *, cycle_only=False, csv_options=None):
        if not self.study or not self._available():
            return
        study = self.study
        kind = "수명시험(CYC)"
        if target is None and not cycle_only:
            kind, ok = QInputDialog.getItem(self.host, "데이터 유형", "등록할 데이터",
                                            [kind, "RPT"], 0, False)
            if not ok:
                return
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self.host, "시험 데이터 선택", "",
                                                   "배터리 데이터 (*.zip *.csv *.tsv *.cyc);;모든 파일 (*)")
        if not paths:
            return
        if kind == "RPT":
            self.host._start("study", lambda progress, cancel: study.preview_rpt(paths, cancel),
                             lambda result: self._confirm(study, result))
        else:
            self.host._start("study", lambda progress, cancel: load_inputs(paths, progress, cancel,
                             csv_options=csv_options),
                             lambda channels: self._map(study, channels, target))

    def _map(self, study, channels, target):
        dialog = QDialog(self.host)
        dialog.setWindowTitle("원본 채널을 셀에 연결")
        dialog.resize(1000, 510)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("새 구간의 누적 시작 Cycle을 직접 입력하세요. 기존 구간을 갱신하면 저장된 시작 번호를 사용합니다."))
        grid = QTableWidget(len(channels), 5)
        grid.setHorizontalHeaderLabels(["원본 채널 / Cycle", "연결 대상", "새 셀 이름", "누적 시작", "누적 범위"])
        grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(grid)
        rows = []
        for i, channel in enumerate(channels):
            cycles = channel.cycles
            source = QTableWidgetItem(f"{channel.name} · {cycles[0]}–{cycles[-1]}" if cycles else channel.name)
            source.setFlags(source.flags() & ~Qt.ItemFlag.ItemIsEditable)
            grid.setItem(i, 0, source)
            mapping = QComboBox()
            mapping.addItem("제외 / 대상을 선택하세요", None)
            mapping.addItem("새 셀에 추가", ("", None, None))
            for cell in study.data["cells"]:
                mapping.addItem(cell["name"] + " · 새 구간", (cell["id"], None, None))
                for segment in cell["segments"]:
                    mapping.addItem(f"{cell['name']} · {segment['start']}–{segment['end']} 갱신",
                                    (cell["id"], segment["id"], segment["start"]))
            name = QLineEdit(f"Cell-{len(study.data['cells']) + i + 1:02d}")
            start = QSpinBox()
            start.setRange(-1, 100_000_000)
            start.setSpecialValueText("직접 입력")
            start.setValue(-1)
            preview = QLabel()
            for col, widget in enumerate((mapping, name, start, preview), 1):
                grid.setCellWidget(i, col, widget)

            def display(*_, combo=mapping, edit=name, number=start, label=preview, source_cycles=cycles):
                pick = combo.currentData()
                edit.setEnabled(pick is not None and not pick[0])
                number.setEnabled(pick is not None and pick[1] is None)
                effective = (pick[2] if pick[1] else number.value()) if pick else -1
                label.setText(f"{effective}–{effective + source_cycles[-1] - source_cycles[0]}"
                              if effective >= 0 and source_cycles else "—")
            mapping.currentIndexChanged.connect(display)
            start.valueChanged.connect(display)
            if target and cycles and len(channels) == 1:
                for index in range(mapping.count()):
                    value = mapping.itemData(index)
                    if value and value[:2] == target:
                        mapping.setCurrentIndex(index)
                        break
            display()
            rows.append((channel, mapping, name, start))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        selection = []

        def accept():
            for channel, mapping, name, start in rows:
                value = mapping.currentData()
                if value and (not channel.cycles or (not value[1] and start.value() < 0)):
                    QMessageBox.warning(dialog, "연결 확인", "Cycle이 있는 채널을 선택하고 누적 시작 Cycle을 입력하세요.")
                    return
            selection[:] = [(c, m.currentData()[0], n.text(), s.value(), m.currentData()[1])
                            for c, m, n, s in rows if m.currentData()]
            dialog.accept()

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.DialogCode.Accepted and selection:
            self.host._start("study", lambda progress, cancel: study.preview_cycles(selection, cancel),
                             lambda result: self._confirm(study, result))

    def _confirm(self, study, result):
        candidate, messages, changed = result
        if self.study is not study:
            return
        dialog = QMessageBox(self.host)
        dialog.setWindowTitle("시험 데이터 반영")
        dialog.setText("아래 변경을 시험에 반영할까요?" if changed else "기존 데이터가 유지됩니다.")
        dialog.setInformativeText("\n".join(messages[:12]))
        if len(messages) > 12:
            dialog.setDetailedText("\n".join(messages))
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                  if changed else QMessageBox.StandardButton.Ok)
        dialog.setDefaultButton(QMessageBox.StandardButton.No if changed else QMessageBox.StandardButton.Ok)
        if changed and dialog.exec() == QMessageBox.StandardButton.Yes:
            state = self.host.ui_state()
            existing_ids = {cell["id"] for cell in study.data["cells"]}
            state["selected_channels"].extend(c["id"] for c in candidate["cells"] if c["id"] not in existing_ids)
            candidate["settings"] = state
            study.commit(candidate)
            self._display(state)
        elif not changed:
            dialog.exec()

    def details(self):
        if not self.study or not self._available():
            return
        study = self.study
        dialog = QDialog(self.host)
        dialog.resize(720, 520)
        dialog.setWindowTitle(study.data["name"] + " · 시험 이력")
        layout = QVBoxLayout(dialog)
        items = QListWidget()
        layout.addWidget(items)
        for cell in study.data["cells"]:
            for segment in cell["segments"]:
                row = QListWidgetItem(f"{cell['name']}  ·  {segment['start']}–{segment['end']} Cycle  ·  {segment['channel']}")
                row.setToolTip(segment["source"] + "\n마지막 Cycle 완료 여부 미확인")
                row.setData(Qt.ItemDataRole.UserRole, (cell["id"], segment["id"]))
                items.addItem(row)
        for rpt in study.data["rpt"]:
            row = QListWidgetItem("RPT  ·  " + rpt["name"])
            row.setToolTip(rpt["path"])
            row.setData(Qt.ItemDataRole.UserRole, ("rpt", rpt["sha256"]))
            items.addItem(row)
        bar = QHBoxLayout()
        layout.addLayout(bar)

        def update():
            item = items.currentItem()
            target = item.data(Qt.ItemDataRole.UserRole) if item else None
            if target and target[0] != "rpt":
                dialog.accept()
                self.add(target=target)

        def remove():
            item = items.currentItem()
            if not item or QMessageBox.question(dialog, "기록 제외", item.text() + "\n이 기록을 시험에서 제외할까요?") != QMessageBox.StandardButton.Yes:
                return
            cell_id, segment_id = item.data(Qt.ItemDataRole.UserRole)
            candidate = deepcopy(study.data)
            if cell_id == "rpt":
                candidate["rpt"] = [rpt for rpt in candidate["rpt"] if rpt["sha256"] != segment_id]
            else:
                for cell in candidate["cells"]:
                    if cell["id"] == cell_id:
                        cell["segments"] = [s for s in cell["segments"] if s["id"] != segment_id]
            candidate["history"].append(dict(at=_now(), text="시험에서 제외 · " + item.text()))
            study.commit(candidate)
            dialog.accept()
            self._display()

        def undo():
            if QMessageBox.question(dialog, "직전 상태 복원", "직전 데이터 변경을 되돌릴까요?") == QMessageBox.StandardButton.Yes:
                study.undo()
                dialog.accept()
                self._display()

        def history():
            message = QMessageBox(dialog)
            message.setWindowTitle("변경 이력")
            message.setText(f"변경 기록 {len(study.data['history'])}건")
            message.setDetailedText("\n\n".join(f"{item['at']}\n{item['text']}" for item in reversed(study.data["history"])))
            message.exec()

        def move():
            item = items.currentItem()
            target = item.data(Qt.ItemDataRole.UserRole) if item else None
            if not target or target[0] == "rpt":
                return
            candidate = deepcopy(study.data)
            origin = next(c for c in candidate["cells"] if c["id"] == target[0])
            others = [c for c in candidate["cells"] if c["id"] != target[0]]
            if not others:
                QMessageBox.information(dialog, "셀 연결 변경", "옮길 다른 셀이 없습니다.")
                return
            names = {c["name"]: c for c in others}
            name, ok = QInputDialog.getItem(dialog, "셀 연결 변경", "옮길 셀", list(names), 0, False)
            if not ok:
                return
            segment = next(s for s in origin["segments"] if s["id"] == target[1])
            destination = names[name]
            incoming = {int(cycle) + segment["offset"] for cycle in segment["signatures"]}
            occupied = {int(cycle) + s["offset"] for s in destination["segments"] for cycle in s["signatures"]}
            clashes = sorted(incoming & occupied)
            if clashes:
                QMessageBox.warning(dialog, "Cycle 겹침", "겹치는 Cycle: " + ", ".join(map(str, clashes[:12])))
                return
            origin["segments"].remove(segment)
            destination["segments"].append(segment)
            candidate["history"].append(dict(at=_now(), text=f"{item.text()} → {name} 셀로 이동"))
            study.commit(candidate)
            dialog.accept()
            self._display()

        _button(bar, "최신 파일로 갱신", update)
        _button(bar, "기록 제외", remove)
        _button(bar, "다른 셀로 이동", move)
        _button(bar, "직전 상태 복원", undo).setEnabled(bool(study.data["undo"]))
        _button(bar, "변경 이력", history)
        _button(bar, "닫기", dialog.close)
        dialog.exec()
