"""Regression: the jinja context key for the initiator's vaultee-secrets URL
must be configurable so one image serves both the BLN-side lookup templates
(`beeline_secrets_url`) and the PKB-side ones (`fcb_secrets_url`).
"""
import pytest

from kz_scoring_api.hashing import compute_row_id_iin


@pytest.mark.asyncio
async def test_default_key_is_beeline_secrets_url(
    settings, fake_pipelines, fake_secrets, lookup_service
):
    # Default matches AGG-117 / production BLN deploy — unchanged.
    salt = fake_secrets.salt_bytes
    row_id = compute_row_id_iin(salt, "801217301434", settings.iin_salt)
    fake_pipelines.set(row_id, "col\nval\n")

    await lookup_service.lookup("801217301434", None)

    ctx = fake_pipelines.created[0]["context"]
    assert "beeline_secrets_url" in ctx
    assert ctx["beeline_secrets_url"] == settings.beeline_secrets_url_for_pipeline
    assert "fcb_secrets_url" not in ctx


@pytest.mark.asyncio
async def test_pkb_side_key_is_fcb_secrets_url(
    settings, fake_pipelines, fake_secrets
):
    # PKB-side deploy overrides pipeline_secrets_context_key so the symmetric
    # lookup_by_pkb templates find their `fcb_secrets_url` variable.
    from kz_scoring_api.lookup import LookupService

    pkb_settings = settings.model_copy(
        update={
            "pipeline_secrets_context_key": "fcb_secrets_url",
            "beeline_secrets_url_for_pipeline": "http://vlt-system-prod-vaultee-secrets",
        }
    )
    lookup = LookupService(pkb_settings, fake_pipelines, fake_secrets)

    salt = fake_secrets.salt_bytes
    row_id = compute_row_id_iin(salt, "801217301434", pkb_settings.iin_salt)
    fake_pipelines.set(row_id, "col\nval\n")

    await lookup.lookup("801217301434", None)

    ctx = fake_pipelines.created[0]["context"]
    assert "fcb_secrets_url" in ctx
    assert ctx["fcb_secrets_url"] == pkb_settings.beeline_secrets_url_for_pipeline
    assert "beeline_secrets_url" not in ctx
