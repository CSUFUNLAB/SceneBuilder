from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence


def write_matrix_csv(path: Path, rows: Iterable[Sequence[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(list(row))
