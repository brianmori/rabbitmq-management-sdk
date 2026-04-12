import ssl
from enum import IntEnum
from urllib.parse import quote

from pydantic import BaseModel, Field, FilePath, SecretStr, model_validator


class SSLConfig(BaseModel):
    """SSLContext for internal PKI or mutual TLS.

    Attributes:
        ca_bundle: Path to a custom CA bundle (.pem) for internal PKI.
        client_cert: Tuple of (cert_path, key_path) for mTLS.
        verify: Set to False to disable verification entirely (dev only).
        min_version: Minimum TLS version. Defaults to TLSv1.2.
    """

    verify: bool = True
    # FilePath validates that the file actually exists on the system
    ca_bundle: FilePath | None = None
    # Tuple of (cert_file, key_file)
    client_cert: tuple[FilePath, FilePath] | None = None
    min_version: ssl.TLSVersion = Field(
        default=ssl.TLSVersion.TLSv1_2, description="Minimum TLS version. Defaults to TLSv1.2."
    )


class Config(BaseModel):
    """Configuration for the RabbitMQ management API client.

    Attributes:
         host: Hostname or IP of the RabbitMQ node.
         port: Management API port. Defaults to 15672 (plain).
         username: RabbitMQ management user.
         password: Password for the management user.
         ssl_context: TLS settings. When None the connection uses plain HTTP.
         virtual_host: Default virtual host for vhost-scoped API calls. Defaults to '/'.
         version_override: Pin a specific RabbitMQ version (e.g. '4.3.0') instead of relying on automatic detection.
    """

    host: str = Field(description="Hostname or IP of the RabbitMQ node.")
    port: int = Field(
        default=15672,
        gt=1024,
        lt=65536,
        description="Management API port. Defaults to 15672 (plain).",
    )

    username: str | None = Field(description="RabbitMQ management user.")
    password: SecretStr | None = Field(description="Password for the management user.")

    ssl_context: SSLConfig | None = Field(
        default=None,
        description="TLS settings. When None the connection uses plain HTTP.",
    )

    virtual_host: str = Field(
        default="/",
        description="Defaults to '/', the RabbitMQ built-in virtual host.",
    )

    @property
    def virtual_host_safe(self) -> str:
        """The automatically encoded vhost for API calls."""
        return quote(self.virtual_host, safe="")

    strict: bool = Field(
        default=False,
        description="If True, all default arguments are sent explicitly to RabbitMQ. "
        "Note: Enabling this on existing queues will cause a 406 Precondition Failed "
        "conflict if they were originally created without explicit defaults.",
    )

    version_override: RabbitMQVersion | None = Field(
        default=None,
        description=(
            "Pin a specific RabbitMQ version string (e.g. '4.3.0') instead of "
            "relying on automatic detection. Useful when the management API is "
            "behind a proxy that strips version headers."
        ),
    )

    @property
    def base_url(self) -> str:
        """The base URL for API calls."""
        scheme = "https" if self.ssl_context is not None else "http"
        return f"{scheme}://{self.host}:{self.port}/api"

    @model_validator(mode="after")
    def validate_config(self) -> Config:
        has_basic_auth = self.username is not None or self.password is not None
        has_mtls = self.ssl_context is not None and self.ssl_context.client_cert is not None

        if not (has_basic_auth or has_mtls):
            raise ValueError("Provide either username/password or a client certificate.")

        if has_basic_auth and has_mtls:
            raise ValueError("Choose Basic Auth (username/password) or mTLS (client_cert). ")

        return self


class RabbitMQVersion(BaseModel):
    """Semantic version of the RabbitMQ server."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, version: str) -> RabbitMQVersion:
        try:
            major, minor, patch = version.split(".")
            return cls(major=int(major), minor=int(minor), patch=int(patch))
        except ValueError:
            raise ValueError(f"Unrecognised RabbitMQ version string: {version!r}") from None

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


class RabbitMQMajorVersion(IntEnum):
    V4 = 4
    V5 = 5
