from kz_scoring_api.hashing import compute_row_id_full, compute_row_id_iin
from kz_scoring_api.pipeline_client import (
    PipelineTimeoutError,
    PipelineUnavailableError,
)


def test_healthz(test_client):
    r = test_client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_single_iin_only_found_returns_array(
    test_client, settings, fake_pipelines, fake_secrets
):
    row_id = compute_row_id_iin(
        fake_secrets.salt_bytes, "801217301434", settings.iin_salt
    )
    fake_pipelines.set(row_id, "a\tb\n1\t2\n3\t4\n")

    r = test_client.get("/single", params={"iin": "801217301434"})
    assert r.status_code == 200
    assert r.json() == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_single_iin_only_not_found_returns_null(test_client):
    r = test_client.get("/single", params={"iin": "801217301434"})
    assert r.status_code == 200
    assert r.json() is None


def test_single_iin_phone_found_returns_object(
    test_client, settings, fake_pipelines, fake_secrets
):
    row_id = compute_row_id_full(
        fake_secrets.salt_bytes, "801217301434", "7000000028", settings.iin_salt
    )
    fake_pipelines.set(row_id, "a\tb\n1\t2\n")

    r = test_client.get(
        "/single", params={"iin": "801217301434", "phone": "7000000028"}
    )
    assert r.status_code == 200
    assert r.json() == {"a": "1", "b": "2"}


def test_single_iin_phone_not_found_returns_null(test_client):
    r = test_client.get(
        "/single", params={"iin": "801217301434", "phone": "7000000028"}
    )
    assert r.status_code == 200
    assert r.json() is None


def test_single_invalid_iin_422(test_client):
    r = test_client.get("/single", params={"iin": "short"})
    assert r.status_code == 422


def test_single_timeout_returns_408(
    test_client, settings, fake_pipelines, fake_secrets
):
    row_id = compute_row_id_iin(
        fake_secrets.salt_bytes, "801217301434", settings.iin_salt
    )
    fake_pipelines.set(row_id, PipelineTimeoutError("deadline exceeded"))

    r = test_client.get("/single", params={"iin": "801217301434"})
    assert r.status_code == 408


def test_single_upstream_unavailable_returns_502(
    test_client, settings, fake_pipelines, fake_secrets
):
    row_id = compute_row_id_iin(
        fake_secrets.salt_bytes, "801217301434", settings.iin_salt
    )
    fake_pipelines.set(row_id, PipelineUnavailableError("connection refused"))

    r = test_client.get("/single", params={"iin": "801217301434"})
    assert r.status_code == 502


def test_multi_mixed_shapes_and_null_for_not_found(
    test_client, settings, fake_pipelines, fake_secrets
):
    salt = fake_secrets.salt_bytes
    iin_a = "111111111111"
    iin_b = "222222222222"
    iin_c = "333333333333"
    iin_d = "444444444444"

    a_row = compute_row_id_iin(salt, iin_a, settings.iin_salt)
    b_row = compute_row_id_full(salt, iin_b, "7000000028", settings.iin_salt)
    c_row = compute_row_id_iin(salt, iin_c, settings.iin_salt)
    d_row = compute_row_id_full(salt, iin_d, "7000000099", settings.iin_salt)

    fake_pipelines.set(a_row, "f\n10\n20\n")  # iin-only found → array
    fake_pipelines.set(b_row, "f\n42\n")  # iin+phone found → object
    fake_pipelines.set(c_row, "")  # iin-only not found → null
    fake_pipelines.set(d_row, "")  # iin+phone not found → null

    body = [
        {"iin": iin_a},
        {"iin": iin_b, "phone": "7000000028"},
        {"iin": iin_c},
        {"iin": iin_d, "phone": "7000000099"},
    ]
    r = test_client.post("/multi", json=body)
    assert r.status_code == 200
    payload = r.json()
    assert payload == [
        [{"f": "10"}, {"f": "20"}],
        {"f": "42"},
        None,
        None,
    ]


def test_multi_partial_failure_is_207(
    test_client, settings, fake_pipelines, fake_secrets
):
    salt = fake_secrets.salt_bytes
    a_row = compute_row_id_iin(salt, "111111111111", settings.iin_salt)
    b_row = compute_row_id_iin(salt, "222222222222", settings.iin_salt)
    fake_pipelines.set(a_row, "f\n10\n")
    fake_pipelines.set(b_row, PipelineUnavailableError("nope"))

    r = test_client.post(
        "/multi",
        json=[{"iin": "111111111111"}, {"iin": "222222222222"}],
    )
    assert r.status_code == 207
    payload = r.json()
    assert payload[0] == [{"f": "10"}]
    assert payload[1] == {"error": "upstream_unavailable", "message": "nope"}


def test_multi_empty_input_returns_empty_array(test_client):
    r = test_client.post("/multi", json=[])
    assert r.status_code == 200
    assert r.json() == []


def test_multi_invalid_item_422(test_client):
    r = test_client.post("/multi", json=[{"iin": "short"}])
    assert r.status_code == 422
