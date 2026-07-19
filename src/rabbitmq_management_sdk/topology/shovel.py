"""
Shovel URI and address parsing.

Split out from parser.py deliberately: this module has zero dependency
on the wire models or the domain layer -- it only knows about RabbitMQ's
URI spec and AMQP 1.0 addressing scheme. That means it's testable in
complete isolation (no ClusterDefinitionsResponse or ClusterTopology
needed to construct a test case), and it's the piece most likely to
need a fix as new URI/address shapes turn up in real dumps, so keeping
it separate makes that boundary explicit rather than implied.

Kind is returned as a plain "queue" | "exchange" string rather than
NodeKind, to avoid pulling in the domain layer here. parser.py maps it
to NodeKind at the point where it actually needs one.
"""

from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

DEFAULT_VHOST = "/"


def vhost_from_amqp091_uri(uri: str) -> str:
    """
    Extract the vhost from an AMQP 0-9-1-shaped URI (src-uri/dest-uri),
    per RabbitMQ's URI spec (https://www.rabbitmq.com/docs/uri-spec).
    Used for both the amqp091 and local shovel protocols -- confirmed
    against a real dump that "local" shovels use the identical
    amqp://user:pass@host/vhost shape, not something protocol-specific.

    "Absent" and "empty" are genuinely different per spec, not the same
    case with two spellings:
      - No "/" after the host at all -> vhost is absent -> default vhost "/".
      - A "/" present with nothing after it -> vhost is "" (empty string),
        a real, distinct vhost name -- NOT the default.
    """
    path = urlsplit(uri).path
    if path == "":
        return DEFAULT_VHOST
    return unquote(path[1:])  # path always starts with "/" once non-empty


def vhost_from_amqp10_uri(uri: str) -> str | None:
    """
    Extract the vhost from a shovel's AMQP 1.0 URI (src-uri/dest-uri).

    AMQP 1.0 has no protocol-level vhost concept. RabbitMQ overloads the
    connection's `hostname` field for vhost addressing: prefixing it
    with "vhost:" names the target vhost ("vhost:/" means the default
    vhost explicitly). In a shovel's URI that value travels as the
    `hostname` query parameter, e.g. `?hostname=vhost:my-vhost`.

    Deliberately not using urllib.parse.parse_qs: it decodes "+" as a
    space (form-encoding convention), which would silently mangle a
    vhost name containing a literal "+". Splitting by hand and using
    unquote() rather than unquote_plus() avoids that.

    If `hostname` is absent, or present without the "vhost:" prefix, the
    connection falls back to the broker's configured default_vhost --
    usually "/" but admin-configurable, and not derivable from the URI
    itself. Returns None rather than assuming "/".
    """
    query = urlsplit(uri).query
    prefix = "vhost:"
    for pair in query.split("&"):
        key, _, value = pair.partition("=")
        if key == "hostname":
            hostname = unquote(value)
            if hostname.startswith(prefix):
                return hostname[len(prefix) :]
            return None
    return None


def _first_uri(uri: object) -> str | None:
    """
    Normalize src-uri/dest-uri to a single representative URI string.

    RabbitMQ allows a list of URIs for failover (confirmed against a
    real dump: ["amqp://lab:lab@localhost/src", "amqp://lab:lab@localhost/src"]),
    not just a plain string -- all candidates in the list should point
    at the same vhost/resource (that's the point of failover), so the
    first is representative for vhost and resource resolution purposes.
    This isn't a claim that they're always literally identical, just
    that picking one deterministically is enough for topology purposes.

    Accepts `object` rather than `str | list[str]` so callers holding a
    raw dict[str, object] value (parser.py's shovel parameters) can pass
    it straight through without narrowing it themselves first -- that
    narrowing is exactly what this function exists to do.
    """
    if isinstance(uri, list):
        return uri[0] if uri and isinstance(uri[0], str) else None
    return uri if isinstance(uri, str) else None


def protocol_uri_vhost(uri: object, protocol: str | None) -> str | None:
    """
    Dispatch to the right protocol's vhost extractor. Returns None
    (rather than guessing "/") when the protocol is unrecognized,
    the URI is missing, or (for a list of URIs) empty.

    "local" shovels share amqp091's URI shape and vhost extraction --
    confirmed against a real dump -- even though the two protocols
    differ elsewhere (e.g. local shovels have no prefetch-count).
    """
    single_uri = _first_uri(uri)
    if single_uri is None or protocol is None:
        return None
    if protocol in ("amqp091", "local"):
        return vhost_from_amqp091_uri(single_uri)
    if protocol == "amqp10":
        return vhost_from_amqp10_uri(single_uri)
    return None


def parse_amqp10_address(address: object) -> tuple[str, str, str | None] | None:
    """
    Parse a RabbitMQ AMQP 1.0 address (src-address/dest-address) into
    (name, kind, routing_key), kind being the literal string "queue" or
    "exchange".

    https://www.rabbitmq.com/docs/amqp -- address formats:
      /queues/:queue             -> (queue, "queue", None)
      /exchanges/:exchange/:key  -> (exchange, "exchange", key)
      /exchanges/:exchange       -> (exchange, "exchange", None) -- routing
                                     key would come from the message's
                                     `subject` per-message, not fixed
      /topic/:routing-key        -> ("amq.topic", "exchange", routing-key)
      :queue (no leading slash)  -> (queue, "queue", None) -- deprecated
                                     v1 format, documented as redundant
                                     to /queues/:queue

    Only the /queues/:name form is directly confirmed against real
    shovel data; the exchange and /topic/ forms are the general
    RabbitMQ AMQP 1.0 addressing scheme (confirmed for AMQP 1.0 clients
    generally), assumed -- not separately confirmed -- to apply the
    same way to a shovel's src-address/dest-address.

    Returns None for anything unresolvable to a fixed resource, e.g. the
    AMQP-null dynamic-addressing target, which by definition doesn't
    name one in the address string at all.
    """
    if not isinstance(address, str) or address == "":
        return None
    if not address.startswith("/"):
        return address, "queue", None

    parts = address.strip("/").split("/")
    kind_segment, *rest = parts
    if kind_segment in ("queue", "queues") and rest:
        return unquote(rest[0]), "queue", None
    if kind_segment in ("exchange", "exchanges") and rest:
        routing_key = unquote(rest[1]) if len(rest) > 1 else None
        return unquote(rest[0]), "exchange", routing_key
    if kind_segment == "topic" and rest:
        return "amq.topic", "exchange", unquote(rest[0])
    return None


def shovel_side_resource(
    value: Mapping[str, object], side: str, protocol: str | None
) -> tuple[str, str, str | None] | None:
    """
    (name, kind, routing_key) for one side ("src" or "dest") of a
    shovel, or None if it can't be resolved (e.g. an AMQP 0-9-1 side
    with neither -queue nor -exchange set).

    "local" shovels use the same -queue / -exchange / -exchange-key
    field names as amqp091 -- confirmed against a real dump -- so they
    share this branch. The two protocols differ elsewhere (amqp091 has
    a prefetch-count, local doesn't), but not in how source/destination
    resources are named.
    """
    if protocol in ("amqp091", "local"):
        queue = value.get(f"{side}-queue")
        if isinstance(queue, str):
            return queue, "queue", None
        exchange = value.get(f"{side}-exchange")
        if isinstance(exchange, str):
            key = value.get(f"{side}-exchange-key")
            return exchange, "exchange", key if isinstance(key, str) else None
        return None
    if protocol == "amqp10":
        return parse_amqp10_address(value.get(f"{side}-address"))
    return None
