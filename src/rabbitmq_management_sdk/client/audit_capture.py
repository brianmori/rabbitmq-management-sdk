"""Collect and replay a sanitized RabbitMQ topology audit capture."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Literal

from pydantic import AwareDatetime, ConfigDict

from rabbitmq_management_sdk.client._capture_sanitization import CaptureSanitizer
from rabbitmq_management_sdk.client.cluster_auditor import ClusterAuditor
from rabbitmq_management_sdk.exceptions import APIError, AuditCaptureError, RabbitMQError, TopologyError
from rabbitmq_management_sdk.resources.base import RabbitMQBase
from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse
from rabbitmq_management_sdk.topology.queue_configuration import captured_broker_version

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from rabbitmq_management_sdk.resources.base import Page
    from rabbitmq_management_sdk.resources.v4.admin.services import AdminManager
    from rabbitmq_management_sdk.resources.v4.exchanges.services import ExchangeManager
    from rabbitmq_management_sdk.resources.v4.queues.services import QueueManager

_MAX_PAGE_SIZE = 500
type _Endpoint = Literal["/api/definitions", "/api/queues", "/api/exchanges"]
type _Resource = QueueResponse | ExchangeResponse


class AuditCaptureRead(RabbitMQBase):
    """Path and UTC acquisition window for a Management API read."""

    model_config = ConfigDict(extra="forbid")

    path: _Endpoint
    started_at: AwareDatetime
    finished_at: AwareDatetime


class AuditCapturePage(RabbitMQBase):
    """Pagination counters reported on a single successful broker page."""

    model_config = ConfigDict(extra="forbid", strict=True)

    page: int
    page_count: int
    page_size: int
    total_count: int
    filtered_count: int
    item_count: int


class AuditCaptureListing(AuditCaptureRead):
    """Acquisition window and page evidence for an unfiltered listing."""

    requested_page_size: int
    pages: tuple[AuditCapturePage, ...]


class AuditCaptureManifest(RabbitMQBase):
    """Provenance for one completed capture, without connection credentials."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    scope: Literal["cluster"]
    cluster_id: str | None
    cluster_name: str | None
    broker_version: str | None
    broker_version_source: Literal["definitions", "overview"] | None
    definitions: AuditCaptureRead
    queues: AuditCaptureListing
    exchanges: AuditCaptureListing


class _Bundle(RabbitMQBase):
    model_config = ConfigDict(extra="forbid")

    manifest: AuditCaptureManifest
    definitions: ClusterDefinitionsResponse
    queues: tuple[QueueResponse, ...]
    exchanges: tuple[ExchangeResponse, ...]


def _check_page(
    path: _Endpoint, first: AuditCapturePage, current: AuditCapturePage, requested: int, page_size: int
) -> None:
    remaining = max(0, first.filtered_count - (requested - 1) * page_size)
    if (
        current.page != requested
        or current.page_size != page_size
        or current.page_count != (first.filtered_count + page_size - 1) // page_size
        or current.total_count != first.total_count
        or current.filtered_count != first.filtered_count
        or current.total_count < current.filtered_count
        or current.filtered_count < 0
        or current.item_count != min(page_size, remaining)
    ):
        raise AuditCaptureError(f"Inconsistent pagination metadata for {path} page {requested}")


def _check_listing(path: _Endpoint, listing: AuditCaptureListing, items: Collection[_Resource]) -> None:
    if listing.path != path or not 1 <= listing.requested_page_size <= _MAX_PAGE_SIZE or not listing.pages:
        raise AuditCaptureError(f"Invalid pagination evidence for {path}")
    first = listing.pages[0]
    for number, page in enumerate(listing.pages, 1):
        _check_page(path, first, page, number, listing.requested_page_size)
    if len(listing.pages) != max(1, first.page_count) or len(items) != first.filtered_count:
        raise AuditCaptureError(f"Incomplete pagination evidence for {path}")
    if len({(item.vhost, item.name) for item in items}) != len(items):
        raise AuditCaptureError(f"Duplicate resource identities in {path}")


def _read[T](call: Callable[[], T], path: _Endpoint, *, page: int | None = None) -> T:
    """Report the failed read without exposing broker response bodies or causes."""
    try:
        return call()
    except APIError as exc:
        failure = f"HTTP {exc.status_code}"
    except RabbitMQError as exc:
        failure = type(exc).__name__
    location = path if page is None else f"{path} page {page}"
    raise AuditCaptureError(f"Could not capture {location} ({failure})")


def _list_resources[T: _Resource](
    list_page: Callable[..., Page[T]], path: _Endpoint, page_size: int
) -> tuple[tuple[T, ...], AuditCaptureListing]:
    started_at = datetime.now(UTC)
    evidence: list[AuditCapturePage] = []
    resources: list[T] = []
    page_count = 1
    number = 1
    while number <= page_count:
        page = _read(partial(list_page, page=number, page_size=page_size), path, page=number)
        counters = AuditCapturePage(
            page=page.page,
            page_count=page.page_count,
            page_size=page.page_size,
            total_count=page.total_count,
            filtered_count=page.filtered_count,
            item_count=page.item_count,
        )
        _check_page(path, evidence[0] if evidence else counters, counters, number, page_size)
        if len(page.items) != counters.item_count:
            raise AuditCaptureError(f"Inconsistent item count for {path} page {number}")
        evidence.append(counters)
        resources.extend(page.items)
        page_count = counters.page_count
        number += 1
    listing = AuditCaptureListing(
        path=path,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        requested_page_size=page_size,
        pages=tuple(evidence),
    )
    _check_listing(path, listing, resources)
    return tuple(resources), listing


def _sanitize(bundle: _Bundle) -> _Bundle:
    try:
        sanitizer = CaptureSanitizer()
        return _Bundle.model_validate(
            {
                "manifest": bundle.manifest,
                "definitions": sanitizer.definitions(bundle.definitions),
                "queues": [sanitizer.queue(queue) for queue in bundle.queues],
                "exchanges": [sanitizer.exchange(exchange) for exchange in bundle.exchanges],
            }
        )
    except (TypeError, ValueError, RecursionError):
        pass
    raise AuditCaptureError("Could not sanitize audit capture")


class AuditCapture:
    """A sanitized set of audit inputs and their acquisition manifest.

    Obtain a capture from a client's collect_audit_capture() or load a
    saved one with AuditCapture.load().
    """

    def __init__(self, bundle: _Bundle) -> None:
        self._bundle = bundle

    @classmethod
    def _collect(
        cls,
        admin: AdminManager,
        queues: QueueManager,
        exchanges: ExchangeManager,
        *,
        page_size: int,
        overview_version: str | None,
        overview_cluster_name: str | None,
    ) -> AuditCapture:
        if type(page_size) is not int or not 1 <= page_size <= _MAX_PAGE_SIZE:
            raise AuditCaptureError(f"Capture page_size must be an integer from 1 to {_MAX_PAGE_SIZE}")
        started_at = datetime.now(UTC)
        definitions = _read(admin.export_definitions, "/api/definitions")
        definitions_read = AuditCaptureRead(
            path="/api/definitions", started_at=started_at, finished_at=datetime.now(UTC)
        )
        queue_rows, queue_read = _list_resources(queues.list_page, "/api/queues", page_size)
        exchange_rows, exchange_read = _list_resources(exchanges.list_page, "/api/exchanges", page_size)
        definitions_version, _ = captured_broker_version(definitions)
        broker_version = definitions_version or overview_version
        broker_version_source: Literal["definitions", "overview"] | None = None
        if definitions_version:
            broker_version_source = "definitions"
        elif overview_version:
            broker_version_source = "overview"
        cluster_name = definitions.cluster_name
        if cluster_name is None:
            cluster_name = definitions.original_cluster_name
        if cluster_name is None:
            cluster_name = overview_cluster_name
        manifest = AuditCaptureManifest(
            schema_version=1,
            scope="cluster",
            cluster_id=definitions.internal_cluster_id,
            cluster_name=cluster_name,
            broker_version=broker_version,
            broker_version_source=broker_version_source,
            definitions=definitions_read,
            queues=queue_read,
            exchanges=exchange_read,
        )
        bundle = _Bundle(manifest=manifest, definitions=definitions, queues=queue_rows, exchanges=exchange_rows)
        return cls(_sanitize(bundle))

    @property
    def manifest(self) -> AuditCaptureManifest:
        """Acquisition provenance for the definitions and observation pair."""
        return self._bundle.manifest

    def save(self, path: Path) -> None:
        """Publish a complete JSON bundle at a new path in an existing directory.

        The finished file becomes visible atomically. An existing file is
        preserved and an interrupted write may leave a private temporary file.

        Raises:
            AuditCaptureError: If the bundle cannot be published.
        """
        temporary_path = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=".audit-capture-", delete=False
            ) as output:
                temporary_path = Path(output.name)
                data = self._bundle.model_dump(mode="json", by_alias=True, exclude_unset=True)
                json.dump(data, output, ensure_ascii=False, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            path.hardlink_to(temporary_path)
            return
        except FileExistsError:
            message = "Audit capture destination already exists"
        except (OSError, TypeError, ValueError, RecursionError):
            message = "Could not save audit capture"
        finally:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink()
        raise AuditCaptureError(message)

    @classmethod
    def load(cls, path: Path) -> AuditCapture:
        """Open a version 1 bundle and validate the paired acquisition evidence.

        Raises:
            AuditCaptureError: If the file cannot be read or has an invalid
                schema, unsupported version, or inconsistent page evidence.
        """
        try:
            with path.open(encoding="utf-8") as source:
                bundle = _Bundle.model_validate(json.load(source))
        except (OSError, TypeError, ValueError, RecursionError):
            pass
        else:
            if bundle.manifest.definitions.path != "/api/definitions":
                raise AuditCaptureError("Invalid definitions endpoint in audit capture")
            _check_listing("/api/queues", bundle.manifest.queues, bundle.queues)
            _check_listing("/api/exchanges", bundle.manifest.exchanges, bundle.exchanges)
            return cls(_sanitize(bundle))
        raise AuditCaptureError("Could not load audit capture (expected a version 1 bundle)")

    def to_auditor(
        self,
        *,
        in_cluster_amqp_hosts: Collection[str] = (),
        cluster_label: str | None = None,
    ) -> ClusterAuditor:
        """Build an auditor from the sanitized inputs and optional local AMQP host mapping.

        Raises:
            AuditCaptureError: If the saved evidence cannot construct a topology.
        """
        try:
            return ClusterAuditor(
                self._bundle.definitions.model_copy(deep=True),
                queues=self._bundle.queues,
                exchanges=self._bundle.exchanges,
                in_cluster_amqp_hosts=in_cluster_amqp_hosts,
                cluster_label=cluster_label,
            )
        except TopologyError as exc:
            failure = type(exc).__name__
        raise AuditCaptureError(f"Could not build a topology from audit capture ({failure})")
