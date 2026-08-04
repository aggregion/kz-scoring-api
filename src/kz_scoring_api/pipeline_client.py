import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    pass


class PipelineTimeoutError(PipelineError):
    pass


class PipelineUnavailableError(PipelineError):
    pass


class PipelineFailedError(PipelineError):
    pass


@dataclass
class PipelineRunHandle:
    pipeline_id: int
    run_id: str
    system_id: str


class VaulteePipelinesClient:
    """REST client for the vaultee-pipelines internal API.

    Flow expected by callers:
      1. ``create_from_template`` -> pipeline_id
      2. ``run_pipeline``        -> {run_id, system_id (= ddm sessionId)}
      3. ``wait_for_completion`` -> polls until DONE/ERROR/ABORTED or timeout
      4. ``fetch_result``        -> reads the publish_to_session payload (string)

    Auth headers (``x-auth-subject`` / ``x-vaultee-tenant``) are injected on
    every request; the upstream tenant guard rejects requests without them.
    """

    CREATE_FROM_TEMPLATE_PATH = "/api/pipeline/createFromTemplate"
    RUN_PIPELINE_PATH = "/api/pipeline/run"
    PIPELINE_RUN_PATH = "/api/pipeline-run"

    TERMINAL_STATUSES = {"done", "error", "aborted"}
    SUCCESS_STATUS = "done"

    def __init__(
        self,
        url: str,
        timeout_seconds: float,
        poll_interval_ms: int,
        executor_id: int,
        service_subject: str,
        tenant_id: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = url.rstrip("/")
        self._timeout = timeout_seconds
        self._poll_interval = max(0.01, poll_interval_ms / 1000.0)
        self._executor_id = executor_id
        self._service_subject = service_subject
        self._tenant_id = tenant_id
        self._http = http
        self._owns_http = http is None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout + 5.0)
        return self._http

    def _headers(self) -> dict[str, str]:
        return {
            "x-auth-subject": self._service_subject,
            "x-vaultee-tenant": self._tenant_id,
        }

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._client()
        url = f"{self._base_url}{path}"
        try:
            resp = await client.request(
                method,
                url,
                json=json_body,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise PipelineUnavailableError(
                f"vaultee-pipelines transport error: {exc}"
            ) from exc
        if resp.status_code >= 500:
            raise PipelineUnavailableError(
                f"vaultee-pipelines HTTP {resp.status_code}: "
                f"{self._error_detail(resp)}"
            )
        if resp.status_code >= 400:
            raise PipelineError(
                f"vaultee-pipelines HTTP {resp.status_code}: "
                f"{self._error_detail(resp)}"
            )
        if not resp.content:
            return {}
        try:
            body = resp.json()
        except ValueError as exc:
            raise PipelineError(
                f"vaultee-pipelines returned non-JSON body: {resp.text[:200]}"
            ) from exc
        if not isinstance(body, dict):
            raise PipelineError(
                f"vaultee-pipelines returned unexpected JSON: {resp.text[:200]}"
            )
        return body

    @staticmethod
    def _error_detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return resp.text[:200]
        if isinstance(body, dict):
            message = body.get("message")
            if isinstance(message, list):
                message = "; ".join(str(m) for m in message)
            error = body.get("error")
            parts = [str(p) for p in (error, message) if p]
            if parts:
                return " - ".join(parts)
        return str(body)[:200]

    async def create_from_template(
        self,
        template_id: int,
        name: str,
        context: dict[str, Any],
    ) -> int:
        body = await self._request(
            "POST",
            self.CREATE_FROM_TEMPLATE_PATH,
            {
                "templateId": template_id,
                "executorId": self._executor_id,
                "name": name,
                "context": context,
            },
        )
        pipeline_id = body.get("id")
        if pipeline_id is None:
            raise PipelineError(
                f"createFromTemplate response missing 'id': {body}"
            )
        return int(pipeline_id)

    async def run_pipeline(
        self, pipeline_id: int, context: dict[str, Any] | None = None
    ) -> tuple[str, str]:
        body = await self._request(
            "POST",
            self.RUN_PIPELINE_PATH,
            {"pipelineId": pipeline_id, "context": context},
        )
        run_id = body.get("runId")
        system_id = body.get("systemId")
        if run_id is None or system_id is None:
            raise PipelineError(
                f"run response missing runId/systemId: {body}"
            )
        return str(run_id), str(system_id)

    async def _get_run(self, run_id: str | int) -> dict[str, Any]:
        return await self._request(
            "GET", f"{self.PIPELINE_RUN_PATH}/{run_id}"
        )

    async def wait_for_completion(
        self, run_id: str | int, deadline_s: float
    ) -> None:
        loop = asyncio.get_event_loop()
        start = loop.time()
        while True:
            run = await self._get_run(run_id)
            status = str(run.get("status", "")).lower()
            if status in self.TERMINAL_STATUSES:
                if status == self.SUCCESS_STATUS:
                    return
                raise PipelineFailedError(
                    f"pipeline run {run_id} ended with status={status}"
                )
            if (loop.time() - start) > deadline_s:
                raise PipelineTimeoutError(
                    f"pipeline run {run_id} not done in {deadline_s:.1f}s "
                    f"(last status={status})"
                )
            await asyncio.sleep(self._poll_interval)

    async def fetch_result(self, run_id: str | int) -> str:
        run = await self._get_run(run_id)
        result = run.get("resultJson")
        if result is None:
            return ""
        # DDM publish_to_session with `type: json` + `type: extract` +
        # `single_value: true` writes the TSV as a JSON-encoded string
        # (`"header\tcol\n7775108170\t0.1\n"`). vaultee-pipelines forwards
        # it verbatim, so we get one round of JSON quoting on top of the
        # actual TSV. Unwrap it before handing the payload to parse_tsv;
        # anything that isn't a JSON string (legacy raw TSV, dict result,
        # etc.) passes through unchanged.
        if isinstance(result, str):
            stripped = result.lstrip()
            if stripped.startswith('"'):
                try:
                    decoded = json.loads(result)
                except ValueError:
                    return result
                if isinstance(decoded, str):
                    return decoded
                return json.dumps(decoded)
            return result
        return str(result)

    async def close(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None
