import os
from typing import Any

import pytest

os.environ.setdefault(
    "KZ_SCORING_PIPELINES_TENANT_ID", "00000000-0000-0000-0000-000000000001"
)

from fastapi.testclient import TestClient  # noqa: E402

from kz_scoring_api.app import build_app  # noqa: E402
from kz_scoring_api.config import Settings  # noqa: E402
from kz_scoring_api.lookup import LookupService  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(
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


class FakeSecrets:
    def __init__(self, value: bytes = b"\x42" * 32):
        self.value = value
        self.calls = 0

    async def get_secret(self, token: str) -> bytes:
        self.calls += 1
        return self.value


class FakePipelines:
    """In-memory stand-in. Configure ``payloads`` keyed by the row_id the
    LookupService will derive (either ``row_id_iin`` or ``row_id_full``);
    the fake returns the matching TSV (or raises) when ``fetch_result``
    is called for that run.
    """

    def __init__(self, payloads: dict[str, str | Exception] | None = None):
        self.payloads = payloads or {}
        self.created: list[dict[str, Any]] = []
        self.runs: list[int] = []
        self.wait_calls: list[str] = []

    def set(self, row_id: str, value: str | Exception) -> None:
        self.payloads[row_id] = value

    async def create_from_template(
        self, template_id: int, name: str, context: dict[str, Any]
    ) -> int:
        self.created.append(
            {"template_id": template_id, "name": name, "context": context}
        )
        return len(self.created)

    async def run_pipeline(
        self, pipeline_id: int, context: dict[str, Any] | None = None
    ):
        self.runs.append(pipeline_id)
        return (f"run-{pipeline_id}", f"sys-{pipeline_id}")

    async def wait_for_completion(self, run_id: str, deadline_s: float) -> None:
        self.wait_calls.append(run_id)
        idx = int(run_id.removeprefix("run-")) - 1
        ctx = self.created[idx]["context"]
        row_id = ctx.get("row_id_iin") or ctx.get("row_id_full")
        value = self.payloads.get(row_id)
        if isinstance(value, Exception):
            raise value

    async def fetch_result(self, run_id: str) -> str:
        idx = int(run_id.removeprefix("run-")) - 1
        ctx = self.created[idx]["context"]
        row_id = ctx.get("row_id_iin") or ctx.get("row_id_full")
        value = self.payloads.get(row_id)
        if isinstance(value, Exception):
            raise value
        return value or ""


@pytest.fixture
def fake_secrets() -> FakeSecrets:
    return FakeSecrets()


@pytest.fixture
def fake_pipelines() -> FakePipelines:
    return FakePipelines()


@pytest.fixture
def lookup_service(
    settings: Settings, fake_pipelines: FakePipelines, fake_secrets: FakeSecrets
) -> LookupService:
    return LookupService(settings, fake_pipelines, fake_secrets)


@pytest.fixture
def test_client(
    settings: Settings, fake_pipelines: FakePipelines, fake_secrets: FakeSecrets
) -> TestClient:
    app = build_app(settings)
    app.state.lookup = LookupService(settings, fake_pipelines, fake_secrets)
    return TestClient(app)
