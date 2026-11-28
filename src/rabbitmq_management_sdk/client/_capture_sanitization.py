"""Project management responses into the credential-sanitized capture format."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from pydantic import JsonValue

from rabbitmq_management_sdk.topology.shovel import _capture_shovel_uri

if TYPE_CHECKING:
    from rabbitmq_management_sdk.resources.v4.admin.schemas.export_response import ClusterDefinitionsResponse
    from rabbitmq_management_sdk.resources.v4.exchanges.schemas.exchange_response import ExchangeResponse
    from rabbitmq_management_sdk.resources.v4.queues.schemas.queue_response import QueueResponse

type JsonObject = dict[str, JsonValue]

_RUNTIME_COMPONENTS = {"operator_policy", "shovel", "vhost-limits"}
_CLUSTER_IDENTITIES = {"cluster_name", "internal_cluster_id"}
_ROUTE_KEYS = {
    "alternate-exchange",
    "dead-letter-exchange",
    "dead-letter-routing-key",
    "x-dead-letter-exchange",
    "x-dead-letter-routing-key",
}
_SHOVEL_KEYS = {"ack-mode"} | {
    f"{side}-{field}"
    for side in ("src", "dest")
    for field in ("address", "exchange", "exchange-key", "protocol", "queue", "uri")
}
_QUEUE_STATS = {
    "consumers",
    "consumer_utilisation",
    "memory",
    "messages",
    "messages_ready",
    "messages_unacknowledged",
    "message_stats",
}
_CREDENTIAL_KEY = re.compile(
    r"authorization|authentication|credential|password|passwd|passphrase|secret|token|"
    r"apikey|accesskey|privatekey|clientkey|cookie|sasl|^pwd$"
)
_CREDENTIAL_TEXT = re.compile(
    r"://|^\s*(?:basic|bearer)\s+|\b(?:password|passwd|token|secret)\s*=|-----BEGIN [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE,
)
_REDACTED = re.compile(r"\[redacted(?: [1-9][0-9]*)?\]")


class CaptureSanitizer:
    """Sanitize one bundle while preserving distinct redacted binding values."""

    def __init__(self) -> None:
        self._redactions: dict[str, str] = {}

    def _redact(self, value: JsonValue) -> JsonValue:
        if value is None or (isinstance(value, str) and _REDACTED.fullmatch(value)):
            return value
        canonical = json.dumps(value, sort_keys=True)
        return self._redactions.setdefault(canonical, f"[redacted {len(self._redactions) + 1}]")

    def _opaque(self, value: JsonValue, *, keep_routes: bool = False) -> JsonValue:
        if isinstance(value, dict):
            return {
                key: item
                if keep_routes and key in _ROUTE_KEYS and (item is None or isinstance(item, str))
                else self._entry(key, item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._opaque(item) for item in value]
        return self._redact(value) if isinstance(value, str) and _CREDENTIAL_TEXT.search(value) else value

    def _entry(self, key: str, value: JsonValue) -> JsonValue:
        if _CREDENTIAL_KEY.search(re.sub(r"[^a-z]", "", key.lower())):
            return self._redact(value)
        return self._opaque(value)

    def _shovel(self, value: JsonValue) -> JsonValue:
        if not isinstance(value, dict):
            # Preserve invalid non-object evidence without storing its contents.
            return None
        result = {key: item for key, item in value.items() if key in _SHOVEL_KEYS}
        for side in ("src", "dest"):
            key = f"{side}-uri"
            if key not in result:
                continue
            protocol = result.get(f"{side}-protocol", "amqp091")
            protocol = protocol if isinstance(protocol, str) else ""
            uri = result[key]
            if isinstance(uri, list):
                result[key] = [_capture_shovel_uri(item, protocol) if isinstance(item, str) else None for item in uri]
            elif isinstance(uri, str):
                result[key] = _capture_shovel_uri(uri, protocol)
            else:
                result[key] = None
        for key in ("ack-mode", "src-protocol", "dest-protocol"):
            if key in result:
                result[key] = self._opaque(result[key])
        return result

    def _parameter_value(self, component: str, value: JsonValue) -> JsonValue:
        if component == "shovel":
            return self._shovel(value)
        if component == "operator_policy":
            if not isinstance(value, dict):
                return None
            return {
                key: self._opaque(item, keep_routes=True) if key == "definition" else item
                for key, item in value.items()
                if key in {"apply-to", "definition", "pattern", "priority"}
            }
        return self._opaque(value)

    def definitions(self, definitions: ClusterDefinitionsResponse) -> JsonObject:
        data = definitions.model_dump(mode="json", by_alias=True, exclude_unset=True, exclude={"explanation"})
        for user in data.get("users", []):
            user["password_hash"] = "[redacted]"
        if "global_parameters" in data:
            data["global_parameters"] = [
                {"name": row.name, "value": row.value if isinstance(row.value, str) else None}
                for row in definitions.global_parameters
                if row.name in _CLUSTER_IDENTITIES
            ]
        if "parameters" in data:
            data["parameters"] = [
                {
                    "component": row.component,
                    "name": row.name,
                    "vhost": row.vhost,
                    "value": self._parameter_value(row.component, row.value),
                }
                for row in definitions.parameters
                if row.component in _RUNTIME_COMPONENTS
            ]
        for row in data.get("vhosts", []):
            row.pop("description", None)
            row.pop("tags", None)
            if row.get("metadata") is not None:
                row["metadata"].pop("description", None)
                row["metadata"].pop("tags", None)
            if "limits" in row:
                row["limits"] = self._opaque(row["limits"])
        for row in data.get("policies", []):
            row["definition"] = self._opaque(row["definition"], keep_routes=True)
        for section in ("queues", "exchanges", "bindings"):
            for row in data.get(section, []):
                if "arguments" in row:
                    row["arguments"] = self._opaque(row["arguments"], keep_routes=section != "bindings")
        return data

    def queue(self, queue: QueueResponse) -> JsonObject:
        data = queue.model_dump(mode="json", by_alias=True, exclude_unset=True, exclude=_QUEUE_STATS)
        if "effective_policy_definition" in data:
            data["effective_policy_definition"] = self._opaque(data["effective_policy_definition"], keep_routes=True)
        data["arguments"] = self._opaque(data["arguments"], keep_routes=True)
        return data

    def exchange(self, exchange: ExchangeResponse) -> JsonObject:
        data = exchange.model_dump(mode="json", by_alias=True, exclude_unset=True, exclude={"message_stats"})
        if "arguments" in data:
            data["arguments"] = self._opaque(data["arguments"], keep_routes=True)
        return data
