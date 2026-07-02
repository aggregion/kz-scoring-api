from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KZ_SCORING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vaultee_pipelines_api_url: str = Field(
        default="http://vlt-system-prod-vaultee-pipelines-internal:3009",
    )
    vaultee_secrets_url: str = Field(
        default="http://vlt-system-prod-vaultee-secrets.vaultee.svc.cluster.local",
    )

    pipelines_service_subject: str = Field(default="kz-scoring-service")
    pipelines_tenant_id: str = Field(default="")

    lookup_iin_only_template_id: int = Field(default=0)
    lookup_iin_phone_template_id: int = Field(default=0)
    pipeline_executor_id: int = Field(default=0)

    @field_validator("pipelines_tenant_id")
    @classmethod
    def _tenant_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "pipelines_tenant_id must be set (env: KZ_SCORING_PIPELINES_TENANT_ID); "
                "vaultee-pipelines rejects requests without x-vaultee-tenant"
            )
        return v

    iin_salt: str = Field(default="secretsalt20260406")
    # The salt used to HMAC row_id_iin / row_id_full on this side. Points at
    # SALT_PKB on the BLN initiator (which queries the PKB-encrypted replica)
    # and at SALT_BLN on the PKB initiator (which queries the BLN-encrypted
    # replica). Field name kept for backward compatibility with the initial
    # deploy; environment name is KZ_SCORING_SALT_PKB_SECRET_TOKEN either way.
    salt_pkb_secret_token: str = Field(default="pkb_beeline/SALT_PKB")
    beeline_secrets_url_for_pipeline: str = Field(
        default="http://vlt-system-prod-vaultee-secrets.vaultee.svc.cluster.local",
    )
    # Name of the jinja context variable that the pipeline template expects
    # for the initiator's vaultee-secrets URL. The BLN-side templates
    # (`lookup_by_beeline*`) use `beeline_secrets_url`; the symmetric PKB-side
    # templates (`lookup_by_pkb*`) use `fcb_secrets_url`. Same image serves
    # both sides — deployment picks the key via env.
    pipeline_secrets_context_key: str = Field(default="beeline_secrets_url")

    timeout_seconds: float = Field(default=30.0)
    poll_interval_ms: int = Field(default=100)
    max_concurrent_lookups: int = Field(default=10)

    salt_cache_ttl_seconds: float = Field(default=300.0)

    # Static shared-secret gate for /single and /multi. Empty = disabled
    # (matches original unauthenticated behaviour). When set, callers must
    # pass the same value in the X-API-Key request header. /healthz always
    # stays open so k8s probes and external liveness monitors keep working.
    api_token: str = Field(default="")

    log_level: str = Field(default="INFO")


@lru_cache
def get_settings() -> Settings:
    return Settings()
