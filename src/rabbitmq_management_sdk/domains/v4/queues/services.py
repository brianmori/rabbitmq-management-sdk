from http import HTTPMethod
from typing import TYPE_CHECKING, Any

from rabbitmq_management_sdk.domains.v4.queues.schemas.queue_response import Queue

if TYPE_CHECKING:
    from rabbitmq_management_sdk.domains.v4.queues.schemas.queue_request import QueueRequest
    from rabbitmq_management_sdk.http_adapter import HttpAdapter


class QueueManagerV4:
    def __init__(self, http_client: HttpAdapter, vhost: str, strict: bool) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict

    def get(self, name: str) -> Queue:
        # Business logic for V4
        data = (self._ha.request(method=HTTPMethod.GET, path=f"/api/queues/{self._vhost}/{name}")).json()

        return Queue.model_validate(data)

    def create(self, name: str, request: QueueRequest) -> None:
        self._ha.request(
            method=HTTPMethod.PUT, path=f"/api/queues/{self._vhost}/{name}", json=self._to_http_payload(request)
        )

    def delete(self, name: str) -> None:

        self._ha.request(method=HTTPMethod.DELETE, path=f"/api/queues/{self._vhost}/{name}")

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
