from urllib.parse import quote

from pydantic import BaseModel, Field, FilePath, SecretStr, computed_field, model_validator


class SSLConfig(BaseModel):
    """
    SSLContext for internal PKI or mutual TLS.
    ca_bundle: Path to a custom CA bundle (.pem) for internal PKI.
    client_cert: Tuple of (cert_path, key_path) for mTLS.
    verify: Set to False to disable verification entirely (dev only).
    """

    verify: bool = True
    # FilePath validates that the file actually exists on the system
    ca_bundle: FilePath | None = None
    # Tuple of (cert_file, key_file)
    client_cert: tuple[FilePath, FilePath] | None = None


class Config(BaseModel):
    host: str = Field(description="Hostname or IP of the RabbitMQ node.")
    port: int = Field(
        default=15672,
        gt=1024,
        lt=65536,
        description="Management API port. Defaults to 15672 (plain) or 15671 (TLS).",
    )

    username: str | None = Field(description="RabbitMQ management user.")
    password: SecretStr | None = Field(description="Password for the management user.")

    ssl_context: SSLConfig | None = Field(
        default=None,
        description="TLS settings. When None the connection uses plain HTTP.",
    )

    raw_virtual_host: str = Field(
        default="/",
        description=(
            "Default virtual host for vhost-scoped API callsDefaults to '/', the RabbitMQ built-in virtual host."
        ),
    )

    @computed_field
    @property
    def vhost(self) -> str:
        """The automatically encoded vhost for API calls."""
        return quote(self.raw_virtual_host, safe="")

    version_override: str | None = Field(
        default=None,
        description=(
            "Pin a specific RabbitMQ version string (e.g. '3.13.1') instead of "
            "relying on automatic detection. Useful when the management API is "
            "behind a proxy that strips version headers."
        ),
    )

    @property
    def base_url(self) -> str:
        scheme = "https" if self.ssl_context is not None else "http"
        return f"{scheme}://{self.host}:{self.port}/api"

    @model_validator(mode="after")
    def validate(self) -> Config:
        # check if basic auth and mtls are both set, which is not allowed
        return self
