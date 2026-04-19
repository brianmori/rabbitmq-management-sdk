from http import HTTPMethod
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.domains.v4.exchanges.schemas.exchange_response import ExchangeResponse

if TYPE_CHECKING:
    from rabbitmq_management_sdk.domains.v4.exchanges.schemas.exchange_request import ExchangeRequest
    from rabbitmq_management_sdk.http_adapter import HttpAdapter


class ExchangeManagerV4:
    def __init__(self, http_client: HttpAdapter, vhost: str, strict: bool) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict

    def get(self, name: str) -> ExchangeResponse:
        """Retrieves a specific exchange by its name.

        Args:
            name: The name of the exchange to retrieve. Use an empty string to retrieve the default nameless exchange.

        Returns:

        """
        data = (self._ha.request(method=HTTPMethod.GET, path=f"/api/exchanges/{self._vhost}/{name}")).json()

        return ExchangeResponse.model_validate(data)

    def list_by_vhost(self) -> list[ExchangeResponse]:
        """Return all exchanges in the virtual host.

        Includes the default nameless exchange and the built-in
        ``amq.*`` exchanges.
        """
        response = self._ha.request(method=HTTPMethod.GET, path=f"/api/exchanges/{self._vhost}")
        return [ExchangeResponse.model_validate(item) for item in response.json()]

    def list_all(self) -> list[ExchangeResponse]:
        """Returns all exchanges in the virtual host.

        This includes the default nameless exchange as well as the built-in
        ``amq.*`` exchanges.

        Returns:
            list[ExchangeResponse]: A list of objects representing all
                exchanges in the virtual host.
        """
        response = self._ha.request(method=HTTPMethod.GET, path="/api/exchanges")
        return [ExchangeResponse.model_validate(item) for item in response.json()]

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
