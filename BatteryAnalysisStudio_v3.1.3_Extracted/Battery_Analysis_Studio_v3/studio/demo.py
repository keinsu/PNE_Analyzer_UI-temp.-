"""Clearly labelled synthetic demo fixtures for UI/packaged smoke tests.

The binary fixture uses individual struct.pack_into calls, independent of the
ingestion NumPy record dtype. These are not measurements from a PNE instrument.
"""
from __future__ import annotations

from pathlib import Path
import csv
import io
import json
import math
import struct
import zipfile


def _step(rows, step_no, cycle, mode, times, voltages, current_mA, capacity_mAh,
          temp=25.0):
    """Append a physical step with a zero-capacity start and endpoint summary."""
    if not times:
        return
    energy = 0.0
    previous_q = 0.0
    previous_v = float(voltages[0])
    for i, (time, voltage) in enumerate(zip(times, voltages)):
        fraction = float(time) / float(times[-1]) if times[-1] else 0
        q = capacity_mAh * fraction
        energy += (q - previous_q) * (float(voltage) + previous_v) / 2
        rows.append({"Cycle": cycle, "StepNo": step_no, "StepType": mode,
                     "StepTime_s": float(time), "Voltage_V": float(voltage),
                     "Current_mA": float(current_mA), "Capacity_mAh": q,
                     "Energy_mWh": energy, "Temp_C": temp + .2 * math.sin(i * .13),
                     "CVTime_s": 0.0})
        previous_q, previous_v = q, float(voltage)


def _channel_rows(channel_index):
    rows = []
    scale = 1.0 - channel_index * .05
    for cycle in range(1, 4):
        capacity = 1000 * scale * (1 - .02 * (cycle - 1))
        times = [float(i * 20) for i in range(61)]
        phase = [i / 60 for i in range(61)]
        charge_v = [3.0 + 1.2 * p + .03 * math.sin(4 * math.pi * p) for p in phase]
        discharge_v = [4.2 - 1.2 * p - .03 * math.sin(4 * math.pi * p) for p in phase]
        _step(rows, 1, cycle, "CC Charge", times, charge_v, capacity * 3, capacity, 25 + channel_index)
        _step(rows, 2, cycle, "Rest", [0, 10, 30], [4.2, 4.195, 4.19], 0, 0, 25 + channel_index)
        _step(rows, 3, cycle, "CC Discharge", times, discharge_v, -capacity * 3, capacity, 25 + channel_index)
        _step(rows, 4, cycle, "Rest", [0, 10, 30], [3.0, 3.005, 3.01], 0, 0, 25 + channel_index)

    for block_index, base_voltage in enumerate((3.8, 3.5)):
        cycle = 4 + block_index
        offset = 10 + block_index * 20
        # Setting is longer than pulse cutoff and therefore never a candidate.
        _step(rows, offset, cycle, "CC Charge", [0, 30, 60, 120],
              [base_voltage - .04, base_voltage - .03, base_voltage - .02, base_voltage],
              300, 10, 25 + channel_index)
        _step(rows, offset + 1, cycle, "Rest", [0, 10, 30], [base_voltage] * 3, 0, 0, 25 + channel_index)
        pulse_times = [0, .1, 1, 5, 10, 20, 30]
        resistance_scale = 1 + .1 * channel_index + .2 * block_index
        drops = [0, .006, .010, .015, .020, .028, .035]
        pulse_q = 1000 * 30 / 3600
        _step(rows, offset + 2, cycle, "CC Discharge", pulse_times,
              [base_voltage - d * resistance_scale for d in drops], -1000, pulse_q, 25 + channel_index)
        _step(rows, offset + 3, cycle, "CC Charge", [0, 30, 60, 90, 120, 150],
              [base_voltage - .03, base_voltage - .024, base_voltage - .018,
               base_voltage - .012, base_voltage - .006, base_voltage], 200, pulse_q, 25 + channel_index)
        _step(rows, offset + 4, cycle, "Rest", [0, 10, 30], [base_voltage] * 3, 0, 0, 25 + channel_index)
        _step(rows, offset + 5, cycle, "CC Charge", pulse_times,
              [base_voltage + d * resistance_scale for d in drops], 1000, pulse_q, 25 + channel_index)
        _step(rows, offset + 6, cycle, "CC Discharge", [0, 30, 60, 90, 120, 150],
              [base_voltage + .03, base_voltage + .024, base_voltage + .018,
               base_voltage + .012, base_voltage + .006, base_voltage], -200, pulse_q, 25 + channel_index)
        _step(rows, offset + 7, cycle, "Rest", [0, 10, 30], [base_voltage] * 3, 0, 0, 25 + channel_index)
    return rows


def _cyc_bytes(rows):
    buffer = bytearray(b"SYNTHETIC PNE PROFILE FIXTURE - NOT INSTRUMENT DATA".ljust(72, b"\0"))
    for index, row in enumerate(rows, 1):
        rec = bytearray(176)
        charge = "Discharge" not in row["StepType"] and "Charge" in row["StepType"]
        discharge = "Discharge" in row["StepType"]
        fields = {0: index, 4: row["StepTime_s"], 8: row["CVTime_s"],
                  16: row["Voltage_V"] * 1000, 20: row["Current_mA"],
                  24: row["Capacity_mAh"], 32: row["Capacity_mAh"] if charge else 0,
                  36: row["Capacity_mAh"] if discharge else 0, 40: row["Energy_mWh"],
                  64: row["Temp_C"], 68: 20, 72: row["Voltage_V"] * row["Current_mA"],
                  112: 6, 116: 10, 120: 15, 124: 35, 128: 40, 140: 0}
        for offset, value in fields.items():
            struct.pack_into("<f", rec, offset, float(value))
        buffer.extend(rec)
    return bytes(buffer)


def _stepend_bytes(rows):
    out = io.StringIO(newline="")
    out.write("SYNTHETIC DEMO - 실제 장비 데이터 아님\n")
    writer = csv.writer(out)
    writer.writerow(["StepNo", "StepType", "StateCode", "CurCycle", "TotalCycle",
                     "StepTime", "Voltage", "Current", "Capacity", "WattHour"])
    for i, row in enumerate(rows):
        if i + 1 < len(rows) and (rows[i + 1]["StepNo"], rows[i + 1]["Cycle"]) == (row["StepNo"], row["Cycle"]):
            continue
        writer.writerow([row["StepNo"], row["StepType"], "Synthetic", row["Cycle"], row["Cycle"],
                         row["StepTime_s"], row["Voltage_V"], row["Current_mA"] / 1000,
                         row["Capacity_mAh"] / 1000, row["Energy_mWh"] / 1000])
    return out.getvalue().encode("cp949")


def generate_demo(destination) -> list[str]:
    """Create small synthetic PNE and CSV examples; return their input paths."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    zip_path = destination / "SYNTHETIC_PNE_multichannel.zip"
    csv_path = destination / "SYNTHETIC_full_raw.csv"
    all_channels = [_channel_rows(index) for index in range(2)]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SYNTHETIC_README.txt", "All records are synthetic. They validate software flow only, not any PNE instrument format.")
        for i, rows in enumerate(all_channels, 1):
            member = f"SYNTHETIC_시험/추가루트/M02Ch{i:03d}/demo"
            archive.writestr(member + ".cyc", _cyc_bytes(rows))
            archive.writestr(member + "_StepEnd.csv", _stepend_bytes(rows))
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        stream.write("SYNTHETIC DEMO - 실제 장비 데이터 아님\n")
        writer = csv.DictWriter(stream, fieldnames=list(all_channels[0][0]))
        writer.writeheader()
        writer.writerows(all_channels[0])
    manifest = {"synthetic": True, "instrument_verified": False,
                "description": "2 PNE channels plus CSV copy of channel 1; cycles 1-3 long CC, cycles 4-5 DCR.",
                "pne_channels": 2, "cycles_per_channel": 5, "dcr_blocks_per_channel": 2,
                "pulses_per_channel": 4, "dcir_first_channel_mOhm": {"block1_1s": 10, "block1_10s": 20, "block1_30s": 35},
                "rows_per_channel": [len(rows) for rows in all_channels],
                "setting_step_numbers": [10, 30], "input_paths": [str(zip_path.resolve()), str(csv_path.resolve())]}
    (destination / "SYNTHETIC_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest["input_paths"]
