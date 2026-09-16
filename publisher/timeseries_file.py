from __future__ import annotations

import bisect
import csv
import time
from typing import Dict, List, Optional

from .models import MetricValue

SECONDS_PER_DAY = 24 * 60 * 60


class TimeSeriesFile:
    """A single device's timeseries, loaded from a sparse, irregularly-spaced CSV.

    The first column is an integer timestamp (epoch ms, or an arbitrary
    increasing integer - the file player does not care which). The remaining
    columns are metrics whose values are inferred per-cell as bool, int,
    float or string. Empty cells are missing values and are left out of
    whatever row is published (see get_metrics).
    """

    def __init__(self, path: str):
        self.path = path
        self.columns: List[str] = []
        self.timestamps: List[int] = []
        self._rows: List[List[Optional[MetricValue]]] = []
        self._load()

    def __len__(self) -> int:
        return len(self.timestamps)

    @property
    def first_ts(self) -> int:
        return self.timestamps[0]

    @property
    def last_ts(self) -> int:
        return self.timestamps[-1]

    @property
    def span_ms(self) -> int:
        return self.last_ts - self.first_ts

    def _load(self) -> None:
        with open(self.path, "r", newline="") as fin:
            reader = csv.reader(fin)
            header = next(reader, None)
            if not header or len(header) < 2:
                raise ValueError(f"{self.path}: expected a header with a timestamp column and at least one metric column")
            self.columns = [c.strip() for c in header[1:]]
            rows = []
            for line_num, raw in enumerate(reader, start=2):
                if not raw or all(c.strip() == "" for c in raw):
                    continue
                if not raw[0].strip():
                    raise ValueError(f"{self.path}:{line_num}: missing timestamp")
                ts = int(raw[0].strip())
                values: List[Optional[MetricValue]] = [_infer_value(v) for v in raw[1:1 + len(self.columns)]]
                if len(values) < len(self.columns):
                    values.extend([None] * (len(self.columns) - len(values)))
                rows.append((ts, values))
        if not rows:
            raise ValueError(f"{self.path}: no data rows")
        rows.sort(key=lambda r: r[0])
        self.timestamps = [r[0] for r in rows]
        self._rows = [r[1] for r in rows]

    def get_metrics(self, index: int) -> Dict[str, MetricValue]:
        """Metrics for the row at index, omitting any missing (sparse) values."""
        values = self._rows[index]
        return {col: val for col, val in zip(self.columns, values) if val is not None}

    def find_index_for_ts(self, target_ts: float) -> int:
        """Index of the last row whose timestamp is <= target_ts (clamped to the file's range)."""
        idx = bisect.bisect_right(self.timestamps, target_ts) - 1
        return max(0, min(idx, len(self.timestamps) - 1))

    def find_index_nearest_to_now(self, now_epoch_s: Optional[float] = None) -> int:
        """Index of the row whose time-of-day is closest to now's time-of-day.

        This ignores the date entirely, so a file recorded on any day still
        starts playback at a point that matches the current wall-clock time
        of day - e.g. solar data picks up around the right point in the
        daylight curve regardless of what day the file was captured.
        """
        now_epoch_s = now_epoch_s if now_epoch_s is not None else time.time()
        now_sod = _seconds_of_day(now_epoch_s)
        best_idx = 0
        best_diff = None
        for idx, ts in enumerate(self.timestamps):
            row_sod = _seconds_of_day(ts / 1000.0)
            diff = abs(row_sod - now_sod)
            diff = min(diff, SECONDS_PER_DAY - diff)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_idx = idx
        return best_idx


def _seconds_of_day(epoch_s: float) -> int:
    t = time.localtime(epoch_s)
    return t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec


def _infer_value(raw: str) -> Optional[MetricValue]:
    if raw is None:
        return None
    s = raw.strip()
    if s == "":
        return None
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s
