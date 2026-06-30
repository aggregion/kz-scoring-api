import pytest

from kz_scoring_api.hashing import compute_row_id_full, compute_row_id_iin
from kz_scoring_api.pipeline_client import (
    PipelineTimeoutError,
    PipelineUnavailableError,
)


@pytest.mark.asyncio
async def test_lookup_iin_only_returns_parsed_rows(
    settings, fake_pipelines, fake_secrets, lookup_service
):
    salt_pkb = fake_secrets.value
    row_id = compute_row_id_iin(salt_pkb, "801217301434", settings.iin_salt)
    fake_pipelines.set(
        row_id,
        "col_a\tcol_b\n1\t2\n3\t4\n",
    )

    result = await lookup_service.lookup("801217301434", None)

    assert result == [
        {"col_a": "1", "col_b": "2"},
        {"col_a": "3", "col_b": "4"},
    ]
    assert fake_pipelines.created[0]["template_id"] == settings.lookup_iin_only_template_id
    assert fake_pipelines.created[0]["context"]["row_id_iin"] == row_id
    assert (
        fake_pipelines.created[0]["context"]["beeline_secrets_url"]
        == settings.beeline_secrets_url_for_pipeline
    )


@pytest.mark.asyncio
async def test_lookup_with_phone_uses_uniq_template_and_row_id_full(
    settings, fake_pipelines, fake_secrets, lookup_service
):
    salt_pkb = fake_secrets.value
    row_id = compute_row_id_full(
        salt_pkb, "801217301434", "7000000028", settings.iin_salt
    )
    fake_pipelines.set(row_id, "x\n42\n")

    result = await lookup_service.lookup("801217301434", "7000000028")

    assert result == [{"x": "42"}]
    assert (
        fake_pipelines.created[0]["template_id"]
        == settings.lookup_iin_phone_template_id
    )
    assert fake_pipelines.created[0]["context"]["row_id_full"] == row_id


@pytest.mark.asyncio
async def test_lookup_not_found_returns_empty_list(lookup_service):
    result = await lookup_service.lookup("801217301434", None)
    assert result == []


@pytest.mark.asyncio
async def test_lookup_timeout_propagates(
    settings, fake_pipelines, fake_secrets, lookup_service
):
    salt_pkb = fake_secrets.value
    row_id = compute_row_id_iin(salt_pkb, "801217301434", settings.iin_salt)
    fake_pipelines.set(row_id, PipelineTimeoutError("timed out"))

    with pytest.raises(PipelineTimeoutError):
        await lookup_service.lookup("801217301434", None)


@pytest.mark.asyncio
async def test_lookup_many_collects_per_item_errors(
    settings, fake_pipelines, fake_secrets, lookup_service
):
    salt_pkb = fake_secrets.value
    a_row = compute_row_id_iin(salt_pkb, "111111111111", settings.iin_salt)
    b_row = compute_row_id_iin(salt_pkb, "222222222222", settings.iin_salt)
    fake_pipelines.set(a_row, "z\n9\n")
    fake_pipelines.set(b_row, PipelineUnavailableError("upstream gone"))

    out = await lookup_service.lookup_many(
        [("111111111111", None), ("222222222222", None)]
    )

    assert out[0] == [{"z": "9"}]
    assert isinstance(out[1], PipelineUnavailableError)
