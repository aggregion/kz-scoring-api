import json

import pytest

from kz_scoring_api.hashing import compute_row_id_full, compute_row_id_iin
from kz_scoring_api.pipeline_client import (
    PipelineTimeoutError,
    PipelineUnavailableError,
)


@pytest.mark.asyncio
async def test_lookup_iin_only_found_returns_list(
    settings, fake_pipelines, fake_secrets, lookup_service
):
    salt_pkb = fake_secrets.value
    row_id = compute_row_id_iin(salt_pkb, "801217301434", settings.iin_salt)
    fake_pipelines.set(
        row_id,
        json.dumps(
            [
                {"col_a": "1", "col_b": "2"},
                {"col_a": "3", "col_b": "4"},
            ]
        ),
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
async def test_lookup_with_phone_found_returns_object(
    settings, fake_pipelines, fake_secrets, lookup_service
):
    salt_pkb = fake_secrets.value
    row_id = compute_row_id_full(
        salt_pkb, "801217301434", "7000000028", settings.iin_salt
    )
    fake_pipelines.set(row_id, json.dumps([{"x": "42"}]))

    result = await lookup_service.lookup("801217301434", "7000000028")

    assert result == {"x": "42"}
    assert (
        fake_pipelines.created[0]["template_id"]
        == settings.lookup_iin_phone_template_id
    )
    assert fake_pipelines.created[0]["context"]["row_id_full"] == row_id


@pytest.mark.asyncio
async def test_lookup_iin_only_not_found_returns_none(lookup_service):
    result = await lookup_service.lookup("801217301434", None)
    assert result is None


@pytest.mark.asyncio
async def test_lookup_with_phone_not_found_returns_none(lookup_service):
    result = await lookup_service.lookup("801217301434", "7000000028")
    assert result is None


@pytest.mark.asyncio
async def test_lookup_iin_only_empty_array_returns_none(
    settings, fake_pipelines, fake_secrets, lookup_service
):
    """Pipeline succeeded but the CH-lookup was empty — decrypt writes
    "[]" and the API surfaces that as null, not an empty list. Keeps
    the /single not-found contract consistent regardless of whether
    "no data" came from empty resultJson or a well-formed empty array.
    """
    salt_pkb = fake_secrets.value
    row_id = compute_row_id_iin(salt_pkb, "801217301434", settings.iin_salt)
    fake_pipelines.set(row_id, "[]")

    result = await lookup_service.lookup("801217301434", None)

    assert result is None


@pytest.mark.asyncio
async def test_lookup_double_encoded_payload_is_unwrapped(
    settings, fake_pipelines, fake_secrets, lookup_service
):
    """Defensive: if vaultee-pipelines relays the DDM Extract-string
    payload with one extra JSON-string layer, the parser still finds
    the intended array. See payload._maybe_double_decode.
    """
    salt_pkb = fake_secrets.value
    row_id = compute_row_id_iin(salt_pkb, "801217301434", settings.iin_salt)
    inner = json.dumps([{"z": "9"}])
    fake_pipelines.set(row_id, json.dumps(inner))

    result = await lookup_service.lookup("801217301434", None)

    assert result == [{"z": "9"}]


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
    fake_pipelines.set(a_row, json.dumps([{"z": "9"}]))
    fake_pipelines.set(b_row, PipelineUnavailableError("upstream gone"))

    out = await lookup_service.lookup_many(
        [("111111111111", None), ("222222222222", None)]
    )

    assert out[0] == [{"z": "9"}]
    assert isinstance(out[1], PipelineUnavailableError)
