from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rabbitmq_management_sdk.http_adapter import HttpAdapter


class BindingManagerV4:
    def __init__(self, http_client: HttpAdapter, vhost: str, strict: bool) -> None:
        self._ha = http_client
        self._vhost = vhost
        self._strict = strict
