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


# LightGBM models (cg_beeline_bezzalog_*, hh_beeline_bezzalog_*) were trained
# with pd-share features named `Num_pd_less_05`, `Share_pd_less_02`,
# `Share_pd_more_30`, etc. — the suffix is `pd × 100` padded to 2 digits
# ("05" = 0.05, "30" = 0.30).
#
# scoring_aggregation (pkb_beeline/scripts/scoring_aggregation/main.py:167-168)
# emits them with a different convention — the suffix is the decimal literal
# with leading zero stripped: 0.05 → "005", 0.30 → "03". The row dict handed
# to the scorer therefore has keys like `cg_Num_pd_less_005`,
# `cg_Share_pd_more_03`, not `Num_pd_less_05`, `Share_pd_more_30`.
#
# Without this remap the scorer silently coerces every one of these features
# to NaN (row.get missing) and LightGBM takes the "missing" tree branch for
# them, biasing CG_pd / HH_pd (confirmed against Damir's recompute on 2026-10-29
# — 10 rows showed systematic diff of a few score points).
_PAYLOAD_SUFFIX_ALIAS: dict[str, str] = {
    'Num_pd_less_05': 'Num_pd_less_005',
    'Share_pd_less_02': 'Share_pd_less_002',
    'Share_pd_less_05': 'Share_pd_less_005',
    'Share_pd_more_30': 'Share_pd_more_03',
    'Share_pd_more_40': 'Share_pd_more_04',
    'Share_pd_more_50': 'Share_pd_more_05',
    'Share_pd_more_60': 'Share_pd_more_06',
}


class BoosterScorer:
    """Wraps a LightGBM booster and scores a single feature-row dict.

    The row dict is projected onto ``booster.feature_name()`` in-order. For
    each feature the scorer tries, in order: ``row[feat]``,
    ``row[payload_prefix + feat]``, ``row[payload_prefix + alias(feat)]``
    (per ``_PAYLOAD_SUFFIX_ALIAS``). Missing keys become NaN; extra keys in
    the row are ignored.
    """

    def __init__(
        self,
        booster: SupportsPredict,
        name: str,
        payload_prefix: str | None = None,
    ) -> None:
        self._booster = booster
        self._name = name
        self._payload_prefix = payload_prefix
        self._feature_names: list[str] = list(booster.feature_name())

    @property
    def name(self) -> str:
        return self._name

    @property
    def feature_names(self) -> list[str]:
        return self._feature_names

    def _resolve(self, row: dict[str, Any], feat: str) -> Any:
        v = row.get(feat)
        if v is not None:
            return v
        if not self._payload_prefix:
            return None
        v = row.get(f"{self._payload_prefix}{feat}")
        if v is not None:
            return v
        alias = _PAYLOAD_SUFFIX_ALIAS.get(feat)
        if alias is not None:
            v = row.get(f"{self._payload_prefix}{alias}")
        return v

    def score(self, row: dict[str, Any]) -> float:
        vec = [_to_float(self._resolve(row, feat)) for feat in self._feature_names]
        arr = np.asarray([vec], dtype=np.float64)
        preds = self._booster.predict(arr)
        return float(np.asarray(preds).ravel()[0])


def pd_to_score(pd: float) -> int | None:
    """Convert a probability of default into a PKB scoring band.

    Formula per PKB: ``int(round(500 + 50 * log2((1 - pd) / pd)))``. Clips
    the input to (eps, 1 - eps) so 0/1 don't blow up log2. Returns None if
    the input is NaN or otherwise unusable.
    """
    if pd is None:
        return None
    try:
        p = float(pd)
    except (TypeError, ValueError):
        return None
    if math.isnan(p):
        return None
    eps = 1e-6
    p = min(max(p, eps), 1.0 - eps)
    return int(round(500 + 50 * math.log2((1.0 - p) / p)))


class ScoringService:
    """Attaches HH_pd/HH_score and CG_pd/CG_score to each feature-row.

    The two LightGBM boosters are loaded once at process start (see
    :meth:`from_paths`). Each booster's ``predict`` returns a probability of
    default (``*_pd``); this is converted into an integer PKB score band
    (``*_score``) via :func:`pd_to_score`. Both fields are written per row.

    Either model can be omitted by leaving its path empty; the corresponding
    pair of fields is then omitted from the response (the service falls back
    to a pass-through for that model).
    """

    HH_PD_KEY = "HH_pd"
    CG_PD_KEY = "CG_pd"
    HH_SCORE_KEY = "HH_score"
    CG_SCORE_KEY = "CG_score"

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
        # Payload prefix the aggregation emits for this model's non-prefixed
        # features (see _PAYLOAD_SUFFIX_ALIAS). hh model → `hh_`, cg → `cg_`.
        scorer = BoosterScorer(booster, name, payload_prefix=f"{name}_")
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
                pd = self._hh.score(row)
                row[self.HH_PD_KEY] = pd
                row[self.HH_SCORE_KEY] = pd_to_score(pd)
            if self._cg is not None:
                pd = self._cg.score(row)
                row[self.CG_PD_KEY] = pd
                row[self.CG_SCORE_KEY] = pd_to_score(pd)
        return rows
