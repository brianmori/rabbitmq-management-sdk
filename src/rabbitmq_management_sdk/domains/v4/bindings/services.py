from http import HTTPMethod
from typing import TYPE_CHECKING

from rabbitmq_management_sdk.domains.v4.bindings.schemas.binding_response import BindingResponse
from rabbitmq_management_sdk.domains.v4.bindings.schemas.common import BindingDestinationType

if TYPE_CHECKING:
    from rabbitmq_management_sdk.domains.v4.bindings.schemas.binding_request import BindingRequest
    from rabbitmq_management_sdk.http_adapter import HttpAdapter


class BindingManagerV4:
    def __init__(self, http_client: HttpAdapter, vhost: str, strict: bool) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict

    def list_all(self) -> list[BindingResponse]:
        """Return all bindings across every virtual host in the cluster."""
        response = self._ha.request(method=HTTPMethod.GET, path="/api/bindings")
        return [BindingResponse.model_validate(item) for item in response.json()]

    def list_by_vhost(self) -> list[BindingResponse]:
        """Return all bindings in the virtual host."""
        response = self._ha.request(method=HTTPMethod.GET, path=f"/api/bindings/{self._vhost}")
        return [BindingResponse.model_validate(item) for item in response.json()]

    def list_exchange_to_queue(
        self,
        exchange: str,
        queue: str,
    ) -> list[BindingResponse]:
        """Return all bindings between *exchange* and *queue*.

        Multiple bindings between the same exchange and queue are possible
        when they differ in routing key or arguments.

        Args:
            exchange: The name of the exchange.
            queue: The name of the queue.

        Returns:
            list[BindingResponse]: A list of objects representing all bindings between the exchange and queue.

        """
        response = self._ha.request(method=HTTPMethod.GET, path=f"/api/bindings/{self._vhost}/e/{exchange}/q/{queue}")
        return [BindingResponse.model_validate(item) for item in response.json()]

    def list_exchange_to_exchange(
        self,
        source: str,
        destination: str,
    ) -> list[BindingResponse]:
        """Return all bindings between *source* and *destination* exchanges.

        Args:
            source: The name of the source exchange.
            destination: The name of the destination exchange.

        Returns:
            list[BindingResponse]: A list of objects representing all bindings between the source and
            destination exchanges.

        """
        response = self._ha.request(
            method=HTTPMethod.GET, path=f"/api/bindings/{self._vhost}/e/{source}/e/{destination}"
        )
        return [BindingResponse.model_validate(item) for item in response.json()]

    def create_exchange_to_queue(
        self,
        exchange: str,
        queue: str,
        request: BindingRequest,
    ) -> BindingResponse:
        """Create a binding between *exchange* and *queue*.

        Args:
            exchange: The name of the exchange to bind from.
            queue: The name of the queue to bind to.
            request: The binding details, including routing key and arguments.

        Returns:
            The created binding including the server-assigned ``properties_key`` needed for deletion.

        Raises:
            ValueError: If RabbitMQ does not return a Location header in the response.
        """
        response = self._ha.request(
            method=HTTPMethod.POST,
            path=f"/api/bindings/{self._vhost}/e/{exchange}/q/{queue}",
            json=request.model_dump(exclude_none=True, by_alias=True),
        )

        location = response.headers.get("location")
        if not location:
            raise ValueError(
                "RabbitMQ did not return a Location header after binding creation",
            )
        props_key = location.split("/")[-1]

        return BindingResponse(
            source=exchange,
            destination=queue,
            destination_type=BindingDestinationType.QUEUE,
            routing_key=request.routing_key,
            arguments=request.arguments,
            properties_key=props_key,
            vhost=self._vhost,
        )

    def create_exchange_to_exchange(
        self,
        source: str,
        destination: str,
        request: BindingRequest,
    ) -> BindingResponse:
        """Create a binding between *source* and *destination* exchanges.

        Returns the created binding including the server-assigned
        ``properties_key`` needed for deletion.

        Args:
            source: The name of the source exchange.
            destination: The name of the destination exchange.
            request: The binding details, including routing key and arguments.

        Returns:
            The created binding including the server-assigned ``properties_key`` needed for deletion.
        """
        response = self._ha.request(
            method=HTTPMethod.POST,
            path=f"/api/bindings/{self._vhost}/e/{source}/e/{destination}",
            json=request.model_dump(exclude_none=True, by_alias=True),
        )

        location = response.headers.get("location")
        if not location:
            raise ValueError(
                "RabbitMQ did not return a Location header after binding creation",
            )
        props_key = location.split("/")[-1]

        return BindingResponse(
            source=source,
            destination=destination,
            destination_type=BindingDestinationType.EXCHANGE,
            routing_key=request.routing_key,
            arguments=request.arguments,
            properties_key=props_key,
            vhost=self._vhost,
        )

    def delete_exchange_to_queue(
        self,
        exchange: str,
        queue: str,
        properties_key: str,
    ) -> None:
        """Delete a specific binding between *exchange* and *queue*.

        *properties_key* is the value returned by :meth:`create_exchange_to_queue`
        or found in a :class:`BindingResponse`. It uniquely identifies the
        binding when multiple bindings exist between the same exchange and queue.

        Args:
            exchange: The name of the exchange.
            queue: The name of the queue.
            properties_key: The unique identifier for the binding to delete, returned by RabbitMQ
            when the binding was created.
        """
        self._ha.request(
            method=HTTPMethod.DELETE, path=f"/api/bindings/{self._vhost}/e/{exchange}/q/{queue}/{properties_key}"
        )

    def delete_exchange_to_exchange(
        self,
        source: str,
        destination: str,
        properties_key: str,
    ) -> None:
        """Delete a specific binding between *source* and *destination* exchanges.

        Args:
            source:
            destination:
            properties_key:

        Returns:

        """
        self._ha.request(
            method=HTTPMethod.DELETE, path=f"/api/bindings/{self._vhost}/e/{source}/e/{destination}/{properties_key}"
        )
