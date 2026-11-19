"""Windows/offline entry point for Battery Analysis Studio."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from studio import APP_VERSION


def smoke_test(app, window, destination):
    import numpy as np
    import pythoncom
    from win32com.client import DispatchEx
    from PySide6.QtCore import qVersion
    from studio.demo import generate_demo
    from studio.ingestion import load_inputs
    from studio.analysis import apply_cycle_baseline, prepare_cycle_tables, derivative_curves, raw_curves
    from studio.project import save_project, load_project
    from studio.studies import Study
    from backend.dcr_analysis import detect_dcr_blocks, compute_dcr_table
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='BAS3_smoke_') as folder:
        paths = generate_demo(folder)
        channels = load_inputs(paths, cache_dir=str(Path(folder) / 'cache'), disk_backed=True)
        window.channels = channels
        window._populate_tree()
        channel = channels[0]
        cycles = channel.cycles
        assert cycles, 'No decoded cycles'
        summaries = {}
        for c in channels:
            folder_cache = Path(folder) / 'cycle_tables'
            tables = prepare_cycle_tables(c, False, folder_cache)
            reloaded = prepare_cycle_tables(c, False, folder_cache)
            assert tables['representative'].equals(reloaded['representative'])
            summaries[c.id] = apply_cycle_baseline(reloaded['representative'], c.name, first_cycle=2)
        window._summaries = summaries
        window.render_summaries()
        assert window.summary.graph.curves[0]['x'][0] == 2
        assert summaries[channel.id].attrs['baseline_cycle'] == 2
        derivatives = derivative_curves(channel, [cycles[0]], direction='both')
        valid = [c for c in derivatives if not c.get('error')]
        assert len(valid) >= 1 and len(valid[0]['x']) == 500
        assert np.isfinite(valid[0]['y']).all()
        window.render_derivatives(derivatives)
        raw = raw_curves(channel, [cycles[0]])
        assert raw, 'No raw curves'
        window.raw.graph.set_curves(raw, group_cycles=True)
        assert window.raw.graph.list.count() == 1
        assert window.raw.graph.origin_btn.isEnabled()
        detected = detect_dcr_blocks(channel.accepted_frame(), pulse_max_duration_s=90)
        dcr = compute_dcr_table(channel.accepted_frame(), detected, [1, 10, 30])
        assert len(dcr['table']) >= 1, 'No synthetic DCR pulses'
        session_path = Path(folder) / 'smoke.basproj'
        save_project(str(session_path), channels, window.ui_state())
        restored = load_project(str(session_path))
        assert len(restored['sources']) == len(channels)
        study = Study.create(Path(folder) / 'studies', 'SYNTHETIC')
        draft, _, changed = study.preview_cycles([(channel, None, 'Cell-01', 101, None)], lambda: False)
        assert changed
        study.commit(draft)
        study = Study(study.directory)
        saved_cell = study.channels()[0]
        assert saved_cell.cycles[0] == 101
        assert prepare_cycle_tables(saved_cell, False, None)['representative'].Cycle.iloc[0] == 101
        assert raw_curves(saved_cell, [101])
        window.show()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            app.processEvents()
            if not window.dcr._jobs:
                break
            time.sleep(.01)
        app.processEvents()
        image = destination.with_suffix('.png')
        if not window.grab().save(str(image)):
            raise RuntimeError('GUI screenshot could not be saved')
        window.dcr.shutdown()
        result = dict(status='ok', version=APP_VERSION, frozen=bool(getattr(sys, 'frozen', False)),
            qt=qVersion(), channels=len(channels), raw_points=channel.row_count,
            disk_backed=all(c.metadata.get('disk_backed') for c in channels),
            summary_cycles=len(summaries[channel.id]), dqdv_points=len(valid[0]['x']),
            dcr_pulses=len(dcr['table']), project_roundtrip=True, screenshot=str(image),
            study_roundtrip=True,
            origin_export_bridge=callable(DispatchEx) and hasattr(pythoncom, 'CoInitializeEx'),
            validation='synthetic data only; actual PNE instrument data not validated')
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        window.close()
        app.processEvents()
        return result


def main():
    parser = argparse.ArgumentParser(description=f'Battery Analysis Studio {APP_VERSION}')
    parser.add_argument('inputs', nargs='*')
    parser.add_argument('--smoke-test', metavar='RESULT_JSON')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    if args.smoke_test:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtCore import QTimer
    from studio.app import StudioWindow
    from ui import theme
    app = QApplication(sys.argv[:1])
    app.setApplicationName('BatteryAnalysisStudio')
    app.setOrganizationName('PNE Research Tools')
    # Windows' offscreen platform has no system font database by default.
    font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'malgun.ttf'
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Malgun Gothic', 10))
    app.setStyleSheet(theme.STYLESHEET + '\nQWidget {font-family:"Malgun Gothic";font-size:12px;}'
        '\nQDockWidget {font-weight:600;} QListWidget::item {padding:8px;}'
        '\nQPushButton {padding:7px 10px;border-radius:5px;} '
        '\nQTreeWidget::item {padding:4px;} QTableView {alternate-background-color:#18242b;}'
        '\nQProgressBar {border:1px solid #29414a;border-radius:3px;text-align:center;}')
    window = StudioWindow()
    if args.smoke_test:
        smoke_test(app, window, args.smoke_test)
        return 0
    window.show()
    paths = list(args.inputs)
    if args.demo:
        from studio.demo import generate_demo
        paths.extend(generate_demo(str(Path(tempfile.gettempdir()) / 'BatteryAnalysisStudio3' / 'SYNTHETIC_DEMO')))
        window.setWindowTitle(f'Battery Analysis Studio {APP_VERSION} — SYNTHETIC DEMO')
    if paths:
        QTimer.singleShot(0, lambda: window.import_paths(paths))
    def unhandled(type_, value, tb):
        import traceback
        details = ''.join(traceback.format_exception(type_, value, tb))
        window.show_error(str(value))
        log = Path(tempfile.gettempdir()) / 'BatteryAnalysisStudio3_error.log'
        log.write_text(details, encoding='utf-8')
        QMessageBox.warning(window, '처리 오류', f'{value}\n\n오류 기록: {log}')
    sys.excepthook = unhandled
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
