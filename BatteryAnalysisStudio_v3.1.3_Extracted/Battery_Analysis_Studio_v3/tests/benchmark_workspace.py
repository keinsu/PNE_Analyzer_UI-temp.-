"""Repeatable, modest synthetic performance benchmark; no instrument-data claim.

Run from the project root:
  python -B tests/benchmark_workspace.py --run-tests
The recorded numbers describe one local run, not guaranteed performance targets.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import psutil
from PySide6.QtCore import QPointF, QTimer, qVersion
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from studio.analysis import cycle_summary, derivative_curves
from studio.app import StudioWindow
from studio.ingestion import load_inputs


def synthetic_zip(path: Path, channels: int, cycles: int, points: int) -> dict:
    """Construct byte-layout data independently of ingestion's dtype/offset map."""
    source_bytes = 0
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for channel in range(channels):
            steps = cycles * 2
            n = steps * points
            data = np.zeros((n, 44), dtype="<f4")  # 44 × 4B = 176B
            data[:, 0] = np.arange(1, n + 1)
            summaries = []
            for cycle in range(1, cycles + 1):
                fade = 1. - min(cycle-1, 500) * .0007
                current = 1000. + channel * 5
                duration = 3600. * fade
                capacity = np.linspace(0., current * fade, points)
                energy = capacity * 3.6
                for discharge in (False, True):
                    position = ((cycle-1)*2 + int(discharge))*points
                    rows = data[position:position+points]
                    voltage = np.linspace(4200., 3000., points) if discharge else np.linspace(3000., 4200., points)
                    signed_current = -current if discharge else current
                    rows[:, 1] = np.linspace(0., duration, points)  # StepTime at 4
                    rows[:, 4] = voltage                         # Voltage at 16
                    rows[:, 5] = signed_current                  # Current at 20
                    rows[:, 6] = capacity                        # Capacity at 24
                    rows[:, 8 if not discharge else 9] = capacity
                    rows[:, 10] = energy                        # Energy at 40
                    rows[:, 16] = 25. + cycle*.01                 # Temperature at 64
                    rows[:, 17] = 10. + cycle*.02                 # 10s impedance at 68
                    rows[:, 18] = voltage * signed_current / 1000.
                    step = 3 if discharge else 1
                    summaries.append(f"{step},{'방전' if discharge else '충전'},0,{cycle},0,{duration:.8f},{voltage[-1]/1000:.6f},{signed_current/1000:.6f},{capacity[-1]/1000:.8f},{energy[-1]/1000:.8f}")
            cyc = bytes(72) + data.tobytes()
            csv = ("SYNTHETIC BENCHMARK ONLY\nStepNo,StepType,StateCode,CurCycle,TotalCycle,StepTime,Voltage,Current,Capacity,WattHour\n" + "\n".join(summaries)).encode("cp949")
            archive.writestr(f"SYNTHETIC/M02Ch{channel+1:03d}/data.cyc", cyc)
            archive.writestr(f"SYNTHETIC/M02Ch{channel+1:03d}/data_StepEnd.csv", csv)
            source_bytes += len(cyc)+len(csv)
    return dict(channels=channels, cycles_per_channel=cycles, points_per_direction=points,
                total_records=channels*cycles*2*points, uncompressed_bytes=source_bytes,
                zip_bytes=path.stat().st_size)


def timed(function):
    start = time.perf_counter()
    result = function()
    return result, time.perf_counter()-start


def memory():
    info = psutil.Process().memory_info()
    return dict(rss_mib=round(info.rss/1024**2, 2),
                peak_working_set_mib=round(getattr(info, "peak_wset", info.rss)/1024**2, 2))


def qt_job(app, window, starter, timeout=90.):
    """Measure the real threaded application flow and timer callback gaps."""
    beats = [time.perf_counter()]
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start()
    start = time.perf_counter()
    starter()
    while window._jobs or window._threads:
        app.processEvents()
        if time.perf_counter()-start > timeout:
            window.cancel_jobs()
            raise TimeoutError("Asynchronous workspace benchmark timed out")
        time.sleep(.002)
    app.processEvents()
    beats.append(time.perf_counter())
    timer.stop()
    intervals = np.diff(beats)*1000
    return dict(elapsed_s=time.perf_counter()-start, timer_interval_ms=20,
                timer_samples=len(intervals), max_event_loop_gap_ms=float(np.max(intervals)),
                p95_event_loop_gap_ms=float(np.percentile(intervals, 95)))


def hover_times(app, graph):
    results = {}
    for selected, name in ((0, "selected_curve"), (-1, "nearest_of_all_curves")):
        graph.highlight(selected)
        samples = []
        curve = graph.curves[0]
        xx, yy = np.asarray(curve["x"]), np.asarray(curve["y"])
        low, high = float(np.nanmin(xx)), float(np.nanmax(xx))
        for x in np.linspace(low+.1*(high-low), high-.1*(high-low), 30):
            # Put the cursor inside the actual plotted range; an outside point
            # would time only the early-return guard rather than point lookup.
            y = float(np.interp(x, xx, yy))
            scene = graph.plot.getViewBox().mapViewToScene(QPointF(float(x), y))
            start = time.perf_counter()
            graph._hover((scene,))
            samples.append((time.perf_counter()-start)*1000)
        app.processEvents()
        assert "계산점" in graph.readout.text(), "Hover benchmark did not query plotted points"
        results[name] = dict(samples=len(samples), p50_ms=float(np.median(samples)),
                             p95_ms=float(np.percentile(samples, 95)), max_ms=max(samples))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT/"validation"/"performance.json"))
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--points", type=int, default=300)
    parser.add_argument("--multi-channels", type=int, default=20)
    parser.add_argument("--multi-cycles", type=int, default=10)
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.cycles <= 500 or not 50 <= args.points <= 2000 or not 1 <= args.multi_channels <= 50 or not 1 <= args.multi_cycles <= 100:
        raise ValueError("Benchmark dimensions exceed modest synthetic bounds")
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),
                  validation="synthetic data only; not actual PNE instrument data",
                  environment=dict(os=platform.platform(), machine=platform.machine(), cpu=platform.processor(),
                                   logical_cpus=os.cpu_count(), total_memory_gib=round(psutil.virtual_memory().total/1024**3, 2),
                                   python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__,
                                   qt=qVersion(), platform_plugin=os.environ.get("QT_QPA_PLATFORM")),
                  measurements={}, memory={"baseline": memory()}, limitations=[
                      "One local warm-machine run; values are not service guarantees.",
                      "Fixture follows experimental 72B header/176B record profile; no instrument-format certification.",
                      "ZIP import currently materializes each selected channel's Raw frame; large real archives require profiling.",
                      "This performance benchmark does not time the Go reference engine; decoder equivalence is a separate test scope.",
                      "Offscreen paint/event-loop timings do not replace interactive company-PC testing.",
                      "Library import/application startup time excluded from timings except separate EXE smoke tests."])
    if args.run_tests:
        result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        log = output.parent/"unit_tests.log"
        log.write_text(result.stdout+result.stderr, encoding="utf-8")
        import re
        match = re.search(r"Ran (\d+) tests? in ([\d.]+)s", result.stdout+result.stderr)
        report["unit_tests"] = dict(status="passed" if result.returncode == 0 else "failed",
                                    count=int(match.group(1)) if match else None,
                                    duration_s=float(match.group(2)) if match else None, log=str(log))
        if result.returncode:
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError("Functional tests failed; see unit_tests.log")
    app = QApplication.instance() or QApplication([])
    font = Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"/"malgun.ttf"
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Malgun Gothic", 10))
    window = StudioWindow()
    window.resize(1366, 768)
    window.show()
    app.processEvents()
    try:
        with tempfile.TemporaryDirectory(prefix="BAS3_benchmark_") as tmp:
            folder = Path(tmp)
            primary, multi = folder/"100cycles_SYNTHETIC.zip", folder/"20channels_SYNTHETIC.zip"
            report["datasets"] = dict(primary=synthetic_zip(primary, 1, args.cycles, args.points),
                                      multichannel=synthetic_zip(multi, args.multi_channels, args.multi_cycles, args.points))
            cache = str(folder/"cache")
            channels, elapsed = timed(lambda: load_inputs([str(primary)], cache_dir=cache))
            report["measurements"]["primary_first_import_s"] = elapsed
            cached, elapsed = timed(lambda: load_inputs([str(primary)], cache_dir=cache))
            assert cached[0].metadata.get("cache_hit"), "Cache reopening did not use cache"
            report["measurements"]["primary_cached_import_s"] = elapsed
            assert len(channels[0].frame) == args.cycles*2*args.points
            assert set(channels[0].frame.MatchStatus) == {"MATCHED"}
            report["memory"]["after_primary_import"] = memory()
            summary, elapsed = timed(lambda: cycle_summary(channels[0]))
            assert len(summary) == args.cycles
            report["measurements"]["cycle_summary_s"] = elapsed
            curves, elapsed = timed(lambda: derivative_curves(channels[0], list(range(1, args.cycles+1)), direction="discharge"))
            assert len(curves) == args.cycles and all(not curve.get("error") for curve in curves)
            report["measurements"]["derivative_curves_s"] = elapsed
            report["derivative_output"] = dict(curves=len(curves), points_per_curve=len(curves[0]["x"]),
                                               total_points=sum(len(c["x"]) for c in curves))
            report["memory"]["after_derivatives"] = memory()
            report["measurements"]["actual_async_import_and_summary"] = qt_job(app, window, lambda: window.import_paths([str(primary)]))
            assert len(window.summary.graph.curves) == 1
            window._restoring = True
            window.nav.setCurrentRow(3)
            window.dq_direction.setCurrentIndex(window.dq_direction.findData("discharge"))
            window.dq_cycles.setText(f"1-{args.cycles}")
            window._restoring = False
            window._selection_timer.stop()
            report["measurements"]["actual_async_derivative_and_render"] = qt_job(app, window, window.calculate_derivatives)
            assert len(window.dq_plot.curves) == args.cycles
            start = time.perf_counter()
            image = window.grab()
            report["measurements"]["window_capture_after_render_s"] = time.perf_counter()-start
            screenshot = output.with_name("performance_workspace.png")
            if not image.save(str(screenshot)):
                raise RuntimeError("Could not save benchmark workspace capture")
            report["workspace"] = dict(width=window.width(), height=window.height(), screenshot=str(screenshot))
            report["measurements"]["hover"] = hover_times(app, window.dq_plot)
            report["memory"]["after_100_curve_render"] = memory()
            others, elapsed = timed(lambda: load_inputs([str(multi)]))
            assert len(others) == args.multi_channels
            report["measurements"]["multichannel_import_s"] = elapsed
            report["multichannel_output"] = dict(channels=len(others), total_records=sum(len(c.frame) for c in others),
                                                  dataframe_memory_mib=round(sum(c.frame.memory_usage(deep=True).sum() for c in others)/1024**2, 2))
            report["memory"]["after_multichannel_import"] = memory()
            report["status"] = "passed"
    except Exception as exc:
        report["status"], report["error"] = "failed", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window._selection_timer.stop()
        window.cancel_jobs()
        window.dcr.shutdown()
        window.close()
        app.processEvents()
    print(json.dumps(dict(status=report["status"], report=str(output), unit_tests=report.get("unit_tests"),
                          measurements=report["measurements"], memory=report["memory"]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
