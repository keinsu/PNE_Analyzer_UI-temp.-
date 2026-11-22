"""Select one import rule per channel group and reuse saved RPT items or presets."""
import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from .import_rules import FIELDS, KINDS, OPERATIONS, apply_rules, step_options


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
        self.setWindowTitle("가져오기 · 파일 분류 및 Step 지정")
        self.resize(760, 650)
        self.settings, self.library = _library()
        self.items = []
        self.options = step_options(channels[0])
        self.steps = list(dict.fromkeys(step for step, *_ in self.options))
        layout = QVBoxLayout(self)
        first = channels[0]
        source = QLabel(f"첫 채널: {first.name}  ·  적용 대상: {len(channels):,}개 채널")
        source.setWordWrap(True)
        source.setToolTip(str(first.metadata.get("source_member") or first.source_path))
        layout.addWidget(source)
        form = QFormLayout()
        layout.addLayout(form)
        self.kind = QComboBox()
        for key, label in KINDS.items():
            if not cycle_only or key == "cycle":
                self.kind.addItem(label, key)
        form.addRow("파일 분류", self.kind)
        self.step_list = QListWidget()
        self.step_list.setMaximumHeight(125)
        self.step_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.step_list.setStyleSheet('QListWidget { background: #202d39; color: #f2f6fa; border: 1px solid #71869a; }')
        for step, mode, cycles, rows in dict.fromkeys(self.options):
            self.step_list.addItem(f"Step {step} · {mode} · {cycles:,} Cycle · {rows:,}점")
        form.addRow("첫 채널의 Step", self.step_list)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)
        cycle = QWidget()
        cycle_form = QFormLayout(cycle)
        self.capacity = {}
        for mode, label in [("CHARGE", "충전 용량"), ("DISCHARGE", "방전 용량")]:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(110)
            scroll.setStyleSheet("QScrollArea { border: 1px solid #71869a; border-radius: 4px; }")
            panel = QWidget()
            panel.setObjectName("capacityStepPanel")
            panel.setStyleSheet("QWidget#capacityStepPanel { background: #202d39; }"
                                "QCheckBox { background: #202d39; color: #f2f6fa; padding: 5px 10px; }"
                                "QCheckBox:hover { background: #314961; }"
                                "QCheckBox:checked { background: #254b6d; }")
            options = QVBoxLayout(panel)
            options.setContentsMargins(6, 4, 6, 4)
            options.setSpacing(3)
            checks = {}
            for step in dict.fromkeys(step for step, direction, *_ in self.options if direction == mode):
                checkbox = QCheckBox(f"Step {step}")
                options.addWidget(checkbox)
                checks[step] = checkbox
            if not checks:
                options.addWidget(QLabel("해당 방향의 Step 없음"))
            options.addStretch()
            self.capacity[mode] = checks
            scroll.setWidget(panel)
            cycle_form.addRow(label, scroll)
        cycle_form.addRow(QLabel("선택한 Step의 용량 증가분을 합산합니다. 선택하지 않은 방향은 기존 자동 규칙을 사용합니다."))
        self.pages.addWidget(cycle)
        rpt = QWidget()
        rpt_layout = QVBoxLayout(rpt)
        standard = QFormLayout()
        self.rpt_steps = {}
        for key, label, direction in (("charge", "RPT 충전용량 Step", "CHARGE"),
                                      ("discharge", "RPT 방전용량 Step", "DISCHARGE"),
                                      ("pulse", "방전 10초 DCR Step", "DISCHARGE")):
            combo = QComboBox()
            combo.addItem("선택", "")
            for number in dict.fromkeys(step for step, mode, *_ in self.options if mode == direction):
                combo.addItem(f"Step {number}", number)
            self.rpt_steps[key] = combo
            standard.addRow(label, combo)
        self.soc_order = QLineEdit()
        self.soc_order.setPlaceholderText("펄스 순서대로 입력 · 예: 100,90,80,70,60,50")
        standard.addRow("반복별 SOC (%)", self.soc_order)
        rpt_layout.addLayout(standard)
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
                    capacity={mode: [step for step, checkbox in picks.items() if checkbox.isChecked()]
                              for mode, picks in self.capacity.items()},
                    rpt=list(self.items),
                    rpt_protocol={**{name: combo.currentData() for name, combo in self.rpt_steps.items()},
                                  'soc': self.soc_order.text().strip()} if self.kind.currentData() == 'rpt' else {})

    def set_rules(self, rules):
        index = self.kind.findData(rules["kind"])
        if index < 0:
            raise ValueError("이 가져오기에서는 Cycle 프리셋만 적용할 수 있습니다.")
        missing = []
        for mode, picks in self.capacity.items():
            wanted = set(rules.get("capacity", {}).get(mode, []))
            available = set(picks)
            missing.extend(sorted(wanted - available))
        if missing:
            raise ValueError("이 파일에 없는 용량 Step: " + ", ".join(missing))
        self.kind.setCurrentIndex(index)
        for mode, picks in self.capacity.items():
            wanted = set(rules.get("capacity", {}).get(mode, []))
            for step, checkbox in picks.items():
                checkbox.setChecked(step in wanted)
        protocol = rules.get("rpt_protocol", {})
        for name, combo in self.rpt_steps.items():
            value = protocol.get(name, "")
            combo.setCurrentIndex(max(0, combo.findData(value)))
        self.soc_order.setText(protocol.get("soc", ""))
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
        protocol = rules["rpt_protocol"]
        try:
            if any(protocol.values()):
                if not all(protocol.values()):
                    raise ValueError("RPT 충전·방전 용량 Step, 방전 DCR Step, 반복별 SOC를 모두 지정하세요.")
                values = [float(value.strip()) for value in protocol["soc"].split(",")]
                if not values or not all(0 <= value <= 100 for value in values):
                    raise ValueError("SOC는 0~100 범위의 값을 쉼표로 구분해 입력하세요.")
            elif not self.items:
                raise ValueError("RPT 용량·DCR Step 또는 추출 항목을 지정하세요.")
            if any(item["step"] not in self.steps for item in self.items):
                raise ValueError("현재 파일에 있는 Step을 사용한 RPT 항목을 지정하세요.")
        except ValueError as exc:
            QMessageBox.warning(self, "RPT 항목 확인", str(exc))
            return False
        return True

    def confirm(self):
        if self.validate():
            self.accept()


def classify_channels(parent, channels, cycle_only=False, initial=None, *, paths=()):
    roots = [Path(path).resolve() for path in paths if Path(path).is_dir()]
    groups = {}
    for channel in channels:
        path = Path(channel.source_path).resolve()
        if path.suffix.lower() == ".zip":
            group = path
        else:
            group = next((root for root in roots if path.is_relative_to(root)), path.parent)
        groups.setdefault(group, []).append(channel)
    configured = []
    for group in groups.values():
        dialog = RuleDialog(parent, group, cycle_only, initial)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        rules = dialog.rules()
        configured.extend(apply_rules(channel, rules) for channel in group)
    return configured
