"""Regression test for the SALT_PKB hex-decode bug (AGG-117).

vaultee-secrets stores SALT_PKB as an ASCII hex string (64 chars = 32 raw bytes).
Before this fix, `LookupService._salt_pkb` returned the raw HTTP body as-is,
feeding the 64-byte ASCII string into `hmac.new()` as the key. Rebuild-encrypt
(pkb_beeline/scripts/encrypt/main.py::read_hex_secret) decodes with
`bytes.fromhex`, so the two sides produced different HMAC keys and every
lookup missed the replica.

This test pins the invariant: whatever LookupService derives for `row_id_iin`
must be exactly what a rebuild-time HMAC over the same *decoded* salt bytes
produces.
"""
import hashlib
import hmac

import pytest

from kz_scoring_api.hashing import compute_row_id_iin


@pytest.mark.asyncio
async def test_salt_pkb_is_hex_decoded_before_hmac(
    settings, fake_pipelines, fake_secrets, lookup_service
):
    # Fake returns the salt as ASCII hex (the real vaultee-secrets shape).
    # The API must decode it to raw 32 bytes before HMAC — otherwise
    # `row_id_iin` won't match what rebuild-encrypt wrote into the replica.
    salt_bytes = fake_secrets.salt_bytes
    assert len(salt_bytes) == 32

    iin = "700205302324"
    expected_row_id = compute_row_id_iin(salt_bytes, iin, settings.iin_salt)

    # Direct HMAC using the raw ASCII hex string as key (the pre-fix bug) —
    # must produce a *different* value, otherwise this test is not actually
    # exercising the regression.
    iin_hash = hashlib.sha256((iin + settings.iin_salt).encode()).hexdigest()
    buggy_row_id = hmac.new(
        salt_bytes.hex().encode("ascii"), iin_hash.encode(), hashlib.sha256
    ).hexdigest()
    assert expected_row_id != buggy_row_id, (
        "fixture is not exercising the bug — pick different salt bytes"
    )

    fake_pipelines.set(expected_row_id, "col\nok\n")

    result = await lookup_service.lookup(iin, None)

    assert result == [{"col": "ok"}]
    sent_row_id = fake_pipelines.created[0]["context"]["row_id_iin"]
    assert sent_row_id == expected_row_id, (
        f"LookupService sent {sent_row_id!r} but replica keys under {expected_row_id!r}"
    )
    assert sent_row_id != buggy_row_id
