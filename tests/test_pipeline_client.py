import httpx
import pytest
import respx

from kz_scoring_api.pipeline_client import (
    PipelineFailedError,
    PipelineUnavailableError,
    VaulteePipelinesClient,
)

TEST_SUBJECT = "kz-scoring-service"
TEST_TENANT = "e4df8d0e-970a-4e83-8d65-787aab057969"


def _make_client(http: httpx.AsyncClient, **overrides) -> VaulteePipelinesClient:
    kwargs = dict(
        url="http://pipelines.example/graphql",
        timeout_seconds=5,
        poll_interval_ms=10,
        executor_id=7,
        service_subject=TEST_SUBJECT,
        tenant_id=TEST_TENANT,
        http=http,
    )
    kwargs.update(overrides)
    return VaulteePipelinesClient(**kwargs)


@pytest.mark.asyncio
async def test_create_from_template_returns_id():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
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
        client = _make_client(http, executor_id=1)
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
        client = _make_client(http, executor_id=1, poll_interval_ms=1)
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
        client = _make_client(http, executor_id=1, poll_interval_ms=1)
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
        client = _make_client(http, executor_id=1, poll_interval_ms=1)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post("http://pipelines.example/graphql").mock(
                return_value=httpx.Response(503, text="upstream down")
            )
            with pytest.raises(PipelineUnavailableError):
                await client.create_from_template(11, "x", {})


def _assert_identity_headers(request: httpx.Request) -> None:
    assert request.headers.get("x-auth-subject") == TEST_SUBJECT
    assert request.headers.get("x-vaultee-tenant") == TEST_TENANT


@pytest.mark.asyncio
async def test_create_from_template_sends_identity_headers():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.post("http://pipelines.example/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={"data": {"createFromTemplate": {"id": 42}}},
                )
            )
            await client.create_from_template(
                template_id=11, name="x", context={"row_id_iin": "abc"}
            )
            assert route.called
            _assert_identity_headers(route.calls.last.request)


@pytest.mark.asyncio
async def test_run_pipeline_sends_identity_headers():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.post("http://pipelines.example/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "runPipeline": {"runId": "100", "systemId": "sys-xyz"}
                        }
                    },
                )
            )
            await client.run_pipeline(42, {"k": "v"})
            assert route.called
            _assert_identity_headers(route.calls.last.request)


@pytest.mark.asyncio
async def test_wait_for_completion_sends_identity_headers():
    async with httpx.AsyncClient() as http:
        client = _make_client(http, poll_interval_ms=1)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.post("http://pipelines.example/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={"data": {"pipelineRun": {"id": "100", "status": "done"}}},
                )
            )
            await client.wait_for_completion("100", deadline_s=5)
            assert route.called
            _assert_identity_headers(route.calls.last.request)


@pytest.mark.asyncio
async def test_fetch_result_sends_identity_headers():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.post("http://pipelines.example/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "pipelineRun": {
                                "id": "100",
                                "status": "done",
                                "resultJson": "col\nval\n",
                            }
                        }
                    },
                )
            )
            result = await client.fetch_result("100")
            assert result == "col\nval\n"
            assert route.called
            _assert_identity_headers(route.calls.last.request)


@pytest.mark.asyncio
async def test_all_calls_use_same_tenant_id():
    """Regression: runPipeline resolver checks pipeline.tenantId === ctx.tenantId,
    so every request in the create → run → poll → result cycle must carry the
    same x-vaultee-tenant. See AGG-101 / AGG-100.
    """
    async with httpx.AsyncClient() as http:
        client = _make_client(http, poll_interval_ms=1)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.post("http://pipelines.example/graphql")
            route.side_effect = [
                httpx.Response(
                    200,
                    json={"data": {"createFromTemplate": {"id": 42}}},
                ),
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "runPipeline": {"runId": "100", "systemId": "sys-xyz"}
                        }
                    },
                ),
                httpx.Response(
                    200,
                    json={"data": {"pipelineRun": {"id": "100", "status": "done"}}},
                ),
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "pipelineRun": {
                                "id": "100",
                                "status": "done",
                                "resultJson": "col\nval\n",
                            }
                        }
                    },
                ),
            ]

            await client.create_from_template(11, "x", {"row_id_iin": "abc"})
            await client.run_pipeline(42, {"k": "v"})
            await client.wait_for_completion("100", deadline_s=5)
            await client.fetch_result("100")

            assert len(route.calls) == 4
            tenants = {
                call.request.headers.get("x-vaultee-tenant") for call in route.calls
            }
            subjects = {
                call.request.headers.get("x-auth-subject") for call in route.calls
            }
            assert tenants == {TEST_TENANT}
            assert subjects == {TEST_SUBJECT}
