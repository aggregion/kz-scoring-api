import pytest
from pydantic import ValidationError

from kz_scoring_api.config import Settings


def _base_kwargs(**overrides):
    kwargs = dict(
        vaultee_pipelines_url="http://pipelines.example/graphql",
        vaultee_secrets_url="http://secrets.example",
        pipelines_service_subject="kz-scoring-service",
        pipelines_tenant_id="00000000-0000-0000-0000-000000000001",
        lookup_iin_only_template_id=11,
        lookup_iin_phone_template_id=22,
        pipeline_executor_id=1,
        iin_salt="testsalt",
        salt_pkb_secret_token="pkb_beeline/SALT_PKB",
        timeout_seconds=2.0,
        poll_interval_ms=10,
        max_concurrent_lookups=4,
        salt_cache_ttl_seconds=60.0,
    )
    kwargs.update(overrides)
    return kwargs


def test_settings_accepts_valid_tenant_id():
    s = Settings(**_base_kwargs())
    assert s.pipelines_tenant_id == "00000000-0000-0000-0000-000000000001"
    assert s.pipelines_service_subject == "kz-scoring-service"


def test_settings_rejects_empty_tenant_id(monkeypatch):
    monkeypatch.delenv("KZ_SCORING_PIPELINES_TENANT_ID", raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_base_kwargs(pipelines_tenant_id=""))
    assert "pipelines_tenant_id" in str(excinfo.value)


def test_settings_rejects_whitespace_tenant_id(monkeypatch):
    monkeypatch.delenv("KZ_SCORING_PIPELINES_TENANT_ID", raising=False)
    with pytest.raises(ValidationError):
        Settings(**_base_kwargs(pipelines_tenant_id="   "))


def test_settings_default_tenant_id_is_rejected(monkeypatch):
    """The default '' is intentionally invalid — a chart deployed without
    KZ_SCORING_PIPELINES_TENANT_ID should fail fast at startup, not on the
    first upstream call (35s timeout, per AGG-100 postmortem)."""
    monkeypatch.delenv("KZ_SCORING_PIPELINES_TENANT_ID", raising=False)
    kwargs = _base_kwargs()
    del kwargs["pipelines_tenant_id"]
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_settings_reads_tenant_id_from_env(monkeypatch):
    monkeypatch.setenv(
        "KZ_SCORING_PIPELINES_TENANT_ID",
        "e4df8d0e-970a-4e83-8d65-787aab057969",
    )
    monkeypatch.setenv("KZ_SCORING_PIPELINES_SERVICE_SUBJECT", "beeline-svc")
    kwargs = _base_kwargs()
    del kwargs["pipelines_tenant_id"]
    del kwargs["pipelines_service_subject"]
    s = Settings(**kwargs)
    assert s.pipelines_tenant_id == "e4df8d0e-970a-4e83-8d65-787aab057969"
    assert s.pipelines_service_subject == "beeline-svc"
