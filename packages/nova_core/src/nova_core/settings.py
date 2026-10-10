from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    # runtime connects as nova_app (non-owner, RLS applies); migrations as nova_owner
    database_url: str = "postgresql+asyncpg://nova_app:nova_app@localhost:5433/nova"
    migrations_database_url: str = "postgresql+asyncpg://nova_owner:nova_owner@localhost:5433/nova"
    keycloak_issuer: str = "http://localhost:8180/realms/nova"
    # where the API fetches JWKS; differs from issuer inside compose (keycloak:8080 vs localhost:8180)
    keycloak_jwks_url: str = ""
    oidc_audience: str = "nova-api"
    oidc_azp: str = "nova-web"
    openfga_url: str = "http://localhost:8081"
    openfga_model_path: str = ""  # default: infra/openfga/model.json in the repo
    temporal_host: str = "localhost:7233"
    otel_exporter_otlp_endpoint: str = ""
    # object storage (MinIO / any S3); keys are always tenant/{tenant_id}/...
    s3_endpoint: str = "localhost:9100"
    s3_access_key: str = "nova"
    s3_secret_key: str = "nova-dev-secret"  # noqa: S105 (dev default; env overrides)
    s3_bucket: str = "nova"
    s3_secure: bool = False
    # LiteLLM proxy (OpenAI-compatible); code only names aliases (CLAUDE.md rule 6)
    llm_base_url: str = "http://localhost:4100"
    llm_api_key: str = "sk-nova-dev"
    # derives each tenant's LiteLLM virtual key (nova_core.llm_keys); empty = every call uses llm_api_key
    llm_key_secret: str = ""
    llm_mode: str = "free"  # local | free | cheap | demo (picks the LiteLLM config; free = $0 models)
    # decisions-model id for the gateway's /jev/decisions pass-through; config, not code (rule 6)
    jev_model: str = ""
    max_upload_mb: int = 20
    max_pages: int = 20
    log_level: str = "INFO"

    @property
    def jwks_url(self) -> str:
        return self.keycloak_jwks_url or f"{self.keycloak_issuer}/protocol/openid-connect/certs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
