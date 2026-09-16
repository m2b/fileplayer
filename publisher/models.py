from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Union

# A metric value is always one of these primitive types. Missing (sparse)
# values are never included in a published event - see TimeSeriesFile.get_metrics.
MetricValue = Union[str, bool, int, float]


@dataclass(frozen=True)
class PlaybackEvent:
    """A single row of a device file, ready to be handed to an EventListener."""

    device: str  # hierarchical asset path, e.g. "AcmeCorp/West/PlantA/Line1/Press1"
    metrics: Dict[str, MetricValue]
    timestamp: int  # wall-clock epoch ms when this event was published

    def to_dict(self) -> Dict[str, Any]:
        return {"device": self.device, "metrics": self.metrics, "timestamp": self.timestamp}
