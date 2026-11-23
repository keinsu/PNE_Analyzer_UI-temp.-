"""Trial library and explicit channel-to-cell import workflow."""
from pathlib import Path
import os
from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .ingestion import load_inputs
from .studies import Study, _cell_label, _now
from .import_dialog import classify_channels


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
        if not reopening:
            state["graphs"] = {}
        self.host.show_study_channels(self.study.channels(), state)
        self.host.study_button.setText(self.study.data["name"])

    def add(self, paths=None, target=None, *, cycle_only=False, csv_options=None):
        if not self.study or not self._available():
            return
        study = self.study
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self.host, "시험 데이터 선택", "",
                                                   "배터리 데이터 (*.zip *.csv *.tsv *.cyc);;모든 파일 (*)")
        if not paths:
            return
        def ready(channels):
            initial = None
            if target:
                cell = next(c for c in study.data["cells"] if c["id"] == target[0])
                initial = next((s.get("import_rules") for s in cell["segments"] if s["id"] == target[1]), None)
            configured = classify_channels(self.host, channels, cycle_only=target is not None, initial=initial, paths=paths)
            if configured is None:
                return
            cycle = [c for c in configured if c.metadata["import_kind"] == "cycle"]
            auxiliary = [c for c in configured if c.metadata["import_kind"] != "cycle"]
            self._map(study, cycle, target, auxiliary)

        self.host._start("study", lambda progress, cancel: load_inputs(
            paths, progress, cancel, csv_options=csv_options), ready)

    def _prepare_import(self, study, selection, auxiliary):
        self.host._start("study", lambda progress, cancel: study.preview_cycles(selection, cancel, auxiliary),
                         lambda result: self._confirm(study, result))

    def _map(self, study, channels, target, auxiliary=()):
        rpt = [channel for channel in auxiliary if channel.metadata.get("import_kind") == "rpt"]
        other = [channel for channel in auxiliary if channel.metadata.get("import_kind") != "rpt"]
        if not channels and not rpt:
            if other:
                self._prepare_import(study, [], other)
            return
        dialog = QDialog(self.host)
        dialog.setWindowTitle("데이터를 셀에 등록")
        dialog.resize(1080, 470)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("측정 채널별로 관리 ID를 연결하고 수명 시작 또는 RPT 측정 시점을 지정하세요."))
        mapped = [(channel, False) for channel in channels] + [(channel, True) for channel in rpt]
        grid = QTableWidget(len(mapped), 5)
        grid.setHorizontalHeaderLabels(["측정 채널", "연결 대상", "새 관리 ID", "새 셀 이름", "등록 시점"])
        grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        grid.verticalHeader().setDefaultSectionSize(42)
        layout.addWidget(grid)
        rows = []
        for i, (channel, is_rpt) in enumerate(mapped):
            suffix = "RPT" if is_rpt else "수명"
            source = QTableWidgetItem(f"{channel.name} · {suffix}")
            source.setFlags(source.flags() & ~Qt.ItemFlag.ItemIsEditable)
            grid.setItem(i, 0, source)
            mapping = QComboBox()
            mapping.addItem("선택 · 등록 보류", None)
            mapping.addItem("새 셀 등록", ("", None, None))
            for cell in study.data["cells"]:
                label = _cell_label(cell)
                mapping.addItem(label if is_rpt else f"{label} · 새 구간", (cell["id"], None, None))
                if not is_rpt:
                    for segment in cell["segments"]:
                        mapping.addItem(f"{label} · {segment['start']}–{segment['end']} 갱신",
                                        (cell["id"], segment["id"], segment["start"]))
            identifier = QLineEdit()
            identifier.setPlaceholderText("예: Cell-01")
            name = QLineEdit()
            name.setPlaceholderText("비워 두면 관리 ID 사용")
            start = QSpinBox()
            start.setRange(0, 100_000_000)
            start.setSpecialValueText("측정 Cycle" if is_rpt else "누적 시작")
            placement = QWidget()
            placement_layout = QHBoxLayout(placement)
            placement_layout.setContentsMargins(0, 0, 0, 0)
            stage = QComboBox()
            stage.addItem("시점 선택", None)
            stage.addItem("BOL", "bol")
            stage.addItem("측정 Cycle", "checkpoint")
            if is_rpt:
                placement_layout.addWidget(stage)
            placement_layout.addWidget(start)
            for col, widget in enumerate((mapping, identifier, name, placement), 1):
                grid.setCellWidget(i, col, widget)

            def display(*_, combo=mapping, management=identifier, edit=name, number=start,
                        timing=stage, rpt_row=is_rpt):
                pick = combo.currentData()
                new_cell = pick is not None and not pick[0]
                management.setEnabled(new_cell)
                edit.setEnabled(new_cell)
                timing.setEnabled(pick is not None)
                if rpt_row:
                    number.setVisible(timing.currentData() == "checkpoint")
                    number.setEnabled(pick is not None)
                else:
                    number.setEnabled(pick is not None and pick[1] is None)
                    if pick and pick[1] is not None:
                        number.setValue(pick[2])
            mapping.currentIndexChanged.connect(display)
            stage.currentIndexChanged.connect(display)

            old = next((record.get("rpt_link", {}) for record in study.data["rpt"]
                        if is_rpt and record.get("id") == channel.id), {})
            wanted = (old["cell_id"], None) if old else target if not is_rpt and len(channels) == 1 else None
            if wanted:
                for option in range(mapping.count()):
                    value = mapping.itemData(option)
                    if value and value[:2] == wanted:
                        mapping.setCurrentIndex(option)
                        break
            if old:
                stage.setCurrentIndex(stage.findData(old.get("stage", "checkpoint")))
                start.setValue(old["cycle"])
            if not study.data["cells"] and not wanted:
                mapping.setCurrentIndex(1)
            display()
            rows.append((channel, is_rpt, mapping, name, identifier, stage, start))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        selections, linked_rpt = [], []

        def confirm():
            life, rpt_imports = [], []
            for channel, rpt_row, mapping, name, identifier, stage, start in rows:
                pick = mapping.currentData()
                if pick is None:
                    continue
                cell_id, segment_id, fixed_start = pick
                management_id = identifier.text().strip()
                cell_name = name.text().strip() or management_id
                if not cell_id and not management_id:
                    QMessageBox.warning(dialog, "관리 ID", f"{channel.name}: 새 셀의 관리 ID를 입력하세요.")
                    return
                if rpt_row:
                    timing = stage.currentData()
                    if timing is None or (timing == "checkpoint" and not start.value()):
                        QMessageBox.warning(dialog, "RPT 시점", f"{channel.name}: BOL 또는 측정 Cycle을 지정하세요.")
                        return
                    link = dict(cell_id=cell_id, stage=timing, cycle=0 if timing == "bol" else start.value())
                    if not cell_id:
                        link.update(cell_name=cell_name, management_id=management_id)
                    rpt_imports.append(replace(channel, metadata=dict(channel.metadata, rpt_link=link)))
                else:
                    effective_start = fixed_start if segment_id else start.value()
                    if not channel.cycles or (not segment_id and not effective_start):
                        QMessageBox.warning(dialog, "수명 구간", f"{channel.name}: 누적 시작 Cycle을 지정하세요.")
                        return
                    life.append((channel, cell_id, cell_name, effective_start, segment_id, management_id))
            if not (life or rpt_imports or other):
                QMessageBox.warning(dialog, "데이터 등록", "등록할 채널과 연결 대상을 선택하세요.")
                return
            selections[:] = life
            linked_rpt[:] = rpt_imports
            dialog.accept()

        buttons.accepted.connect(confirm)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._prepare_import(study, selections, [*linked_rpt, *other])

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
            new_ids = [cell["id"] for cell in candidate["cells"] if cell["id"] not in existing_ids]
            if not state.get("active_channel") and new_ids:
                state["active_channel"] = new_ids[0]
                state["selected_channels"] = [new_ids[0]]
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
        picker = QComboBox()
        active = self.host.active_channel()
        active_id = active.metadata.get("rpt_link", {}).get("cell_id", active.id) if active else None
        cells = study.data["cells"]
        for cell in cells:
            picker.addItem(_cell_label(cell), cell["id"])
        unlinked = [record for record in study.data["rpt"] if not record.get("rpt_link", {}).get("cell_id")]
        if unlinked:
            picker.addItem("미연결 기록", None)
        layout.addWidget(picker)
        reference_label = QLabel()
        layout.addWidget(reference_label)
        items = QListWidget()
        layout.addWidget(items)

        def display():
            items.clear()
            cell_id = picker.currentData()
            if cell_id is None:
                reference_label.hide()
                for record in unlinked:
                    label = "기타" if record.get("kind") == "other" else "RPT"
                    row = QListWidgetItem(f"{label} · {record['name']}")
                    row.setToolTip(record["path"])
                    row.setData(Qt.ItemDataRole.UserRole, ("rpt", record.get("id", record["sha256"])))
                    items.addItem(row)
                return
            cell = next(c for c in cells if c["id"] == cell_id)
            source_id = cell.get("bol_reference_id") or cell_id
            reference = next((c for c in cells if c["id"] == source_id), None)
            reference_label.show()
            reference_label.setText(f"기준 BOL · {_cell_label(reference)}" if reference and study.bol_records(source_id)
                                    else "기준 BOL 미등록")
            for event in study.timeline(cell_id):
                row = QListWidgetItem(f"{event['label']} · {event['channel']}")
                row.setToolTip(event["source"])
                target = cell_id if event["kind"] == "life" else "rpt"
                row.setData(Qt.ItemDataRole.UserRole, (target, event["record_id"]))
                items.addItem(row)
            if items.count():
                items.setCurrentRow(0)

        picker.currentIndexChanged.connect(display)
        picker.setCurrentIndex(max(0, picker.findData(active_id)))
        display()
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
                candidate["rpt"] = [rpt for rpt in candidate["rpt"] if rpt.get("id", rpt["sha256"]) != segment_id]
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

        def change_reference():
            cell_id = picker.currentData()
            if cell_id is None:
                return
            options = [("자체 BOL 사용", None)] + [(_cell_label(c), c["id"]) for c in cells
                       if c["id"] != cell_id and study.bol_records(c["id"])]
            current = next(c for c in cells if c["id"] == cell_id).get("bol_reference_id")
            index = next((i for i, (_, item_id) in enumerate(options) if current == item_id), 0)
            names = dict(options)
            label, ok = QInputDialog.getItem(dialog, "기준 BOL 선택", "기준 셀", list(names), index, False)
            if ok:
                try:
                    study.set_bol_reference(cell_id, names[label])
                except (OSError, ValueError) as exc:
                    QMessageBox.warning(dialog, "기준 BOL", str(exc))
                    return
                dialog.accept()
                self._display()

        _button(bar, "기준 BOL 선택…", change_reference)
        _button(bar, "최신 파일로 갱신", update)
        _button(bar, "기록 제외", remove)
        _button(bar, "다른 셀로 이동", move)
        _button(bar, "직전 상태 복원", undo).setEnabled(bool(study.data["undo"]))
        _button(bar, "변경 이력", history)
        _button(bar, "닫기", dialog.close)
        dialog.exec()
