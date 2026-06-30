import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class VaulteeSecretsClient:
    """Fetches secrets from vaultee-secrets with an in-memory TTL cache."""

    def __init__(
        self,
        base_url: str,
        ttl_seconds: float,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._ttl = ttl_seconds
        self._http = http
        self._owns_http = http is None
        self._cache: dict[str, tuple[float, bytes]] = {}
        self._lock = asyncio.Lock()

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    async def get_secret(self, token: str) -> bytes:
        now = time.monotonic()
        cached = self._cache.get(token)
        if cached is not None and (now - cached[0]) < self._ttl:
            return cached[1]
        async with self._lock:
            cached = self._cache.get(token)
            if cached is not None and (time.monotonic() - cached[0]) < self._ttl:
                return cached[1]
            client = await self._client()
            url = f"{self._base_url}/secrets"
            resp = await client.get(url, params={"token": token})
            resp.raise_for_status()
            value = self._extract(resp)
            self._cache[token] = (time.monotonic(), value)
            return value

    @staticmethod
    def _extract(resp: httpx.Response) -> bytes:
        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            data = resp.json()
            if isinstance(data, str):
                return data.encode()
            if isinstance(data, dict):
                for key in ("value", "secret", "data"):
                    if key in data and isinstance(data[key], str):
                        return data[key].encode()
            raise ValueError(
                f"Unexpected JSON shape from vaultee-secrets: {type(data).__name__}"
            )
        return resp.content

    async def close(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None
