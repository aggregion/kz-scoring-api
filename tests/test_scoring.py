import math
from typing import Any

import numpy as np
import pytest

from kz_scoring_api.scoring import (
    BoosterScorer,
    ScoringService,
    _to_float,
    pd_to_score,
)


class FakeBooster:
    """Deterministic stand-in for ``lightgbm.Booster``.

    Its prediction is ``sum(non-nan features)`` — enough to distinguish rows,
    react to missing values (NaN), and confirm feature-order projection.
    """

    def __init__(self, feature_names: list[str]) -> None:
        self._feature_names = list(feature_names)
        self.calls: list[np.ndarray] = []

    def feature_name(self) -> list[str]:
        return list(self._feature_names)

    def predict(self, data: Any) -> np.ndarray:
        arr = np.asarray(data, dtype=np.float64)
        self.calls.append(arr)
        # sum of non-nan features per row
        nan_mask = np.isnan(arr)
        summed = np.where(nan_mask, 0.0, arr).sum(axis=1)
        return summed


class TestToFloat:
    def test_int_and_float_pass_through(self):
        assert _to_float(3) == 3.0
        assert _to_float(3.14) == 3.14

    def test_none_and_blank_are_nan(self):
        assert math.isnan(_to_float(None))
        assert math.isnan(_to_float(""))
        assert math.isnan(_to_float("   "))

    def test_missing_tokens_are_nan(self):
        for tok in ("null", "NULL", "None", "nan", "NA", "n/a"):
            assert math.isnan(_to_float(tok)), tok

    def test_numeric_strings_parsed(self):
        assert _to_float("1.5") == 1.5
        assert _to_float(" -2 ") == -2.0
        assert _to_float("1e3") == 1000.0

    def test_unparseable_string_is_nan(self):
        assert math.isnan(_to_float("abc"))
        assert math.isnan(_to_float("1.2.3"))

    def test_bool_is_zero_or_one(self):
        assert _to_float(True) == 1.0
        assert _to_float(False) == 0.0


class TestBoosterScorer:
    def test_projects_row_in_feature_order(self):
        booster = FakeBooster(["a", "b", "c"])
        scorer = BoosterScorer(booster, "test")
        result = scorer.score({"b": "2", "a": 1, "c": 3.0, "extra": 99})
        assert result == 6.0
        assert booster.calls[0].tolist() == [[1.0, 2.0, 3.0]]

    def test_missing_row_key_becomes_nan(self):
        booster = FakeBooster(["a", "b"])
        scorer = BoosterScorer(booster, "test")
        result = scorer.score({"a": 5})
        assert result == 5.0
        row = booster.calls[0][0]
        assert row[0] == 5.0
        assert math.isnan(row[1])

    def test_feature_names_exposed(self):
        booster = FakeBooster(["x", "y"])
        scorer = BoosterScorer(booster, "hh")
        assert scorer.feature_names == ["x", "y"]
        assert scorer.name == "hh"

    def test_payload_prefix_resolves_prefixed_key(self):
        # Model expects `MAX`; payload emits `cg_MAX`. Prefix fallback must hit.
        booster = FakeBooster(["MAX", "fhc_1"])
        scorer = BoosterScorer(booster, "cg", payload_prefix="cg_")
        result = scorer.score({"cg_MAX": "7", "fhc_1": 3})
        assert result == 10.0

    def test_payload_prefix_resolves_suffix_alias(self):
        # Model expects `Share_pd_more_30` (= 0.30); payload emits
        # `cg_Share_pd_more_03` with suffix 03 (= 0.30 under the aggregation
        # script's "strip leading zero" convention).
        booster = FakeBooster(["Share_pd_more_30", "Num_pd_less_05", "Avg_score"])
        scorer = BoosterScorer(booster, "cg", payload_prefix="cg_")
        result = scorer.score({
            "cg_Share_pd_more_03": 0.14,
            "cg_Num_pd_less_005": 132,
            "cg_Avg_score": 0.19,
        })
        assert result == pytest.approx(0.14 + 132 + 0.19)

    def test_payload_prefix_direct_key_preferred_over_alias(self):
        # If both bare and prefixed keys exist, bare wins (matches current
        # row.get(feat) semantic for backward compat).
        booster = FakeBooster(["MAX"])
        scorer = BoosterScorer(booster, "cg", payload_prefix="cg_")
        result = scorer.score({"MAX": 1, "cg_MAX": 99})
        assert result == 1.0


class TestPdToScore:
    def test_pd_half_is_500(self):
        assert pd_to_score(0.5) == 500

    def test_pd_low_gives_high_score(self):
        # pd = 0.1 → log2(9) ≈ 3.1699 → 500 + 158.5 → 659
        assert pd_to_score(0.1) == 658

    def test_pd_high_gives_low_score(self):
        # pd = 0.9 → log2(1/9) ≈ -3.1699 → 500 - 158.5 → 341
        assert pd_to_score(0.9) == 342

    def test_pd_zero_clipped_not_inf(self):
        s = pd_to_score(0.0)
        assert isinstance(s, int)
        assert s >= 1000

    def test_pd_one_clipped_not_neg_inf(self):
        s = pd_to_score(1.0)
        assert isinstance(s, int)
        assert s <= 0

    def test_nan_returns_none(self):
        assert pd_to_score(float("nan")) is None

    def test_none_returns_none(self):
        assert pd_to_score(None) is None


class TestScoringService:
    def test_score_rows_adds_pd_and_score(self):
        # FakeBooster returns sum(non-nan features), clamped to (eps, 1-eps) inside pd_to_score
        hh = BoosterScorer(FakeBooster(["f1", "f2"]), "hh")
        cg = BoosterScorer(FakeBooster(["f3"]), "cg")
        svc = ScoringService(hh_scorer=hh, cg_scorer=cg)
        rows = [{"f1": "0.1", "f2": "0.4", "f3": "0.9"}]
        svc.score_rows(rows)
        assert rows[0]["HH_pd"] == pytest.approx(0.5)
        assert rows[0]["HH_score"] == 500
        assert rows[0]["CG_pd"] == pytest.approx(0.9)
        assert rows[0]["CG_score"] == 342

    def test_pass_through_when_disabled(self):
        svc = ScoringService()
        assert not svc.enabled
        rows = [{"a": "1"}]
        out = svc.score_rows(rows)
        assert out is rows
        assert "HH_pd" not in rows[0]
        assert "HH_score" not in rows[0]
        assert "CG_pd" not in rows[0]
        assert "CG_score" not in rows[0]

    def test_partial_enabled_only_hh(self):
        hh = BoosterScorer(FakeBooster(["a"]), "hh")
        svc = ScoringService(hh_scorer=hh, cg_scorer=None)
        rows = [{"a": "0.5"}]
        svc.score_rows(rows)
        assert rows[0]["HH_pd"] == pytest.approx(0.5)
        assert rows[0]["HH_score"] == 500
        assert "CG_pd" not in rows[0]
        assert "CG_score" not in rows[0]

    def test_empty_rows_short_circuits(self):
        hh = BoosterScorer(FakeBooster(["a"]), "hh")
        svc = ScoringService(hh_scorer=hh)
        assert svc.score_rows([]) == []

    def test_missing_features_produce_nan_input(self):
        hh = BoosterScorer(FakeBooster(["a", "b", "c"]), "hh")
        svc = ScoringService(hh_scorer=hh)
        rows = [{"a": "0.3"}]
        svc.score_rows(rows)
        # FakeBooster sums non-nan; 'b' and 'c' missing → pd = 0.3
        assert rows[0]["HH_pd"] == pytest.approx(0.3)

    def test_from_paths_empty_disables(self, tmp_path):
        svc = ScoringService.from_paths(hh_model_path="", cg_model_path="")
        assert not svc.enabled
        assert svc.hh is None
        assert svc.cg is None

    def test_from_paths_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ScoringService.from_paths(
                hh_model_path=str(tmp_path / "does-not-exist.txt"),
                cg_model_path="",
            )


class TestRealLightgbm:
    """Round-trip against a real LightGBM booster: train tiny, dump, reload, score."""

    @pytest.fixture
    def booster_file(self, tmp_path):
        import lightgbm as lgb

        rng = np.random.default_rng(seed=42)
        X = rng.normal(size=(100, 3))
        y = (X[:, 0] + 0.5 * X[:, 1] > 0).astype(int)
        train = lgb.Dataset(X, label=y, feature_name=["alpha", "beta", "gamma"])
        booster = lgb.train(
            {
                "objective": "binary",
                "metric": "binary_logloss",
                "verbosity": -1,
                "num_leaves": 4,
            },
            train,
            num_boost_round=3,
        )
        path = tmp_path / "model.txt"
        booster.save_model(str(path))
        return path

    def test_loads_and_scores(self, booster_file):
        svc = ScoringService.from_paths(
            hh_model_path=str(booster_file),
            cg_model_path="",
        )
        assert svc.enabled
        assert svc.hh is not None
        assert set(svc.hh.feature_names) == {"alpha", "beta", "gamma"}

        rows = [{"alpha": "1.0", "beta": "0.5", "gamma": "0.1"}]
        svc.score_rows(rows)
        assert "HH_pd" in rows[0]
        pd = rows[0]["HH_pd"]
        assert isinstance(pd, float)
        assert 0.0 <= pd <= 1.0
        assert "HH_score" in rows[0]
        assert isinstance(rows[0]["HH_score"], int)
