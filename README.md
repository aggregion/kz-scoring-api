# kz-scoring-api

Synchronous REST facade in front of the Beeline-initiator PKB lookup pipelines
in [`aggregion/kz-scoring`](https://github.com/aggregion/kz-scoring). Callers
issue a plain `GET /single?iin=…` or `POST /multi` and the service drives the
underlying `lookup_by_beeline` / `lookup_by_beeline_uniq` pipelines through
vaultee-pipelines, waits for completion, and returns the decrypted plain
payload as JSON.

Implements [AGG-97](https://github.com/aggregion/kz-scoring/issues); depends on
[AGG-96](https://github.com/aggregion/kz-scoring/issues) for the registered
pipeline templates in vaultee-pipelines.

## Endpoints

### `GET /single`

| param | required | format                                    |
| ----- | -------- | ----------------------------------------- |
| iin   | yes      | 12 digits (e.g. `801217301434`)           |
| phone | no       | msisdn without `+` (e.g. `7000000028`)    |

Response: JSON array of feature objects.

- Without `phone`: 0..N rows (one per SIM/SUBS_KEY on file for that IIN).
- With `phone`: 0 or 1 row.

Status codes:

| code | meaning                                                              |
| ---- | -------------------------------------------------------------------- |
| 200  | success — array (may be empty `[]` if the IIN/phone is unknown)      |
| 408  | upstream pipeline did not complete within `timeout_seconds`          |
| 422  | request validation failed                                            |
| 502  | vaultee-pipelines unreachable / 5xx                                  |
| 500  | pipeline ran but ended with `status=error` / unexpected error        |

### `POST /multi`

Body: JSON array `[{"iin": "...", "phone": "..."}, ...]` (`phone` optional per item).

Response: JSON array of arrays. Position `i` of the response matches position
`i` of the input. Each element is either a feature-row array (as in `/single`)
or a per-item error object `{"error": "...", "message": "..."}`.

Status codes:

| code | meaning                                                                                                 |
| ---- | ------------------------------------------------------------------------------------------------------- |
| 200  | every lookup succeeded                                                                                  |
| 207  | partial success — at least one lookup failed; per-item error objects in the response                    |
| 422  | request validation failed                                                                               |
| 502  | every lookup failed because vaultee-pipelines is unreachable                                            |

Lookups inside one `/multi` request are dispatched concurrently with a
configurable cap (`max_concurrent_lookups`).

## Request flow

1. API receives the request, validates the IIN / phone.
2. API fetches `SALT_PKB` from `vaultee-secrets` (`pkb_beeline/SALT_PKB`) once
   per pod and caches it for `salt_cache_ttl_seconds`.
3. API computes `row_id_iin = HMAC-SHA256(SALT_PKB, sha256(iin + IIN_SALT))`
   or `row_id_full = HMAC-SHA256(SALT_PKB, sha256(iin + IIN_SALT) || "|" || phone)`.
   This matches `aggregion/kz-scoring/pipelines/pkb_beeline/templates/lookup_by_beeline*`.
4. API calls vaultee-pipelines GraphQL:
   - `createFromTemplate(templateId, executorId, name, context)`
   - `runPipeline(pipelineId, context)` → `{ runId, systemId }`
   - poll `pipelineRun(id)` until `status` is terminal (`done` / `error` /
     `aborted`) or `timeout_seconds` is exceeded
   - on `done`, read `pipelineRun.resultJson` (the `publish_to_session`
     payload), parse as TSV
5. Return the decoded rows.

> The exact wire format of the result-fetch query is centralised in
> [`pipeline_client.py`](src/kz_scoring_api/pipeline_client.py) as
> `PIPELINE_RUN_RESULT_QUERY`. AGG-96 finalises the canonical
> `resultJson` surface on `pipelineRun`; until then the query is the only place
> to swap if the actual field name changes.

## Configuration

All settings are env vars with the `KZ_SCORING_` prefix; defaults are in
[`src/kz_scoring_api/config.py`](src/kz_scoring_api/config.py).

| env var                                          | default                                                                                  |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| `KZ_SCORING_VAULTEE_PIPELINES_URL`               | `http://vlt-system-prod-vaultee-pipelines.vaultee.svc.cluster.local:3008/graphql`        |
| `KZ_SCORING_VAULTEE_SECRETS_URL`                 | `http://vlt-system-prod-vaultee-secrets.vaultee.svc.cluster.local`                       |
| `KZ_SCORING_BEELINE_SECRETS_URL_FOR_PIPELINE`    | same as above (forwarded to the pipeline `context.beeline_secrets_url`)                  |
| `KZ_SCORING_SALT_PKB_SECRET_TOKEN`               | `pkb_beeline/SALT_PKB`                                                                   |
| `KZ_SCORING_IIN_SALT`                            | `secretsalt20260406`                                                                     |
| `KZ_SCORING_LOOKUP_IIN_ONLY_TEMPLATE_ID`         | — (provided by Денис, AGG-96)                                                            |
| `KZ_SCORING_LOOKUP_IIN_PHONE_TEMPLATE_ID`        | — (provided by Денис, AGG-96)                                                            |
| `KZ_SCORING_PIPELINE_EXECUTOR_ID`                | — (vaultee-pipelines DDM executor id)                                                    |
| `KZ_SCORING_TIMEOUT_SECONDS`                     | `30`                                                                                     |
| `KZ_SCORING_POLL_INTERVAL_MS`                    | `100`                                                                                    |
| `KZ_SCORING_MAX_CONCURRENT_LOOKUPS`              | `10`                                                                                     |
| `KZ_SCORING_SALT_CACHE_TTL_SECONDS`              | `300`                                                                                    |
| `KZ_SCORING_LOG_LEVEL`                           | `INFO`                                                                                   |
| `KZ_SCORING_HOST` / `KZ_SCORING_PORT`            | `0.0.0.0` / `8000`                                                                       |

## Running locally

```bash
pip install -e '.[dev]'
pytest -q
python -m kz_scoring_api
# OpenAPI: http://127.0.0.1:8000/docs
```

## Docker

```bash
docker build -t kz-scoring-api .
docker run --rm -p 8000:8000 \
  -e KZ_SCORING_LOOKUP_IIN_ONLY_TEMPLATE_ID=… \
  -e KZ_SCORING_LOOKUP_IIN_PHONE_TEMPLATE_ID=… \
  -e KZ_SCORING_PIPELINE_EXECUTOR_ID=… \
  -e KZ_SCORING_VAULTEE_PIPELINES_URL=… \
  -e KZ_SCORING_VAULTEE_SECRETS_URL=… \
  kz-scoring-api
```

CI publishes `ghcr.io/aggregion/kz-scoring-api` on every push to `main` and
every tagged release (`v*`).

## Helm chart

The chart in [`chart/`](chart/) ships a `Deployment`, `Service`, optional
`Ingress` (disabled by default), `ServiceAccount`, and a `ConfigMap` that
materialises the env vars above. Tweak everything via `values.yaml`:

```bash
helm template kz-scoring-api chart \
  --set config.pipelines.lookup_iin_only_template_id=42 \
  --set config.pipelines.lookup_iin_phone_template_id=43 \
  --set config.pipelines.executor_id=1
```

The Argo Application that wires the chart into the Beeline cluster lives in
[`aggregion/kartel-deploy`](https://github.com/aggregion/kartel-deploy) under
`argo/overlays/vaultee/` and is set up separately by DevOps once the chart
is green.

## Not in scope

- AuthN / TLS — handled by external ingress.
- Mirror PKB-side service (`lookup_by_pkb*` templates) — separate repo, later.
- RA-TLS / TEE attestation — AGG-15.
- Load testing.
