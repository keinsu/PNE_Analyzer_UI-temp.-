"""Shared, UI-independent channel data contract.

Source measurements are retained, including unmatched and initialization rows.
Quantitative analyses choose their accepted MatchStatus explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
import pandas as pd
from .raw_store import RawStore, cycle_ranges


@dataclass
class ChannelData:
    id: str
    name: str
    source_path: str
    frame: pd.DataFrame | RawStore
    steps: pd.DataFrame = field(default_factory=pd.DataFrame)
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return str(self.metadata.get("parse_fingerprint", self.metadata.get("sha256", self.id)))

    @cached_property
    def cycle_ranges(self) -> dict:
        if not isinstance(self.frame, pd.DataFrame):
            return self.frame.ranges
        if self.frame.empty or "Cycle" not in self.frame:
            return {}
        return cycle_ranges(self.frame["Cycle"].to_numpy())

    @property
    def row_count(self):
        return len(self.frame) if isinstance(self.frame, pd.DataFrame) else self.frame.size

    def read_rows(self, parts=None, columns=None):
        if not isinstance(self.frame, pd.DataFrame):
            return self.frame.read(parts, columns)
        frame = self.frame if columns is None else self.frame.loc[:, self.frame.columns.intersection(columns)]
        if parts is None:
            return frame
        if len(parts) == 1:
            return frame.iloc[parts[0]]
        return pd.concat([frame.iloc[part] for part in parts]) if parts else frame.iloc[:0]

    @property
    def cycles(self) -> list[int]:
        return sorted(cycle for cycle in self.cycle_ranges if cycle >= 0)

    def accepted_frame(self, include_review: bool = False) -> pd.DataFrame:
        accepted = {"MATCHED", "CSV"}
        if include_review:
            accepted.add("REVIEW")
        frame = self.read_rows()
        return frame.loc[frame["MatchStatus"].isin(accepted)].copy()
