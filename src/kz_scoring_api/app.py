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

    def get_lookup_service(request: Request) -> LookupService:
        return request.app.state.lookup

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/single")
    async def single(
        iin: str = Query(..., min_length=12, max_length=12, pattern=r"^\d{12}$"),
        phone: str | None = Query(default=None, pattern=r"^\d{6,15}$"),
        lookup: LookupService = Depends(get_lookup_service),
    ) -> list[dict[str, Any]]:
        try:
            return await lookup.lookup(iin, phone)
        except PipelineTimeoutError as exc:
            raise HTTPException(status_code=408, detail=str(exc)) from exc
        except PipelineUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except PipelineFailedError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/multi")
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
