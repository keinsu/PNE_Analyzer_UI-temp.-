"""Paste an editable Origin Cycle comparison onto a PowerPoint slide."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import math
import re
import time

import numpy as np


_MISSING = -1.23456789e-300  # Origin's documented double-precision missing value.
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_DIRECTION = {"charge": "충전", "discharge": "방전", "rest": "휴지"}
_TRANSFER_ROWS = 65_536


@dataclass(frozen=True)
class OriginCurve:
    label: str
    description: str
    x: np.ndarray
    y: np.ndarray
    color: str
    dash: bool
    right_axis: bool = False


def prepare_curves(curves, source_id):
    """Snapshot one comparison set without substituting downsampled plot points."""
    selected = [c for c in curves if c.get("source_id") == source_id and c.get("cycle") is not None]
    counts = Counter((c["cycle"], str(c.get("direction", "")).lower()) for c in selected)
    selected.sort(key=lambda c: int(c["cycle"]))
    names = Counter()
    result = []
    for curve in selected:
        x, y = np.asarray(curve["x"], dtype=float), np.asarray(curve["y"], dtype=float)
        if x.ndim != 1 or y.ndim != 1 or len(x) != len(y) or not np.any(np.isfinite(x) & np.isfinite(y)):
            continue
        cycle, direction = curve["cycle"], str(curve.get("direction", "")).lower()
        label = f"Cycle {cycle}"
        if direction:
            label += f" · {_DIRECTION.get(direction, direction)}"
        if counts[(cycle, direction)] > 1 and curve.get("step"):
            label += f" · Step {curve['step']}"
        names[label] += 1
        unique = label if names[label] == 1 else f"{label} ({names[label]})"
        color = str(curve.get("color", "#2679B5"))
        result.append(OriginCurve(unique, str(curve.get("name", unique)), x, y,
                                  color if _COLOR.fullmatch(color) else "#2679B5", direction == "discharge"))
    return result


def prepare_summary_curves(curves):
    result = []
    for curve in curves:
        x, y = np.asarray(curve["x"], dtype=float), np.asarray(curve["y"], dtype=float)
        if x.ndim != 1 or y.ndim != 1 or len(x) != len(y) or not np.any(np.isfinite(x) & np.isfinite(y)):
            continue
        name = str(curve.get("name", "Summary"))
        color = str(curve.get("color", "#2679B5"))
        right = bool(curve.get("right_axis"))
        result.append(OriginCurve(name, name, x, y, color if _COLOR.fullmatch(color) else "#2679B5",
                                  right or curve.get("direction") == "discharge", right))
    return result


@contextmanager
def _origin_session():
    try:
        import pythoncom
        from win32com.client import DispatchEx
    except ImportError as exc:
        raise RuntimeError("Origin 연동 모듈을 불러오지 못했습니다. 곡선 CSV는 계속 사용할 수 있습니다.") from exc
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    app = None
    try:
        try:
            # This ProgID launches its own project and leaves open Origin sessions alone.
            app = DispatchEx("Origin.Application", clsctx=pythoncom.CLSCTX_LOCAL_SERVER)
        except pythoncom.com_error as exc:
            code = exc.hresult & 0xFFFFFFFF
            if code in (0x80040154, 0x800401F3):
                raise RuntimeError("이 PC에서 Origin을 찾을 수 없습니다. Origin이 설치된 Windows PC에서 사용하세요.") from exc
            raise RuntimeError(f"Origin 자동화를 시작하지 못했습니다 (0x{code:08X}). Origin 설치와 실행 상태를 확인하세요.") from exc
        yield app
    finally:
        try:
            if app is not None:
                app.Exit()
        except pythoncom.com_error:
            pass
        finally:
            del app
            pythoncom.CoUninitialize()


def _axis_text(text):
    # Axis labels are supplied by the application's fixed axis menus. Remove
    # LabTalk metacharacters as well, in case another caller provides a label.
    return re.sub(r'["\\%$;\r\n]', ' ', str(text)).strip()


def _check(cancel):
    if cancel():
        raise InterruptedError("PPT 붙여넣기 취소")


def _build_graph(app, curves, name, x_label, y_label, progress, cancel, ranges, right_label=''):
    book = app.WorksheetPages.Add("Origin")
    book.LongName = name
    sheet = book.Layers(0)
    sheet.Cols = 2 * len(curves)
    sheet.Rows = max(len(curve.x) for curve in curves)
    graph = app.GraphPages.Add("DoubleY" if right_label else "Origin")
    graph.LongName = name
    layers = [graph.Layers(0)]
    if right_label:
        try:
            layers.append(graph.Layers(1))
        except Exception as exc:
            raise RuntimeError("Origin의 DoubleY 템플릿에서 오른쪽 축을 열 수 없습니다.") from exc
    styles = [[] for layer in layers]
    for i, curve in enumerate(curves):
        _check(cancel)
        progress(i, len(curves), f"Origin · {curve.label}")
        x_col, y_col = 2 * i, 2 * i + 1
        x_column, y_column = sheet.Columns(x_col), sheet.Columns(y_col)
        x_column.Type, y_column.Type = 3, 0  # Origin COLTYPE_X and COLTYPE_Y.
        x_column.LongName, y_column.LongName = x_label, curve.label
        side = int(bool(right_label and curve.right_axis))
        layer = layers[side]
        y_column.Comments = f"{right_label if side else y_label} · {curve.description}"
        for start in range(0, len(curve.x), _TRANSFER_ROWS):
            _check(cancel)
            end = start + _TRANSFER_ROWS
            for column, values in ((x_col, curve.x[start:end]), (y_col, curve.y[start:end])):
                data = np.where(np.isfinite(values), values, _MISSING).tolist()
                if not app.PutWorksheet(book.Name, data, start, column):
                    raise RuntimeError(f"Origin에 {curve.label} 데이터를 쓰지 못했습니다.")
        data_range = sheet.NewDataRange(0, x_col, len(curve.x) - 1, y_col)
        plot_type = 201 if side or np.count_nonzero(np.isfinite(curve.y)) == 1 else 200
        if layer.DataPlots.Add(data_range, plot_type) is None:
            raise RuntimeError(f"Origin에서 {curve.label} 곡선을 그리지 못했습니다.")
        styles[side].append(f'layer.plot={len(styles[side]) + 1}; set %C -cl color("{curve.color}"); '
                      f'set %C -d {1 if curve.dash else 0}; set %C -wp 1.6;')
    axes = []
    for axis, limits in zip(("x", "y"), ranges):
        if len(limits) == 2 and all(math.isfinite(v) for v in limits) and limits[0] < limits[1]:
            axes.append(f"layer.{axis}.from={limits[0]:.16g}; layer.{axis}.to={limits[1]:.16g};")
    legend = 'legendupdate dest:=layer update:=reconstruct mode:=lname;'
    left_script = ' '.join(styles[0] + ['layer -a;', *axes, f'label -xb "{_axis_text(x_label)}";',
                                      f'label -yl "{_axis_text(y_label)}";', legend])
    if not layers[0].Execute(left_script):
        raise RuntimeError("Origin에서 축과 Cycle 범례를 설정하지 못했습니다.")
    if right_label:
        right_script = ' '.join(styles[1] + ['layer -a;', f'label -yr "{_axis_text(right_label)}";', legend])
        if not layers[1].Execute(right_script):
            raise RuntimeError("Origin에서 오른쪽 DCIR 축을 설정하지 못했습니다.")
    return graph.Name


def _active_slide():
    import pythoncom
    from win32com.client import GetActiveObject
    try:
        powerpoint = GetActiveObject("PowerPoint.Application")
    except pythoncom.com_error as exc:
        raise RuntimeError("PowerPoint 데스크톱 앱에서 붙여넣을 PPT를 먼저 여세요.") from exc
    try:
        window = powerpoint.ActiveWindow
        presentation = window.Presentation
        slide = window.View.Slide
        slide_number = int(slide.SlideIndex)
        name = str(presentation.Name)
        size = float(presentation.PageSetup.SlideWidth), float(presentation.PageSetup.SlideHeight)
    except (AttributeError, TypeError, pythoncom.com_error) as exc:
        raise RuntimeError("PowerPoint 편집 화면에서 붙여넣을 슬라이드 한 장을 선택하세요.") from exc
    return slide, name, slide_number, size


def _clipboard_state():
    import ctypes
    user32 = ctypes.windll.user32
    user32.GetClipboardOwner.restype = ctypes.c_void_p
    return user32.GetClipboardOwner(), user32.GetClipboardSequenceNumber()


def _paste_graph(slide, graph, app, size, cancel):
    import pythoncom

    before = _clipboard_state()
    if not app.CopyPage(graph, 4):  # Origin CopyPage: OLE Graph Object.
        raise RuntimeError("Origin 그래프를 복사하지 못했습니다.")
    copied = _clipboard_state()
    deadline = time.monotonic() + .75
    while copied == before and time.monotonic() < deadline:
        _check(cancel)
        pythoncom.PumpWaitingMessages()
        time.sleep(.025)
        copied = _clipboard_state()
    if copied == before:
        raise RuntimeError("Origin 그래프가 클립보드에 복사됐는지 확인하지 못했습니다.")
    _check(cancel)
    if _clipboard_state() != copied:
        raise RuntimeError("다른 복사 작업이 감지됐습니다. PPT 붙여넣기를 다시 눌러 주세요.")
    try:
        pasted = slide.Shapes.PasteSpecial(10)  # PowerPoint ppPasteOLEObject.
    except pythoncom.com_error:
        _check(cancel)
        if _clipboard_state() != copied:
            raise RuntimeError("다른 복사 작업이 감지됐습니다. PPT 붙여넣기를 다시 눌러 주세요.")
        # Origin's Copy Page also supports the Office default paste route.
        try:
            pasted = slide.Shapes.Paste()
        except pythoncom.com_error as exc:
            raise RuntimeError("PowerPoint에서 그래프 붙여넣기에 실패했습니다. 편집 가능한 슬라이드를 선택했는지 확인하세요.") from exc
    try:
        shape = pasted.Item(1)
        if pasted.Count != 1 or shape.Type != 7 or "origin" not in str(shape.OLEFormat.ProgID).lower():
            raise RuntimeError("PowerPoint에서 편집 가능한 Origin 개체로 붙여넣지 못했습니다.")
        width, height = size
        dimensions = float(shape.Width), float(shape.Height)
        if min(*dimensions, width, height) > 0:
            scale = min(1., width * .9 / dimensions[0], height * .9 / dimensions[1])
            shape.LockAspectRatio = -1
            if scale < 1:
                shape.Width = dimensions[0] * scale
            shape.Left = (width - shape.Width) / 2
            shape.Top = (height - shape.Height) / 2
    except Exception:
        pasted.Delete()
        raise


def paste_origin_to_powerpoint(curves, name, x_label, y_label, progress, cancel, ranges=(), *, right_label=''):
    if not curves:
        raise ValueError("PPT에 붙여넣을 Cycle 곡선을 선택하세요.")
    _check(cancel)
    progress(0, len(curves), "PowerPoint를 연결하고 있습니다…")
    import pythoncom
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    try:
        slide, deck_name, number, size = _active_slide()
        try:
            progress(0, len(curves), f"{deck_name} · {number}번 슬라이드용 그래프 준비 중")
            with _origin_session() as app:
                try:
                    axis_options = {'right_label': right_label} if right_label else {}
                    graph = _build_graph(app, curves, name, x_label, y_label, progress, cancel, ranges, **axis_options)
                    _check(cancel)
                    progress(len(curves), len(curves), f"{deck_name} · {number}번 슬라이드에 붙여넣는 중")
                    _paste_graph(slide, graph, app, size, cancel)
                finally:
                    del app
        finally:
            del slide
    finally:
        pythoncom.CoUninitialize()
    return f"{deck_name} · {number}번 슬라이드"
