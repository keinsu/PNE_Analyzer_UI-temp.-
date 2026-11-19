"""Select per-file import rules and reuse saved RPT items or presets."""
import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from .analysis import cycle_aggregates
from .import_rules import FIELDS, KINDS, OPERATIONS, apply_rules, rpt_rows, step_options


def _library():
    settings = QSettings("BatteryAnalysisStudio", "ImportRules")
    try:
        library = json.loads(settings.value("library", "{}"))
        return settings, {"items": library.get("items", {}), "presets": library.get("presets", {})}
    except (TypeError, ValueError, AttributeError):
        return settings, {"items": {}, "presets": {}}


class RuleDialog(QDialog):
    def __init__(self, parent, channels, cycle_only=False, initial=None):
        super().__init__(parent)
        self.channels = channels
        self.setWindowTitle("가져오기 · 파일 분류 및 Step 지정")
        self.resize(760, 650)
        self.settings, self.library = _library()
        self.items = []
        self.options = [entry for channel in channels for entry in step_options(channel)]
        self.steps = list(dict.fromkeys(step for step, *_ in self.options))
        layout = QVBoxLayout(self)
        first = channels[0]
        layout.addWidget(QLabel(str(first.metadata.get("source_member") or Path(first.source_path).name)))
        form = QFormLayout()
        layout.addLayout(form)
        self.kind = QComboBox()
        for key, label in KINDS.items():
            if not cycle_only or key == "cycle":
                self.kind.addItem(label, key)
        form.addRow("파일 분류", self.kind)
        self.step_list = QListWidget()
        self.step_list.setMaximumHeight(125)
        for step, mode, cycles, rows in dict.fromkeys(self.options):
            self.step_list.addItem(f"Step {step} · {mode} · {cycles:,} Cycle · {rows:,}점")
        form.addRow("추출된 Step", self.step_list)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)
        cycle = QWidget()
        cycle_form = QFormLayout(cycle)
        self.capacity = {}
        for mode, label in [("CHARGE", "충전 용량"), ("DISCHARGE", "방전 용량")]:
            picks = QListWidget()
            picks.setMaximumHeight(105)
            picks.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
            for step in dict.fromkeys(step for step, direction, *_ in self.options if direction == mode):
                picks.addItem(step)
            self.capacity[mode] = picks
            cycle_form.addRow(label, picks)
        cycle_form.addRow(QLabel("선택한 Step의 용량 증가분을 합산합니다. 선택하지 않은 방향은 기존 자동 규칙을 사용합니다."))
        self.pages.addWidget(cycle)
        rpt = QWidget()
        rpt_layout = QVBoxLayout(rpt)
        row = QHBoxLayout()
        self.item_name = QLineEdit()
        self.item_name.setPlaceholderText("결과 이름")
        self.step = QComboBox()
        self.step.addItems(self.steps)
        self.field, self.operation = QComboBox(), QComboBox()
        for key, label in FIELDS.items():
            self.field.addItem(label, key)
        for key, label in OPERATIONS.items():
            self.operation.addItem(label, key)
        for widget in (self.item_name, self.step, self.field, self.operation):
            row.addWidget(widget)
        self._button(row, "항목 추가", self.add_item)
        rpt_layout.addLayout(row)
        self.item_list = QListWidget()
        self.item_list.currentRowChanged.connect(self.edit_item)
        rpt_layout.addWidget(self.item_list)
        item_actions = QHBoxLayout()
        self._button(item_actions, "목록에서 가져오기", self.load_item)
        self._button(item_actions, "선택 항목을 목록에 저장", self.save_item)
        self._button(item_actions, "선택 항목 수정", self.update_item)
        self._button(item_actions, "선택 항목 제외", self.remove_item)
        rpt_layout.addLayout(item_actions)
        self.pages.addWidget(rpt)
        self.pages.addWidget(QLabel("곡선은 저장하고 Cycle 용량과 RPT 항목 집계에서는 제외합니다."))
        self.kind.currentTextChanged.connect(lambda: self.pages.setCurrentIndex(list(KINDS).index(self.kind.currentData())))
        actions = QHBoxLayout()
        self._button(actions, "프리셋 불러오기", self.load_preset)
        self._button(actions, "프리셋 저장", self.save_preset)
        self._button(actions, "계산 미리보기", self.preview)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if initial and self.kind.findData(initial["kind"]) >= 0:
            try:
                self.set_rules(initial)
            except ValueError as exc:
                layout.addWidget(QLabel(str(exc)))

    def _button(self, row, label, action):
        button = QPushButton(label)
        button.clicked.connect(action)
        row.addWidget(button)

    def rules(self):
        return dict(kind=self.kind.currentData(),
                    capacity={mode: [item.text() for item in picks.selectedItems()]
                              for mode, picks in self.capacity.items()},
                    rpt=list(self.items))

    def set_rules(self, rules):
        index = self.kind.findData(rules["kind"])
        if index < 0:
            raise ValueError("이 가져오기에서는 Cycle 프리셋만 적용할 수 있습니다.")
        missing = []
        for mode, picks in self.capacity.items():
            wanted = set(rules.get("capacity", {}).get(mode, []))
            available = {picks.item(i).text() for i in range(picks.count())}
            missing.extend(sorted(wanted - available))
        if missing:
            raise ValueError("이 파일에 없는 용량 Step: " + ", ".join(missing))
        self.kind.setCurrentIndex(index)
        for mode, picks in self.capacity.items():
            wanted = set(rules.get("capacity", {}).get(mode, []))
            for i in range(picks.count()):
                picks.item(i).setSelected(picks.item(i).text() in wanted)
        self.items = list(rules.get("rpt", []))
        self.show_items()

    def show_items(self):
        self.item_list.clear()
        for item in self.items:
            self.item_list.addItem(f"{item['name']} · Step {item['step']} · {FIELDS[item['field']]} · {OPERATIONS[item['operation']]}")

    def add_item(self):
        name = self.item_name.text().strip()
        if not name or not self.step.currentText():
            QMessageBox.warning(self, "RPT 항목", "결과 이름과 Step을 지정하세요.")
            return
        entry = dict(name=name, step=self.step.currentText(), field=self.field.currentData(),
                     operation=self.operation.currentData())
        if entry not in self.items:
            self.items.append(entry)
            self.show_items()

    def edit_item(self, row):
        if row >= 0:
            item = self.items[row]
            self.item_name.setText(item["name"])
            self.step.setCurrentIndex(self.step.findText(item["step"]))
            self.field.setCurrentIndex(self.field.findData(item["field"]))
            self.operation.setCurrentIndex(self.operation.findData(item["operation"]))

    def update_item(self):
        row = self.item_list.currentRow()
        name = self.item_name.text().strip()
        if row < 0 or not name or not self.step.currentText():
            QMessageBox.warning(self, "RPT 항목", "목록에서 항목을 선택한 뒤 이름과 Step을 지정하세요.")
            return
        self.items[row] = dict(name=name, step=self.step.currentText(), field=self.field.currentData(),
                               operation=self.operation.currentData())
        self.show_items()
        self.item_list.setCurrentRow(row)

    def remove_item(self):
        row = self.item_list.currentRow()
        if row >= 0:
            self.items.pop(row)
            self.show_items()

    def save_item(self):
        row = self.item_list.currentRow()
        if row >= 0:
            self.save_entry("items", self.items[row], self.items[row]["name"])

    def load_item(self):
        entry = self.choose_entry("items", "저장된 RPT 항목")
        if entry:
            self.item_name.setText(entry["name"])
            self.field.setCurrentIndex(self.field.findData(entry["field"]))
            self.operation.setCurrentIndex(self.operation.findData(entry["operation"]))
            index = self.step.findText(entry["step"])
            self.step.setCurrentIndex(index)
            if index >= 0:
                self.add_item()
            else:
                QMessageBox.warning(self, "Step 확인", "저장된 Step이 이 파일에 없습니다. Step을 다시 선택하고 항목을 추가하세요.")

    def save_entry(self, group, entry, default=""):
        name, ok = QInputDialog.getText(self, "이름으로 저장", "저장 이름", text=default)
        name = name.strip()
        if not ok or not name:
            return
        if name in self.library[group] and QMessageBox.question(
                self, "저장 확인", f"'{name}' 항목을 바꿀까요?") != QMessageBox.StandardButton.Yes:
            return
        self.library[group][name] = entry
        self.settings.setValue("library", json.dumps(self.library, ensure_ascii=False))

    def choose_entry(self, group, title):
        options = list(self.library[group])
        if not options:
            QMessageBox.information(self, title, "저장된 항목이 없습니다.")
            return None
        name, ok = QInputDialog.getItem(self, title, "이름", options, 0, False)
        return self.library[group][name] if ok else None

    def save_preset(self):
        if self.validate():
            self.save_entry("presets", self.rules())

    def load_preset(self):
        preset = self.choose_entry("presets", "저장된 가져오기 프리셋")
        if preset:
            try:
                self.set_rules(preset)
            except ValueError as exc:
                QMessageBox.warning(self, "프리셋 확인", str(exc))

    def validate(self):
        rules = self.rules()
        if rules["kind"] != "rpt":
            return True
        if not self.items or any(item["step"] not in self.steps for item in self.items):
            QMessageBox.warning(self, "RPT 항목 확인", "현재 파일에 있는 Step을 사용한 RPT 항목을 추가하세요.")
            return False
        return True

    def confirm(self):
        if self.validate():
            self.accept()

    def preview(self):
        if not self.validate():
            return
        rules = self.rules()
        rows = []
        try:
            for channel in self.channels:
                if rules["kind"] == "cycle" and channel.cycles:
                    prepared = apply_rules(channel, rules)
                    for row in cycle_aggregates(prepared, cycles=[channel.cycles[0]], allow_empty=True)["representative"].to_dict("records"):
                        rows.append(f"{channel.name} · Cycle {row['Cycle']} · 충전 {row.get('Charge_Capacity_mAh', float('nan')):.6g} / 방전 {row.get('Discharge_Capacity_mAh', float('nan')):.6g} mAh")
                elif rules["kind"] == "rpt":
                    for row in rpt_rows(channel, rules["rpt"])[:12]:
                        rows.append(f"{channel.name} · {row['Item']} · Step {row['Step']} · {row['Value'] if row['Value'] is not None else row['Status']}")
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "미리보기", str(exc))
            return
        QMessageBox.information(self, "첫 결과 미리보기", "\n".join(rows[:15]) if rows else "집계할 항목이 없습니다.")


def classify_channels(parent, channels, cycle_only=False, initial=None):
    groups = {}
    for channel in channels:
        groups.setdefault((channel.source_path, channel.metadata.get("source_member", "")), []).append(channel)
    configured = []
    for group in groups.values():
        dialog = RuleDialog(parent, group, cycle_only, initial)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        configured.extend(apply_rules(channel, dialog.rules()) for channel in group)
    return configured
