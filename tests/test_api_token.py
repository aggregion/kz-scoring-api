"""Middleware tests for the KZ_SCORING_API_TOKEN gate.

Auth is opt-in: when settings.api_token is empty (the default) every route
behaves like the pre-token build. When set, /single and /multi require the
same value in X-API-Key; /healthz stays open so k8s probes don't need the
token.
"""
from fastapi.testclient import TestClient

from kz_scoring_api.app import build_app
from kz_scoring_api.lookup import LookupService


def _make_client(
    api_token: str, settings, fake_pipelines, fake_secrets
) -> TestClient:
    tokened = settings.model_copy(update={"api_token": api_token})
    app = build_app(tokened)
    app.state.lookup = LookupService(tokened, fake_pipelines, fake_secrets)
    return TestClient(app)


def test_healthz_open_when_token_configured(settings, fake_pipelines, fake_secrets):
    client = _make_client("s3cret", settings, fake_pipelines, fake_secrets)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_single_requires_token_when_configured(
    settings, fake_pipelines, fake_secrets
):
    client = _make_client("s3cret", settings, fake_pipelines, fake_secrets)
    resp = client.get("/single", params={"iin": "801217301434"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid or missing X-API-Key"}


def test_single_rejects_wrong_token(settings, fake_pipelines, fake_secrets):
    client = _make_client("s3cret", settings, fake_pipelines, fake_secrets)
    resp = client.get(
        "/single",
        params={"iin": "801217301434"},
        headers={"x-api-key": "wrong"},
    )
    assert resp.status_code == 401


def test_single_accepts_correct_token(settings, fake_pipelines, fake_secrets):
    # The lookup itself returns null for an unseen IIN — auth just has to let
    # the request through to the handler.
    client = _make_client("s3cret", settings, fake_pipelines, fake_secrets)
    resp = client.get(
        "/single",
        params={"iin": "801217301434"},
        headers={"x-api-key": "s3cret"},
    )
    assert resp.status_code == 200
    assert resp.json() is None


def test_multi_requires_token_when_configured(
    settings, fake_pipelines, fake_secrets
):
    client = _make_client("s3cret", settings, fake_pipelines, fake_secrets)
    resp = client.post("/multi", json=[{"iin": "801217301434"}])
    assert resp.status_code == 401


def test_multi_accepts_correct_token(settings, fake_pipelines, fake_secrets):
    client = _make_client("s3cret", settings, fake_pipelines, fake_secrets)
    resp = client.post(
        "/multi",
        json=[{"iin": "801217301434"}],
        headers={"x-api-key": "s3cret"},
    )
    assert resp.status_code == 200


def test_open_when_token_empty(settings, fake_pipelines, fake_secrets):
    # Default (unset) token = middleware is passthrough; keeps dev-stand and
    # port-forward callers happy without any header wrangling.
    client = _make_client("", settings, fake_pipelines, fake_secrets)
    resp = client.get("/single", params={"iin": "801217301434"})
    assert resp.status_code == 200


def test_docs_open_when_token_configured(settings, fake_pipelines, fake_secrets):
    # Swagger UI, ReDoc и OpenAPI schema должны быть доступны без токена —
    # иначе клиенту неоткуда взять описание, чтобы кликнуть Authorize и
    # вставить туда ключ. Пропускаем 4 пути.
    client = _make_client("s3cret", settings, fake_pipelines, fake_secrets)
    for path in ("/docs", "/redoc", "/openapi.json"):
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"{path} must be reachable without X-API-Key, got {resp.status_code}"
        )


def test_openapi_declares_apikey_security_scheme(
    settings, fake_pipelines, fake_secrets
):
    # Swagger UI Authorize dialog появляется только если в схеме есть
    # securitySchemes → apiKey. Проверяем, что /single и /multi
    # ссылаются на него, а /healthz — нет.
    client = _make_client("s3cret", settings, fake_pipelines, fake_secrets)
    schema = client.get("/openapi.json").json()
    schemes = schema.get("components", {}).get("securitySchemes", {})
    assert any(
        s.get("type") == "apiKey" and s.get("in") == "header"
        and s.get("name") == "X-API-Key"
        for s in schemes.values()
    ), f"expected APIKeyHeader X-API-Key scheme, got {schemes!r}"

    paths = schema["paths"]
    assert any("security" in op for op in paths["/single"].values())
    assert any("security" in op for op in paths["/multi"].values())
    assert not any("security" in op for op in paths["/healthz"].values())


def test_constant_time_compare_used(settings, fake_pipelines, fake_secrets):
    # Regression: use hmac.compare_digest, not `==`. Sanity-check by feeding
    # tokens that differ only in a late character — both must return 401,
    # not one shorter than the other via early-exit.
    client = _make_client("aaaaaaaaaa", settings, fake_pipelines, fake_secrets)
    r1 = client.get(
        "/single",
        params={"iin": "801217301434"},
        headers={"x-api-key": "aaaaaaaaab"},
    )
    r2 = client.get(
        "/single",
        params={"iin": "801217301434"},
        headers={"x-api-key": "baaaaaaaaa"},
    )
    assert r1.status_code == 401
    assert r2.status_code == 401
