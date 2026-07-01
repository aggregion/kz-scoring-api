"""Parse the JSON payload produced by the lookup pipeline's
`publish_to_session` task.

Contract (wire format, in sync with
`kz-scoring/pipelines/pkb_beeline/scripts/decrypt/main.py`):

    resultJson: str  # a JSON array of row objects (may be `[]` for
                     # not-found; each object's keys are the plaintext
                     # feature columns, values are strings — the same
                     # shape parse_tsv returned before v0.3).

Empty payload (`""` / `None`) means the pipeline produced no result at
all (usually a not-found path where publish never ran, or a run status
without resultJson) — we treat it the same as `[]`.

`_maybe_double_decode` guards against DDM's `publish_to_session` /
vaultee-pipelines' `resultJson` inadvertently sending the array wrapped
inside a JSON-encoded string (i.e. `"\"[...]\""` rather than `"[...]"`).
DDM's `Extract single_value=true` on a Utf8 cell serializes the cell
content as a JSON string, so depending on how vaultee-pipelines relays
the DDM session's result_json field, either level of decoding may
already have been applied for us. Being defensive on both is cheaper
than pinning the interop precisely — a mis-shape here manifests as a
500 to the caller, not silent data corruption.
"""

from __future__ import annotations

import json
from typing import Any


def parse_pipeline_result(payload: str) -> list[dict[str, Any]]:
    """Parse the pipeline `resultJson` payload into a list of feature-row dicts.

    Empty / whitespace payload → `[]`. A JSON `null` → `[]`. Anything else
    is expected to be a JSON array; a single object is auto-wrapped for
    forward-compat with a possible future pipeline that emits one object
    instead of a one-element array.
    """
    if not payload:
        return []
    text = payload.strip().lstrip("﻿")
    if not text:
        return []

    parsed = _maybe_double_decode(text)

    if parsed is None:
        return []
    if isinstance(parsed, list):
        return [_ensure_row(item) for item in parsed]
    if isinstance(parsed, dict):
        return [_ensure_row(parsed)]
    raise ValueError(
        f"pipeline resultJson has unexpected top-level type "
        f"{type(parsed).__name__}: {text[:200]}"
    )


def _maybe_double_decode(text: str) -> Any:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"pipeline resultJson is not valid JSON: {text[:200]}"
        ) from exc
    if isinstance(parsed, str):
        # DDM's Extract on a Utf8 cell → serde_json Value::String → the
        # outer JSON encodes the payload as a quoted string. Decode one
        # more layer to recover the intended array/object/null.
        try:
            return json.loads(parsed)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "pipeline resultJson decoded to a string that is not JSON: "
                f"{parsed[:200]}"
            ) from exc
    return parsed


def _ensure_row(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(
            f"pipeline resultJson row is not an object: {item!r:.200}"
        )
    return item
