import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings, get_settings
from .lookup import LookupService
from .models import MultiInputItem
from .pipeline_client import (
    PipelineFailedError,
    PipelineTimeoutError,
    PipelineUnavailableError,
    VaulteePipelinesClient,
)
from .secrets import VaulteeSecretsClient

logger = logging.getLogger(__name__)


# OpenAPI response schema for a single (iin, phone?) lookup:
#   - object: a feature row (phone-uniq case)
#   - array:  list of feature rows (iin-only case)
#   - null:   not found (either case)
_SINGLE_RESULT_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "object", "additionalProperties": {"type": "string"}},
        {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        {"type": "null"},
    ],
    "description": (
        "object (phone-uniq), array (iin-only), or null (not found). "
        "Status 200 in all three cases — null is a valid successful response, "
        "not an error."
    ),
}

_MULTI_RESULT_SCHEMA: dict[str, Any] = {
    "type": "array",
    "description": (
        "Array in the same order as the input. Each element is object | array | "
        "null (see /single), OR a per-item error `{error, message}` on partial "
        "failure."
    ),
    "items": {
        "oneOf": [
            {"type": "object", "additionalProperties": {"type": "string"}},
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            {"type": "null"},
            {
                "type": "object",
                "properties": {
                    "error": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["error", "message"],
            },
        ]
    },
}


def build_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        http = httpx.AsyncClient(timeout=settings.timeout_seconds + 5.0)
        pipelines = VaulteePipelinesClient(
            url=settings.vaultee_pipelines_api_url,
            timeout_seconds=settings.timeout_seconds,
            poll_interval_ms=settings.poll_interval_ms,
            executor_id=settings.pipeline_executor_id,
            service_subject=settings.pipelines_service_subject,
            tenant_id=settings.pipelines_tenant_id,
            http=http,
        )
        secrets = VaulteeSecretsClient(
            base_url=settings.vaultee_secrets_url,
            ttl_seconds=settings.salt_cache_ttl_seconds,
            http=http,
        )
        app.state.lookup = LookupService(settings, pipelines, secrets)
        app.state.http = http
        try:
            yield
        finally:
            await http.aclose()

    app = FastAPI(
        title="kz-scoring-api",
        version=__version__,
        description=(
            "Synchronous REST facade for Beeline-initiator PKB lookups "
            "via vaultee-pipelines."
        ),
        lifespan=lifespan,
    )

    # Static shared-secret gate. When settings.api_token is empty, the middleware
    # is a passthrough — matches the original unauthenticated behaviour so
    # existing dev-stand runs and internal port-forwards keep working. When set,
    # every request except GET /healthz must carry a matching X-API-Key header.
    # /healthz stays open so k8s liveness/readiness probes and external monitors
    # don't need to know the token.
    _api_token = (settings.api_token or "").encode()

    @app.middleware("http")
    async def api_token_middleware(request: Request, call_next):
        if not _api_token or request.url.path == "/healthz":
            return await call_next(request)
        provided = request.headers.get("x-api-key", "").encode()
        if not hmac.compare_digest(provided, _api_token):
            return JSONResponse(
                status_code=401,
                content={"detail": "invalid or missing X-API-Key"},
            )
        return await call_next(request)

    def get_lookup_service(request: Request) -> LookupService:
        return request.app.state.lookup

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get(
        "/single",
        responses={200: {"content": {"application/json": {"schema": _SINGLE_RESULT_SCHEMA}}}},
    )
    async def single(
        iin: str = Query(..., min_length=12, max_length=12, pattern=r"^\d{12}$"),
        phone: str | None = Query(default=None, pattern=r"^\d{6,15}$"),
        lookup: LookupService = Depends(get_lookup_service),
    ) -> JSONResponse:
        try:
            result = await lookup.lookup(iin, phone)
        except PipelineTimeoutError as exc:
            raise HTTPException(status_code=408, detail=str(exc)) from exc
        except PipelineUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except PipelineFailedError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse(status_code=200, content=result)

    @app.post(
        "/multi",
        responses={
            200: {"content": {"application/json": {"schema": _MULTI_RESULT_SCHEMA}}},
            207: {"content": {"application/json": {"schema": _MULTI_RESULT_SCHEMA}}},
        },
    )
    async def multi(
        items: list[MultiInputItem],
        lookup: LookupService = Depends(get_lookup_service),
    ) -> JSONResponse:
        pairs = [(it.iin, it.phone) for it in items]
        results = await lookup.lookup_many(pairs)

        any_unavailable = any(
            isinstance(r, PipelineUnavailableError) for r in results
        )
        if any_unavailable and all(isinstance(r, Exception) for r in results):
            raise HTTPException(
                status_code=502,
                detail="all upstream lookups failed; vaultee-pipelines unavailable",
            )

        any_timeout = any(isinstance(r, PipelineTimeoutError) for r in results)

        body: list[Any] = []
        for r in results:
            if isinstance(r, Exception):
                body.append({"error": _classify(r), "message": str(r)})
            else:
                body.append(r)

        status_code = 200
        if any_unavailable:
            status_code = 207
        elif any_timeout:
            status_code = 207
        return JSONResponse(status_code=status_code, content=body)

    return app


def _classify(exc: Exception) -> str:
    if isinstance(exc, PipelineTimeoutError):
        return "timeout"
    if isinstance(exc, PipelineUnavailableError):
        return "upstream_unavailable"
    if isinstance(exc, PipelineFailedError):
        return "pipeline_failed"
    return "internal_error"


app = build_app()
