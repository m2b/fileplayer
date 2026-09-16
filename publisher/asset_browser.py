from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterator, List, Optional


@dataclass(frozen=True)
class AssetNode:
    """One node of the asset hierarchy (organization/division/plant/line/device).

    `path` is the '/'-separated hierarchical path from the browser's root,
    e.g. "AcmeCorp/West/PlantA/Line1/Press1". Leaf nodes are devices backed
    by a CSV file; every other node is a plain directory in the hierarchy.
    """

    name: str
    path: str
    is_device: bool
    file_path: Optional[str] = None


class AssetBrowser:
    """Lazily queries a directory tree that models an asset hierarchy.

    The hierarchy is: organization (dir) / division (dir) / plant (dir) /
    line (dir) / device (csv file). The structure may be collapsed at any
    level: as soon as a directory contains CSV files directly, those files
    are treated as devices and browsing stops descending there, regardless
    of how many hierarchy levels remain unused.

    Nothing is scanned until it is asked for - get_children only lists the
    one directory it is given, so browsing a large hierarchy never touches
    parts the caller hasn't asked about.
    """

    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)

    def get_children(self, path: str = "") -> List[AssetNode]:
        dir_path = self._resolve(path)
        try:
            entries = sorted(os.listdir(dir_path))
        except FileNotFoundError:
            return []
        csv_files = [e for e in entries if e.lower().endswith(".csv") and os.path.isfile(os.path.join(dir_path, e))]
        if csv_files:
            return [
                AssetNode(
                    name=os.path.splitext(e)[0],
                    path=_join(path, os.path.splitext(e)[0]),
                    is_device=True,
                    file_path=os.path.join(dir_path, e),
                )
                for e in csv_files
            ]
        subdirs = [e for e in entries if os.path.isdir(os.path.join(dir_path, e))]
        return [AssetNode(name=d, path=_join(path, d), is_device=False) for d in subdirs]

    def walk_devices(self, path: str = "") -> Iterator[AssetNode]:
        """Depth-first, lazy discovery of every device under path (default: the whole tree)."""
        for child in self.get_children(path):
            if child.is_device:
                yield child
            else:
                yield from self.walk_devices(child.path)

    def _resolve(self, path: str) -> str:
        if not path:
            return self.root_dir
        return os.path.join(self.root_dir, *path.split("/"))


def _join(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name
