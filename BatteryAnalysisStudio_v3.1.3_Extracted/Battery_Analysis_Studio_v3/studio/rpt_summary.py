"""Create one overview row per imported RPT channel."""
from collections import defaultdict
from pathlib import Path

import pandas as pd


METRICS = {
    "RPT 충전용량": "충전 용량 (mAh)", "RPT 충전에너지": "충전 에너지 (mWh)",
    "RPT 방전용량": "방전 용량 (mAh)", "RPT 방전에너지": "방전 에너지 (mWh)",
}


def rpt_summary(channels, rows_by_channel, cell_labels):
    records, soc_columns = [], set()
    for channel, rows in zip(channels, rows_by_channel):
        link = channel.metadata.get("rpt_link", {})
        result = {"RPT 채널": channel.name, "파일": Path(channel.source_path).name,
                  "연결 셀 / 관리 ID": cell_labels.get(link.get("cell_id"), ""),
                  "측정 Cycle": link.get("cycle")}
        for item, label in METRICS.items():
            matches = [r["Value"] for r in rows if r.get("Metric") == item]
            if not matches:
                matches = [r["Value"] for r in rows if r.get("Item") == item]
            result[label] = matches[0] if len(matches) == 1 else None
        pulses = defaultdict(list)
        unassigned = False
        for row in rows:
            if row.get("Item") != "방전 10초 DCIR":
                continue
            soc = row.get("SOC_pct")
            if soc is None:
                unassigned = unassigned or row.get("Status") == "SOC 개수 불일치"
            else:
                pulses[soc].append(row.get("Value"))
        for soc, values in pulses.items():
            for index, value in enumerate(values, 1):
                soc_columns.add((soc, index))
                label = f"SOC {soc:g}% (mΩ)" if index == 1 else f"SOC {soc:g}% · {index}회 (mΩ)"
                result[label] = value
        result["DCIR 상태"] = "반복 횟수와 SOC 수 불일치" if unassigned else ""
        records.append(result)
    base = ["RPT 채널", "파일", "연결 셀 / 관리 ID", "측정 Cycle", *METRICS.values()]
    base.extend(f"SOC {soc:g}% (mΩ)" if index == 1 else f"SOC {soc:g}% · {index}회 (mΩ)"
                for soc, index in sorted(soc_columns, key=lambda key: (-key[0], key[1])))
    return pd.DataFrame(records, columns=[*base, "DCIR 상태"])
