from __future__ import annotations

import csv
from typing import Dict


def load_speed_overrides(path: str) -> Dict[str, float]:
    """Loads per-device speed overrides from a CSV with a 'device,speed' header.

    'device' must match a hierarchical device path as produced by
    AssetBrowser (e.g. "AcmeCorp/West/PlantA/Line1/Press1"). Devices not
    listed here play at the file player's default speed.
    """
    overrides: Dict[str, float] = {}
    with open(path, "r", newline="") as fin:
        reader = csv.DictReader(fin)
        if reader.fieldnames is None or "device" not in reader.fieldnames or "speed" not in reader.fieldnames:
            raise ValueError(f"{path}: expected a header with 'device' and 'speed' columns")
        for row in reader:
            device = (row.get("device") or "").strip()
            if not device:
                continue
            overrides[device] = float(row["speed"])
    return overrides
