from __future__ import annotations

from http import HTTPMethod
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.resources.base import Page, parse_one, parse_page
from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse

if TYPE_CHECKING:
    from rabbitmq_management_sdk.http_adapter import HttpAdapter
    from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_request import ExchangeRequest


class ExchangeManager:
    def __init__(self, http_client: HttpAdapter, vhost: str, strict: bool) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict

    def get(self, name: str, *, disable_stats: bool = False) -> ExchangeResponse:
        """Return one exchange by name.

        Args:
            name: The name of the exchange to retrieve. Use an empty string to retrieve the default nameless exchange.
            disable_stats: Exclude runtime statistics from the response.
        """
        return parse_one(
            self._ha.request(
                method=HTTPMethod.GET,
                path=f"/api/exchanges/{self._vhost}/{name}",
                params=self._stats_params(disable_stats),
            ),
            ExchangeResponse,
        )

    def list_page_by_vhost(
        self,
        *,
        page: int = 1,
        page_size: int = 100,
        name: str | None = None,
        use_regex: bool = False,
        disable_stats: bool = False,
    ) -> Page[ExchangeResponse]:
        """Return one filtered page of exchanges in the configured virtual host.

        Args:
            page: One-based page number.
            page_size: Maximum number of exchanges returned per page.
            name: Optional exchange name filter.
            use_regex: Treat ``name`` as a regular expression when true.
            disable_stats: Exclude runtime statistics from the response.
        """
        return self._list_page(
            path=f"/api/exchanges/{self._vhost}",
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
    ) -> list[ExchangeResponse]:
        """Return every exchange in the configured virtual host.

        The manager fetches bounded pages until RabbitMQ reports that all matching
        exchanges have been returned. Use :meth:`list_page_by_vhost` when callers
        need one page and its pagination metadata.
        """
        return self._list_all_pages(
            path=f"/api/exchanges/{self._vhost}",
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
    ) -> Page[ExchangeResponse]:
        """Return one filtered page of exchanges across all virtual hosts.

        Args:
            page: One-based page number.
            page_size: Maximum number of exchanges returned per page.
            name: Optional exchange name filter.
            use_regex: Treat ``name`` as a regular expression when true.
            disable_stats: Exclude runtime statistics from the response.
        """
        return self._list_page(
            path="/api/exchanges",
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
    ) -> list[ExchangeResponse]:
        """Return every exchange across all virtual hosts using bounded requests.

        Use :meth:`list_page` when callers need one page and its pagination metadata.
        """
        return self._list_all_pages(
            path="/api/exchanges",
            page_size=page_size,
            name=name,
            use_regex=use_regex,
            disable_stats=disable_stats,
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create(self, name: str, request: ExchangeRequest) -> None:
        """Declares an exchange.

        This operation is idempotent. If the exchange already exists with
        identical properties, RabbitMQ returns a 204 status without error.

        Args:
            name: The name of the exchange to declare.
            request: configuration properties for the exchange.

        Raises:
            Exception: If the exchange exists with different properties,
                RabbitMQ returns a 400 status, raised as an exception by
                the transport layer.
        """
        data = request.model_dump(by_alias=True, exclude_none=True)
        self._ha.request(
            method=HTTPMethod.PUT,
            path=f"/api/exchanges/{self._vhost}/{name}",
            json=data,
        )

    def delete(self, name: str, if_unused: bool = False) -> None:
        """Deletes a specific exchange by its name.

        Args:
            name: The name of the exchange to delete.
            if_unused: If True, the exchange is only deleted if it has no
                existing bindings.

        Raises:
            Exception: If RabbitMQ returns a 400 status (when if_unused is True
                and bindings exist).
            Exception: If RabbitMQ returns a 403 status (when attempting to
                delete the default or built-in amq.* exchanges).
        """
        params = {"if-unused": "true"} if if_unused else {}
        self._ha.request(
            method=HTTPMethod.DELETE,
            path=f"/api/exchanges/{self._vhost}/{name}",
            params=params,
        )

    def _list_all_pages(
        self,
        *,
        path: str,
        page_size: int,
        name: str | None,
        use_regex: bool,
        disable_stats: bool,
    ) -> list[ExchangeResponse]:
        first_page = self._list_page(
            path=path,
            page=1,
            page_size=page_size,
            name=name,
            use_regex=use_regex,
            disable_stats=disable_stats,
        )
        exchanges = list(first_page.items)
        for page in range(first_page.page + 1, first_page.page_count + 1):
            exchanges.extend(
                self._list_page(
                    path=path,
                    page=page,
                    page_size=page_size,
                    name=name,
                    use_regex=use_regex,
                    disable_stats=disable_stats,
                ).items
            )
        return exchanges

    def _list_page(
        self,
        *,
        path: str,
        page: int,
        page_size: int,
        name: str | None,
        use_regex: bool,
        disable_stats: bool,
    ) -> Page[ExchangeResponse]:
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
            ExchangeResponse,
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
