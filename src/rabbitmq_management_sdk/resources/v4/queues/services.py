from __future__ import annotations

from http import HTTPMethod
from typing import TYPE_CHECKING, Any

from rabbitmq_management_sdk.resources.base import Page, parse_one, parse_page
from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse

if TYPE_CHECKING:
    from rabbitmq_management_sdk.http_adapter import HttpAdapter
    from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_request import QueueRequest


class QueueManager:
    def __init__(self, http_client: HttpAdapter, vhost: str, strict: bool) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict

    def get(self, name: str, *, disable_stats: bool = False) -> QueueResponse:
        """Return one queue by name.

        Args:
            name: The name of the queue to retrieve.
            disable_stats: Exclude runtime statistics from the response.
        """
        return parse_one(
            self._ha.request(
                method=HTTPMethod.GET,
                path=f"/api/queues/{self._vhost}/{name}",
                params=self._stats_params(disable_stats),
            ),
            QueueResponse,
        )

    def list_page_by_vhost(
        self,
        *,
        page: int = 1,
        page_size: int = 100,
        name: str | None = None,
        use_regex: bool = False,
        disable_stats: bool = False,
    ) -> Page[QueueResponse]:
        """Return one filtered page of queues in the configured virtual host.

        Args:
            page: One-based page number.
            page_size: Maximum number of queues returned per page.
            name: Optional queue name filter.
            use_regex: Treat ``name`` as a regular expression when true.
            disable_stats: Exclude runtime statistics from the response.
        """
        return self._list_page(
            path=f"/api/queues/{self._vhost}",
            page=page,
            page_size=page_size,
            name=name,
            use_regex=use_regex,
            disable_stats=disable_stats,
        )

    def list_by_vhost(
        self,
        *,
        page_size: int = 100,
        name: str | None = None,
        use_regex: bool = False,
        disable_stats: bool = False,
    ) -> list[QueueResponse]:
        """Return every queue in the configured virtual host.

        The manager fetches bounded pages until RabbitMQ reports that all matching
        queues have been returned. Use :meth:`list_page_by_vhost` when callers need
        one page and its pagination metadata.
        """
        return self._list_all_pages(
            path=f"/api/queues/{self._vhost}",
            page_size=page_size,
            name=name,
            use_regex=use_regex,
            disable_stats=disable_stats,
        )

    def list_page(
        self,
        *,
        page: int = 1,
        page_size: int = 100,
        name: str | None = None,
        use_regex: bool = False,
        disable_stats: bool = False,
    ) -> Page[QueueResponse]:
        """Return one filtered page of queues across all virtual hosts.

        Args:
            page: One-based page number.
            page_size: Maximum number of queues returned per page.
            name: Optional queue name filter.
            use_regex: Treat ``name`` as a regular expression when true.
            disable_stats: Exclude runtime statistics from the response.
        """
        return self._list_page(
            path="/api/queues",
            page=page,
            page_size=page_size,
            name=name,
            use_regex=use_regex,
            disable_stats=disable_stats,
        )

    def list_all(
        self,
        *,
        page_size: int = 100,
        name: str | None = None,
        use_regex: bool = False,
        disable_stats: bool = False,
    ) -> list[QueueResponse]:
        """Return every queue across all virtual hosts using bounded requests.

        Use :meth:`list_page` when callers need one page and its pagination metadata.
        """
        return self._list_all_pages(
            path="/api/queues",
            page_size=page_size,
            name=name,
            use_regex=use_regex,
            disable_stats=disable_stats,
        )

    def create(self, name: str, request: QueueRequest) -> None:
        self._ha.request(
            method=HTTPMethod.PUT, path=f"/api/queues/{self._vhost}/{name}", json=self._to_http_payload(request)
        )

    def delete(self, name: str) -> None:
        self._ha.request(method=HTTPMethod.DELETE, path=f"/api/queues/{self._vhost}/{name}")

    def _list_all_pages(
        self,
        *,
        path: str,
        page_size: int,
        name: str | None,
        use_regex: bool,
        disable_stats: bool,
    ) -> list[QueueResponse]:
        first_page = self._list_page(
            path=path,
            page=1,
            page_size=page_size,
            name=name,
            use_regex=use_regex,
            disable_stats=disable_stats,
        )
        queues = list(first_page.items)
        for page in range(first_page.page + 1, first_page.page_count + 1):
            queues.extend(
                self._list_page(
                    path=path,
                    page=page,
                    page_size=page_size,
                    name=name,
                    use_regex=use_regex,
                    disable_stats=disable_stats,
                ).items
            )
        return queues

    def _list_page(
        self,
        *,
        path: str,
        page: int,
        page_size: int,
        name: str | None,
        use_regex: bool,
        disable_stats: bool,
    ) -> Page[QueueResponse]:
        return parse_page(
            self._ha.request(
                method=HTTPMethod.GET,
                path=path,
                params=self._list_params(
                    page=page,
                    page_size=page_size,
                    name=name,
                    use_regex=use_regex,
                    disable_stats=disable_stats,
                ),
            ),
            QueueResponse,
        )

    @staticmethod
    def _stats_params(disable_stats: bool) -> dict[str, str] | None:
        return {"disable_stats": "true"} if disable_stats else None

    @staticmethod
    def _list_params(
        *,
        page: int,
        page_size: int,
        name: str | None,
        use_regex: bool,
        disable_stats: bool,
    ) -> dict[str, str]:
        params = {"page": str(page), "page_size": str(page_size)}
        if name is not None:
            params["name"] = name
            params["use_regex"] = str(use_regex).lower()
        if disable_stats:
            params["disable_stats"] = "true"
        return params

    def _to_http_payload(self, request: QueueRequest) -> dict[str, Any]:
        """Convert a QueueRequest object to a dictionary because the queue_type has a default value
        and stripped by model_dump in compatibility mode.

        Returns:
            A dictionary with keys "durable", "auto_delete",
            and "arguments" that can be sent as JSON in an HTTP request.
        """
        data = {
            "x-queue-type": request.arguments.queue_type,
            "durable": request.durable,
            "auto_delete": request.auto_delete,
            "arguments": request.arguments.model_dump(
                by_alias=True,
                exclude_none=True,
                exclude_defaults=not self._strict,
                # When strict is False, defaults are excluded to avoid
                # errors on queues created without explicit x-arguments.
            ),
        }
        return data
