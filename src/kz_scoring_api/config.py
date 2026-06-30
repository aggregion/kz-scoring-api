from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KZ_SCORING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vaultee_pipelines_url: str = Field(
        default="http://vlt-system-prod-vaultee-pipelines.vaultee.svc.cluster.local:3008/graphql",
    )
    vaultee_secrets_url: str = Field(
        default="http://vlt-system-prod-vaultee-secrets.vaultee.svc.cluster.local",
    )

    lookup_iin_only_template_id: int = Field(default=0)
    lookup_iin_phone_template_id: int = Field(default=0)
    pipeline_executor_id: int = Field(default=0)

    iin_salt: str = Field(default="secretsalt20260406")
    salt_pkb_secret_token: str = Field(default="pkb_beeline/SALT_PKB")
    beeline_secrets_url_for_pipeline: str = Field(
        default="http://vlt-system-prod-vaultee-secrets.vaultee.svc.cluster.local",
    )

    timeout_seconds: float = Field(default=30.0)
    poll_interval_ms: int = Field(default=100)
    max_concurrent_lookups: int = Field(default=10)

    salt_cache_ttl_seconds: float = Field(default=300.0)

    log_level: str = Field(default="INFO")


@lru_cache
def get_settings() -> Settings:
    return Settings()
