import httpx
import pytest
import respx

from kz_scoring_api.pipeline_client import (
    PipelineFailedError,
    PipelineUnavailableError,
    VaulteePipelinesClient,
)


@pytest.mark.asyncio
async def test_create_from_template_returns_id():
    async with httpx.AsyncClient() as http:
        client = VaulteePipelinesClient(
            url="http://pipelines.example/graphql",
            timeout_seconds=5,
            poll_interval_ms=10,
            executor_id=7,
            http=http,
        )
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post("http://pipelines.example/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={"data": {"createFromTemplate": {"id": 42}}},
                )
            )
            pid = await client.create_from_template(
                template_id=11, name="x", context={"row_id_iin": "abc"}
            )
            assert pid == 42


@pytest.mark.asyncio
async def test_run_pipeline_returns_run_and_system_id():
    async with httpx.AsyncClient() as http:
        client = VaulteePipelinesClient(
            url="http://pipelines.example/graphql",
            timeout_seconds=5,
            poll_interval_ms=10,
            executor_id=1,
            http=http,
        )
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post("http://pipelines.example/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "runPipeline": {
                                "runId": "100",
                                "systemId": "sys-xyz",
                            }
                        }
                    },
                )
            )
            run_id, system_id = await client.run_pipeline(42, {"k": "v"})
            assert run_id == "100"
            assert system_id == "sys-xyz"


@pytest.mark.asyncio
async def test_wait_for_completion_done_returns():
    async with httpx.AsyncClient() as http:
        client = VaulteePipelinesClient(
            url="http://pipelines.example/graphql",
            timeout_seconds=5,
            poll_interval_ms=1,
            executor_id=1,
            http=http,
        )
        with respx.mock() as rmock:
            route = rmock.post("http://pipelines.example/graphql")
            route.side_effect = [
                httpx.Response(
                    200,
                    json={"data": {"pipelineRun": {"id": "100", "status": "run"}}},
                ),
                httpx.Response(
                    200,
                    json={"data": {"pipelineRun": {"id": "100", "status": "done"}}},
                ),
            ]
            await client.wait_for_completion("100", deadline_s=5)


@pytest.mark.asyncio
async def test_wait_for_completion_error_raises():
    async with httpx.AsyncClient() as http:
        client = VaulteePipelinesClient(
            url="http://pipelines.example/graphql",
            timeout_seconds=5,
            poll_interval_ms=1,
            executor_id=1,
            http=http,
        )
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post("http://pipelines.example/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={"data": {"pipelineRun": {"id": "100", "status": "error"}}},
                )
            )
            with pytest.raises(PipelineFailedError):
                await client.wait_for_completion("100", deadline_s=5)


@pytest.mark.asyncio
async def test_upstream_5xx_is_unavailable_error():
    async with httpx.AsyncClient() as http:
        client = VaulteePipelinesClient(
            url="http://pipelines.example/graphql",
            timeout_seconds=5,
            poll_interval_ms=1,
            executor_id=1,
            http=http,
        )
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post("http://pipelines.example/graphql").mock(
                return_value=httpx.Response(503, text="upstream down")
            )
            with pytest.raises(PipelineUnavailableError):
                await client.create_from_template(11, "x", {})
