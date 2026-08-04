import json

import httpx
import pytest
import respx

from kz_scoring_api.pipeline_client import (
    PipelineError,
    PipelineFailedError,
    PipelineTimeoutError,
    PipelineUnavailableError,
    VaulteePipelinesClient,
)

TEST_SUBJECT = "kz-scoring-service"
TEST_TENANT = "e4df8d0e-970a-4e83-8d65-787aab057969"
BASE_URL = "http://pipelines.example"


def _make_client(http: httpx.AsyncClient, **overrides) -> VaulteePipelinesClient:
    kwargs = dict(
        url=BASE_URL,
        timeout_seconds=5,
        poll_interval_ms=10,
        executor_id=7,
        service_subject=TEST_SUBJECT,
        tenant_id=TEST_TENANT,
        http=http,
    )
    kwargs.update(overrides)
    return VaulteePipelinesClient(**kwargs)


def _payload(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


def _assert_identity_headers(request: httpx.Request) -> None:
    assert request.headers.get("x-auth-subject") == TEST_SUBJECT
    assert request.headers.get("x-vaultee-tenant") == TEST_TENANT


@pytest.mark.asyncio
async def test_create_from_template_returns_id():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.post(
                f"{BASE_URL}/api/pipeline/createFromTemplate"
            ).mock(return_value=httpx.Response(201, json={"id": 42}))
            pid = await client.create_from_template(
                template_id=11, name="x", context={"row_id_iin": "abc"}
            )
            assert pid == 42
            payload = _payload(route.calls.last.request)
            assert payload == {
                "templateId": 11,
                "executorId": 7,
                "name": "x",
                "context": {"row_id_iin": "abc"},
            }


@pytest.mark.asyncio
async def test_run_pipeline_returns_run_and_system_id():
    async with httpx.AsyncClient() as http:
        client = _make_client(http, executor_id=1)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.post(f"{BASE_URL}/api/pipeline/run").mock(
                return_value=httpx.Response(
                    201, json={"runId": "17", "systemId": "sess-x"}
                )
            )
            run_id, system_id = await client.run_pipeline(42, {"k": "v"})
            assert run_id == "17"
            assert isinstance(run_id, str)
            assert system_id == "sess-x"
            payload = _payload(route.calls.last.request)
            assert payload == {"pipelineId": 42, "context": {"k": "v"}}


@pytest.mark.asyncio
async def test_wait_for_completion_done_returns():
    async with httpx.AsyncClient() as http:
        client = _make_client(http, executor_id=1, poll_interval_ms=1)
        with respx.mock() as rmock:
            route = rmock.get(f"{BASE_URL}/api/pipeline-run/100")
            route.side_effect = [
                httpx.Response(
                    200,
                    json={"runId": "100", "systemId": "s", "status": "run"},
                ),
                httpx.Response(
                    200,
                    json={"runId": "100", "systemId": "s", "status": "done"},
                ),
            ]
            await client.wait_for_completion("100", deadline_s=5)
            assert route.call_count == 2


@pytest.mark.asyncio
async def test_wait_for_completion_error_raises():
    async with httpx.AsyncClient() as http:
        client = _make_client(http, executor_id=1, poll_interval_ms=1)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.get(f"{BASE_URL}/api/pipeline-run/100").mock(
                return_value=httpx.Response(
                    200,
                    json={"runId": "100", "systemId": "s", "status": "error"},
                )
            )
            with pytest.raises(PipelineFailedError):
                await client.wait_for_completion("100", deadline_s=5)


@pytest.mark.asyncio
async def test_wait_for_completion_aborted_raises():
    async with httpx.AsyncClient() as http:
        client = _make_client(http, executor_id=1, poll_interval_ms=1)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.get(f"{BASE_URL}/api/pipeline-run/100").mock(
                return_value=httpx.Response(
                    200,
                    json={"runId": "100", "systemId": "s", "status": "aborted"},
                )
            )
            with pytest.raises(PipelineFailedError):
                await client.wait_for_completion("100", deadline_s=5)


@pytest.mark.asyncio
async def test_wait_for_completion_times_out():
    async with httpx.AsyncClient() as http:
        client = _make_client(http, executor_id=1, poll_interval_ms=1)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.get(f"{BASE_URL}/api/pipeline-run/100").mock(
                return_value=httpx.Response(
                    200,
                    json={"runId": "100", "systemId": "s", "status": "run"},
                )
            )
            with pytest.raises(PipelineTimeoutError):
                await client.wait_for_completion("100", deadline_s=0.05)


@pytest.mark.asyncio
async def test_upstream_5xx_is_unavailable_error():
    async with httpx.AsyncClient() as http:
        client = _make_client(http, executor_id=1, poll_interval_ms=1)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post(f"{BASE_URL}/api/pipeline/createFromTemplate").mock(
                return_value=httpx.Response(503, text="upstream down")
            )
            with pytest.raises(PipelineUnavailableError):
                await client.create_from_template(11, "x", {})


@pytest.mark.asyncio
async def test_transport_error_is_unavailable_error():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post(f"{BASE_URL}/api/pipeline/createFromTemplate").mock(
                side_effect=httpx.ConnectError("boom")
            )
            with pytest.raises(PipelineUnavailableError):
                await client.create_from_template(11, "x", {})


@pytest.mark.asyncio
async def test_upstream_4xx_is_pipeline_error_with_nestjs_message():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post(f"{BASE_URL}/api/pipeline/run").mock(
                return_value=httpx.Response(
                    404,
                    json={
                        "statusCode": 404,
                        "message": "Pipeline with ID 42 not found",
                        "error": "Not Found",
                    },
                )
            )
            with pytest.raises(PipelineError) as excinfo:
                await client.run_pipeline(42, {})
            msg = str(excinfo.value)
            assert "404" in msg
            assert "Pipeline with ID 42 not found" in msg
            assert "Not Found" in msg


@pytest.mark.asyncio
async def test_upstream_4xx_handles_message_list():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post(f"{BASE_URL}/api/pipeline/run").mock(
                return_value=httpx.Response(
                    400,
                    json={
                        "statusCode": 400,
                        "message": [
                            "pipelineId must be an integer",
                            "pipelineId must not be less than 1",
                        ],
                        "error": "Bad Request",
                    },
                )
            )
            with pytest.raises(PipelineError) as excinfo:
                await client.run_pipeline(0, {})
            assert "pipelineId must be an integer" in str(excinfo.value)


@pytest.mark.asyncio
async def test_forbidden_wrong_tenant_is_pipeline_error():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post(f"{BASE_URL}/api/pipeline/createFromTemplate").mock(
                return_value=httpx.Response(
                    403,
                    json={
                        "statusCode": 403,
                        "message": "User context is not set",
                        "error": "Forbidden",
                    },
                )
            )
            with pytest.raises(PipelineError):
                await client.create_from_template(11, "x", {})


@pytest.mark.asyncio
async def test_create_from_template_sends_identity_headers():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.post(
                f"{BASE_URL}/api/pipeline/createFromTemplate"
            ).mock(return_value=httpx.Response(201, json={"id": 42}))
            await client.create_from_template(
                template_id=11, name="x", context={"row_id_iin": "abc"}
            )
            _assert_identity_headers(route.calls.last.request)


@pytest.mark.asyncio
async def test_run_pipeline_sends_identity_headers():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.post(f"{BASE_URL}/api/pipeline/run").mock(
                return_value=httpx.Response(
                    201, json={"runId": "100", "systemId": "sys-xyz"}
                )
            )
            await client.run_pipeline(42, {"k": "v"})
            _assert_identity_headers(route.calls.last.request)


@pytest.mark.asyncio
async def test_wait_for_completion_sends_identity_headers():
    async with httpx.AsyncClient() as http:
        client = _make_client(http, poll_interval_ms=1)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.get(f"{BASE_URL}/api/pipeline-run/100").mock(
                return_value=httpx.Response(
                    200,
                    json={"runId": "100", "systemId": "s", "status": "done"},
                )
            )
            await client.wait_for_completion("100", deadline_s=5)
            _assert_identity_headers(route.calls.last.request)


@pytest.mark.asyncio
async def test_fetch_result_sends_identity_headers_and_reads_resultjson():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.get(f"{BASE_URL}/api/pipeline-run/100").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "runId": "100",
                        "systemId": "s",
                        "status": "done",
                        "resultJson": "col\nval\n",
                    },
                )
            )
            result = await client.fetch_result("100")
            assert result == "col\nval\n"
            _assert_identity_headers(route.calls.last.request)


@pytest.mark.asyncio
async def test_fetch_result_unwraps_json_encoded_tsv():
    # DDM publish_to_session (json + extract single_value) writes the TSV as a
    # JSON-encoded string. vaultee-pipelines forwards it verbatim, so we get
    # one layer of quoting/escaping on top of the actual TSV. fetch_result
    # must unwrap it so parse_tsv sees a plain multiline string.
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.get(f"{BASE_URL}/api/pipeline-run/100").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "runId": "100",
                        "systemId": "s",
                        "status": "done",
                        # A JSON-encoded string, mirroring what
                        # publish_to_session actually writes.
                        "resultJson": '"col_a\\tcol_b\\n1\\t2\\n"',
                    },
                )
            )
            result = await client.fetch_result("100")
            assert result == "col_a\tcol_b\n1\t2\n"


@pytest.mark.asyncio
async def test_fetch_result_passes_through_plain_tsv():
    # Backwards-compat: if the payload isn't JSON-quoted (legacy shape / other
    # publisher), leave it alone.
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.get(f"{BASE_URL}/api/pipeline-run/100").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "runId": "100",
                        "systemId": "s",
                        "status": "done",
                        "resultJson": "col\nval\n",
                    },
                )
            )
            result = await client.fetch_result("100")
            assert result == "col\nval\n"


@pytest.mark.asyncio
async def test_fetch_result_returns_empty_when_missing():
    async with httpx.AsyncClient() as http:
        client = _make_client(http)
        with respx.mock(assert_all_called=True) as rmock:
            rmock.get(f"{BASE_URL}/api/pipeline-run/100").mock(
                return_value=httpx.Response(
                    200,
                    json={"runId": "100", "systemId": "s", "status": "done"},
                )
            )
            result = await client.fetch_result("100")
            assert result == ""


@pytest.mark.asyncio
async def test_all_calls_use_same_tenant_id():
    """Regression: the internal REST controllers enforce
    pipeline.tenantId === ctx.tenantId (see AGG-101 / AGG-100), so every
    request in the create -> run -> poll -> result cycle must carry the same
    x-vaultee-tenant header.
    """
    async with httpx.AsyncClient() as http:
        client = _make_client(http, poll_interval_ms=1)
        with respx.mock(assert_all_called=True) as rmock:
            create = rmock.post(
                f"{BASE_URL}/api/pipeline/createFromTemplate"
            ).mock(return_value=httpx.Response(201, json={"id": 42}))
            run = rmock.post(f"{BASE_URL}/api/pipeline/run").mock(
                return_value=httpx.Response(
                    201, json={"runId": "100", "systemId": "sys-xyz"}
                )
            )
            get = rmock.get(f"{BASE_URL}/api/pipeline-run/100").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "runId": "100",
                        "systemId": "sys-xyz",
                        "status": "done",
                        "resultJson": "col\nval\n",
                    },
                )
            )

            await client.create_from_template(11, "x", {"row_id_iin": "abc"})
            run_id, _ = await client.run_pipeline(42, {"k": "v"})
            await client.wait_for_completion(run_id, deadline_s=5)
            await client.fetch_result(run_id)

            all_requests = (
                [c.request for c in create.calls]
                + [c.request for c in run.calls]
                + [c.request for c in get.calls]
            )
            tenants = {
                req.headers.get("x-vaultee-tenant") for req in all_requests
            }
            subjects = {
                req.headers.get("x-auth-subject") for req in all_requests
            }
            assert tenants == {TEST_TENANT}
            assert subjects == {TEST_SUBJECT}


@pytest.mark.asyncio
async def test_base_url_trailing_slash_is_trimmed():
    async with httpx.AsyncClient() as http:
        client = _make_client(http, url=f"{BASE_URL}/")
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post(f"{BASE_URL}/api/pipeline/createFromTemplate").mock(
                return_value=httpx.Response(201, json={"id": 42})
            )
            pid = await client.create_from_template(11, "x", {})
            assert pid == 42
