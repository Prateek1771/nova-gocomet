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
    otel_exporter_otlp_endpoint: str = ""
    log_level: str = "INFO"

    @property
    def jwks_url(self) -> str:
        return self.keycloak_jwks_url or f"{self.keycloak_issuer}/protocol/openid-connect/certs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
