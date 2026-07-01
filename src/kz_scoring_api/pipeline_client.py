import asyncio
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
    run_id: int
    system_id: str


class VaulteePipelinesClient:
    """Thin GraphQL client over vaultee-pipelines.

    Flow expected by callers:
      1. ``create_from_template`` -> pipeline_id
      2. ``run_pipeline``        -> {run_id, system_id (= ddm sessionId)}
      3. ``wait_for_completion`` -> polls until DONE/ERROR/ABORTED or timeout
      4. ``fetch_result``        -> reads the publish_to_session payload (string)

    Result-fetch query is intentionally kept as a configurable single GraphQL
    op so that whatever AGG-96 finalizes (a ``pipelineRunResult`` query, a
    field on PipelineRun, etc.) can be plugged in via env without changing
    the service body.
    """

    CREATE_FROM_TEMPLATE_MUTATION = """
    mutation CreateFromTemplate($input: CreateFromTemplateInput!) {
      createFromTemplate(input: $input) { id }
    }
    """

    RUN_PIPELINE_MUTATION = """
    mutation RunPipeline($pipelineId: Int!, $context: JSON) {
      runPipeline(pipelineId: $pipelineId, context: $context) { runId systemId }
    }
    """

    PIPELINE_RUN_STATUS_QUERY = """
    query PipelineRunStatus($id: Int!) {
      pipelineRun(id: $id) { id status systemId }
    }
    """

    PIPELINE_RUN_RESULT_QUERY = """
    query PipelineRunResult($id: Int!) {
      pipelineRun(id: $id) { id status resultJson }
    }
    """

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
        self._url = url
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

    async def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        client = await self._client()
        try:
            resp = await client.post(
                self._url,
                json={"query": query, "variables": variables},
                headers={
                    "x-auth-subject": self._service_subject,
                    "x-vaultee-tenant": self._tenant_id,
                },
            )
        except httpx.HTTPError as exc:
            raise PipelineUnavailableError(
                f"vaultee-pipelines transport error: {exc}"
            ) from exc
        if resp.status_code >= 500:
            raise PipelineUnavailableError(
                f"vaultee-pipelines HTTP {resp.status_code}: {resp.text[:200]}"
            )
        if resp.status_code != 200:
            raise PipelineError(
                f"vaultee-pipelines HTTP {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        if body.get("errors"):
            raise PipelineError(f"GraphQL errors: {body['errors']}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise PipelineError(f"GraphQL response missing data: {body}")
        return data

    async def create_from_template(
        self,
        template_id: int,
        name: str,
        context: dict[str, Any],
    ) -> int:
        data = await self._gql(
            self.CREATE_FROM_TEMPLATE_MUTATION,
            {
                "input": {
                    "templateId": template_id,
                    "executorId": self._executor_id,
                    "name": name,
                    "context": context,
                }
            },
        )
        pipeline_id = data["createFromTemplate"]["id"]
        return int(pipeline_id)

    async def run_pipeline(
        self, pipeline_id: int, context: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        data = await self._gql(
            self.RUN_PIPELINE_MUTATION,
            {"pipelineId": pipeline_id, "context": context},
        )
        run = data["runPipeline"]
        return int(run["runId"]), str(run["systemId"])

    async def wait_for_completion(self, run_id: int, deadline_s: float) -> None:
        loop = asyncio.get_event_loop()
        start = loop.time()
        while True:
            data = await self._gql(
                self.PIPELINE_RUN_STATUS_QUERY, {"id": run_id}
            )
            run = data.get("pipelineRun")
            if not run:
                raise PipelineError(f"pipelineRun({run_id}) not found")
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

    async def fetch_result(self, run_id: int) -> str:
        data = await self._gql(
            self.PIPELINE_RUN_RESULT_QUERY, {"id": run_id}
        )
        run = data.get("pipelineRun")
        if not run:
            raise PipelineError(f"pipelineRun({run_id}) not found when fetching result")
        result = run.get("resultJson")
        if result is None:
            return ""
        if isinstance(result, str):
            return result
        return str(result)

    async def close(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None
