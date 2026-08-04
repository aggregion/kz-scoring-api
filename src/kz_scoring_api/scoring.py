from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)

_MISSING = float("nan")

_MISSING_TOKENS = frozenset({"", "null", "none", "nan", "na", "n/a"})


def _to_float(value: Any) -> float:
    """Coerce a heterogeneous cell value to float, mapping missing/garbage to NaN.

    LightGBM handles NaN as an explicit missing value at tree splits, so
    unparseable / blank cells propagate as NaN into ``predict`` rather than
    getting silently zeroed.
    """
    if value is None:
        return _MISSING
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        f = float(value)
        return f if not math.isnan(f) else _MISSING
    if isinstance(value, str):
        s = value.strip()
        if s.lower() in _MISSING_TOKENS:
            return _MISSING
        try:
            return float(s)
        except ValueError:
            return _MISSING
    return _MISSING


class SupportsPredict(Protocol):
    def predict(self, data: Any) -> Any: ...
    def feature_name(self) -> list[str]: ...


class BoosterScorer:
    """Wraps a LightGBM booster and scores a single feature-row dict.

    The row dict is projected onto ``booster.feature_name()`` in-order; missing
    keys become NaN. Extra keys in the row are ignored.
    """

    def __init__(self, booster: SupportsPredict, name: str) -> None:
        self._booster = booster
        self._name = name
        self._feature_names: list[str] = list(booster.feature_name())

    @property
    def name(self) -> str:
        return self._name

    @property
    def feature_names(self) -> list[str]:
        return self._feature_names

    def score(self, row: dict[str, Any]) -> float:
        vec = [_to_float(row.get(feat)) for feat in self._feature_names]
        arr = np.asarray([vec], dtype=np.float64)
        preds = self._booster.predict(arr)
        return float(np.asarray(preds).ravel()[0])


class ScoringService:
    """Attaches HH_score / CG_score to each feature-row.

    The two LightGBM boosters are loaded once at process start (see
    :meth:`from_paths`). Scoring is a plain in-place mutation of the row
    dicts so it composes with the existing TSV → JSON pipeline without
    changing the response schema shape.

    Either model can be omitted by leaving its path empty; the corresponding
    ``*_score`` field is then omitted from the response (the service falls
    back to a pass-through).
    """

    HH_KEY = "HH_score"
    CG_KEY = "CG_score"

    def __init__(
        self,
        hh_scorer: BoosterScorer | None = None,
        cg_scorer: BoosterScorer | None = None,
    ) -> None:
        self._hh = hh_scorer
        self._cg = cg_scorer

    @classmethod
    def from_paths(
        cls,
        hh_model_path: str | None,
        cg_model_path: str | None,
    ) -> ScoringService:
        return cls(
            hh_scorer=cls._load(hh_model_path, "hh"),
            cg_scorer=cls._load(cg_model_path, "cg"),
        )

    @staticmethod
    def _load(model_path: str | None, name: str) -> BoosterScorer | None:
        if not model_path:
            logger.info(
                "scoring: %s model path is not configured; %s_score will be omitted",
                name,
                name,
            )
            return None
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"scoring: {name} model file not found at {model_path}"
            )
        import lightgbm as lgb

        booster = lgb.Booster(model_file=str(path))
        scorer = BoosterScorer(booster, name)
        logger.info(
            "scoring: loaded %s model from %s (%d features)",
            name,
            model_path,
            len(scorer.feature_names),
        )
        return scorer

    @property
    def enabled(self) -> bool:
        return self._hh is not None or self._cg is not None

    @property
    def hh(self) -> BoosterScorer | None:
        return self._hh

    @property
    def cg(self) -> BoosterScorer | None:
        return self._cg

    def score_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows or not self.enabled:
            return rows
        for row in rows:
            if self._hh is not None:
                row[self.HH_KEY] = self._hh.score(row)
            if self._cg is not None:
                row[self.CG_KEY] = self._cg.score(row)
        return rows
