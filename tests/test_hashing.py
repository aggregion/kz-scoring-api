import hashlib
import hmac

from kz_scoring_api.hashing import compute_row_id_full, compute_row_id_iin, hash_iin


def test_hash_iin_matches_sha256_of_iin_plus_salt():
    iin = "801217301434"
    salt = "secretsalt20260406"
    assert hash_iin(iin, salt) == hashlib.sha256((iin + salt).encode()).hexdigest()


def test_compute_row_id_iin_is_hmac_of_iin_hash():
    iin = "801217301434"
    salt = "secretsalt20260406"
    salt_pkb = b"\x01" * 32
    iin_hash = hash_iin(iin, salt)
    expected = hmac.new(salt_pkb, iin_hash.encode(), hashlib.sha256).hexdigest()
    assert compute_row_id_iin(salt_pkb, iin, salt) == expected


def test_compute_row_id_full_is_hmac_of_iin_hash_pipe_phone():
    iin = "801217301434"
    phone = "7000000028"
    salt = "secretsalt20260406"
    salt_pkb = b"\x02" * 32
    iin_hash = hash_iin(iin, salt)
    expected = hmac.new(
        salt_pkb, (iin_hash + "|" + phone).encode(), hashlib.sha256
    ).hexdigest()
    assert compute_row_id_full(salt_pkb, iin, phone, salt) == expected


def test_row_id_iin_differs_from_row_id_full():
    iin = "801217301434"
    phone = "7000000028"
    salt = "secretsalt20260406"
    salt_pkb = b"\x03" * 32
    assert (
        compute_row_id_iin(salt_pkb, iin, salt)
        != compute_row_id_full(salt_pkb, iin, phone, salt)
    )
