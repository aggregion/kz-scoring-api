import hashlib
import hmac


def hash_iin(iin: str, iin_salt: str) -> str:
    return hashlib.sha256((iin + iin_salt).encode()).hexdigest()


def compute_row_id_iin(salt_pkb: bytes, iin: str, iin_salt: str) -> str:
    iin_hash = hash_iin(iin, iin_salt)
    return hmac.new(salt_pkb, iin_hash.encode(), hashlib.sha256).hexdigest()


def compute_row_id_full(salt_pkb: bytes, iin: str, phone: str, iin_salt: str) -> str:
    iin_hash = hash_iin(iin, iin_salt)
    return hmac.new(
        salt_pkb, (iin_hash + "|" + phone).encode(), hashlib.sha256
    ).hexdigest()
