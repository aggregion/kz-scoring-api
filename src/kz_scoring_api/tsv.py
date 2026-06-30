import io
from collections.abc import Iterable
from csv import DictReader
from typing import Any


def parse_tsv(payload: str) -> list[dict[str, Any]]:
    """Parse a TSV payload (header row + N data rows) into a list of dicts.

    Returns [] when payload is empty or contains only a header line.
    Numeric-looking values are returned as-is (string); downstream consumers
    can coerce types if they care.
    """
    if not payload:
        return []
    text = payload.lstrip("﻿")
    reader: Iterable[dict[str, Any]] = DictReader(
        io.StringIO(text), delimiter="\t"
    )
    rows: list[dict[str, Any]] = []
    for row in reader:
        rows.append({k: v for k, v in row.items() if k is not None})
    return rows
