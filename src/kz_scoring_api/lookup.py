import asyncio
import logging
import uuid
from typing import Any

from .config import Settings
from .hashing import compute_row_id_full, compute_row_id_iin
from .pipeline_client import (
    PipelineFailedError,
    PipelineTimeoutError,
    PipelineUnavailableError,
    VaulteePipelinesClient,
)
from .secrets import VaulteeSecretsClient
from .tsv import parse_tsv

logger = logging.getLogger(__name__)


# Shape returned to the caller for a single (iin, phone?) pair.
# - dict:        phone was supplied AND CH-lookup returned a row
# - list[dict]:  phone was NOT supplied AND CH-lookup returned 1+ rows
# - None:        CH-lookup returned zero rows (not-found), regardless of phone
LookupResult = list[dict[str, Any]] | dict[str, Any] | None


class LookupService:
    def __init__(
        self,
        settings: Settings,
        pipelines: VaulteePipelinesClient,
        secrets: VaulteeSecretsClient,
    ) -> None:
        self._settings = settings
        self._pipelines = pipelines
        self._secrets = secrets
        self._sem = asyncio.Semaphore(max(1, settings.max_concurrent_lookups))

    async def _salt_pkb(self) -> bytes:
        # vaultee-secrets returns SALT_PKB as an ASCII hex string (64 chars for
        # a 32-byte salt). rebuild-encrypt in DDM (pkb_beeline/scripts/encrypt
        # /main.py::read_hex_secret) decodes it to raw bytes before using it as
        # the HMAC key. If we pass the raw ASCII response instead, HMAC uses a
        # different key and every lookup misses the replica.
        raw = await self._secrets.get_secret(self._settings.salt_pkb_secret_token)
        return bytes.fromhex(raw.decode("ascii").strip())

    def _template_id(self, has_phone: bool) -> int:
        return (
            self._settings.lookup_iin_phone_template_id
            if has_phone
            else self._settings.lookup_iin_only_template_id
        )

    async def _run_one(self, iin: str, phone: str | None) -> LookupResult:
        salt_pkb = await self._salt_pkb()
        if phone is None:
            row_id = compute_row_id_iin(salt_pkb, iin, self._settings.iin_salt)
            context = {
                "row_id_iin": row_id,
                "beeline_secrets_url": self._settings.beeline_secrets_url_for_pipeline,
            }
            template_id = self._template_id(has_phone=False)
        else:
            row_id = compute_row_id_full(
                salt_pkb, iin, phone, self._settings.iin_salt
            )
            context = {
                "row_id_full": row_id,
                "beeline_secrets_url": self._settings.beeline_secrets_url_for_pipeline,
            }
            template_id = self._template_id(has_phone=True)

        if template_id <= 0:
            raise RuntimeError(
                "Pipeline template id is not configured "
                "(lookup_iin_only_template_id / lookup_iin_phone_template_id)"
            )

        name = f"kz-scoring-api/{uuid.uuid4()}"
        pipeline_id = await self._pipelines.create_from_template(
            template_id, name, context
        )
        run_id, _system_id = await self._pipelines.run_pipeline(
            pipeline_id, context
        )
        await self._pipelines.wait_for_completion(
            run_id, deadline_s=self._settings.timeout_seconds
        )
        payload = await self._pipelines.fetch_result(run_id)
        rows = parse_tsv(payload)
        return self._shape(rows, has_phone=phone is not None)

    @staticmethod
    def _shape(rows: list[dict[str, Any]], has_phone: bool) -> LookupResult:
        if not rows:
            return None
        if has_phone:
            # /single with phone (or /multi item with phone): 0 or 1 row expected.
            # If the pipeline ever returns >1, drop the extras — the (iin, phone)
            # pair is a uniqueness key on the replica, so more than one row is a
            # data-shape violation, not something the API should widen its
            # contract for.
            return rows[0]
        return rows

    async def lookup(self, iin: str, phone: str | None) -> LookupResult:
        async with self._sem:
            return await self._run_one(iin, phone)

    async def lookup_many(
        self, items: list[tuple[str, str | None]]
    ) -> list[LookupResult | Exception]:
        async def _safe(iin: str, phone: str | None):
            try:
                return await self.lookup(iin, phone)
            except (
                PipelineTimeoutError,
                PipelineUnavailableError,
                PipelineFailedError,
            ) as exc:
                return exc
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected lookup error")
                return exc

        return await asyncio.gather(*(_safe(i, p) for i, p in items))
