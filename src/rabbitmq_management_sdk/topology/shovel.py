"""Normalize credential-free shovel endpoint and locality evidence.

Locality is conservative. A shovel endpoint contributes a resource edge only
when it identifies a fixed queue or exchange and positive evidence confirms
that resource belongs to the captured cluster. An unconfirmed endpoint is not
necessarily remote.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import SplitResult, unquote, urlsplit

from rabbitmq_management_sdk.topology.models import EndpointAuthority, NodeKind, ResourceEndpoint

DEFAULT_VHOST = "/"
_DEFAULT_PORT_BY_SCHEME = {"amqp": 5672, "amqps": 5671}
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_MIN_NETWORK_PORT = 1
_MAX_NETWORK_PORT = 65535
_ASCII_CONTROL_LIMIT = 0x20
_ASCII_DELETE = 0x7F
_ASCII_DIGITS = frozenset("0123456789")
_URI_COMPONENT_CHARACTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~!$&'()*+,;=")
_IP_LITERAL_CHARACTERS = _URI_COMPONENT_CHARACTERS | frozenset("%:")


@dataclass(frozen=True, slots=True)
class _UriCandidate:
    """Validated evidence from one URI in a shovel failover set."""

    vhost: str | None
    authority: EndpointAuthority


@dataclass(frozen=True, slots=True)
class _UriEvidence:
    """Combined vhost and authority evidence for a complete failover set."""

    vhost: str | None
    authorities: tuple[EndpointAuthority, ...] | None


def _contains_disallowed_raw_character(value: str) -> bool:
    """Return whether raw whitespace or a control character is present."""
    return any(
        character.isspace() or ord(character) < _ASCII_CONTROL_LIMIT or ord(character) == _ASCII_DELETE
        for character in value
    )


def _has_valid_percent_encoding(value: str) -> bool:
    """Return whether every percent sign begins one encoded byte."""
    return all(
        index + 2 < len(value) and value[index + 1] in _HEX_DIGITS and value[index + 2] in _HEX_DIGITS
        for index, character in enumerate(value)
        if character == "%"
    )


def _has_only_uri_component_characters(value: str) -> bool:
    """Validate one RFC 3986 component after percent syntax was checked."""
    return all(character == "%" or character in _URI_COMPONENT_CHARACTERS for character in value)


def _has_valid_userinfo(userinfo: str) -> bool:
    """Validate and UTF-8-decode RabbitMQ's optional username and password."""
    if userinfo.count(":") > 1:
        return False
    username, separator, password = userinfo.partition(":")
    components = (username, password) if separator else (username,)
    if not all(_has_only_uri_component_characters(component) for component in components):
        return False
    try:
        for component in components:
            unquote(component, errors="strict")
    except UnicodeDecodeError:
        return False
    return True


def _has_valid_host_and_port(host_and_port: str) -> bool:
    """Validate the raw host and optional decimal port delimiters."""
    if host_and_port.startswith("["):
        closing_bracket = host_and_port.find("]")
        if closing_bracket < 0:
            return False
        raw_literal = host_and_port[1:closing_bracket]
        suffix = host_and_port[closing_bracket + 1 :]
        valid_literal = bool(raw_literal) and all(character in _IP_LITERAL_CHARACTERS for character in raw_literal)
        return valid_literal and (
            suffix == ""
            or (
                suffix.startswith(":")
                and bool(suffix[1:])
                and all(character in _ASCII_DIGITS for character in suffix[1:])
            )
        )

    if host_and_port.count(":") > 1:
        return False
    raw_host, separator, raw_port = host_and_port.partition(":")
    return _has_only_uri_component_characters(raw_host) and (
        not separator or (bool(raw_port) and all(character in _ASCII_DIGITS for character in raw_port))
    )


def _has_valid_raw_authority(authority: str) -> bool:
    """Validate RabbitMQ's ``[userinfo@]host[:port]`` authority grammar."""
    if authority.count("@") > 1:
        return False
    userinfo, separator, host_and_port = authority.rpartition("@")
    if not separator:
        host_and_port = authority
    return (not separator or _has_valid_userinfo(userinfo)) and _has_valid_host_and_port(host_and_port)


def _parse_base_uri(uri: str) -> tuple[SplitResult, EndpointAuthority] | None:
    """Validate common AMQP URI syntax and normalize its safe authority."""
    if not uri or _contains_disallowed_raw_character(uri) or not _has_valid_percent_encoding(uri) or "#" in uri:
        return None

    raw_scheme, separator, _remainder = uri.partition("://")
    scheme = raw_scheme.casefold()
    if not separator or scheme not in _DEFAULT_PORT_BY_SCHEME:
        return None

    try:
        parsed = urlsplit(uri)
        raw_authority_is_valid = _has_valid_raw_authority(parsed.netloc)
        parsed_port = parsed.port
        port = parsed_port if parsed_port is not None else _DEFAULT_PORT_BY_SCHEME[scheme]
        raw_host = parsed.hostname
        host = unquote(raw_host, errors="strict").casefold() if raw_host is not None else None
    except (UnicodeDecodeError, ValueError):
        return None

    if host == "":
        host = None
    if (
        parsed.scheme.casefold() != scheme
        or not raw_authority_is_valid
        or not _MIN_NETWORK_PORT <= port <= _MAX_NETWORK_PORT
        or (host is not None and _contains_disallowed_raw_character(host))
    ):
        return None

    return parsed, EndpointAuthority(scheme=scheme, host=host, port=port)


def _amqp091_vhost(path: str) -> str | None:
    """Decode one AMQP 0-9-1 vhost path, or reject an invalid path."""
    if path == "":
        return DEFAULT_VHOST
    if not path.startswith("/") or "/" in path[1:]:
        return None
    try:
        return unquote(path[1:], errors="strict")
    except UnicodeDecodeError:
        return None


def _amqp10_vhost(query: str) -> tuple[bool, str | None]:
    """Resolve RabbitMQ's AMQP 1.0 hostname convention from one query."""
    hostnames: list[str] = []
    try:
        for pair in query.split("&") if query else ():
            raw_key, _separator, raw_value = pair.partition("=")
            if unquote(raw_key, errors="strict") == "hostname":
                hostnames.append(unquote(raw_value, errors="strict"))
    except UnicodeDecodeError:
        return False, None

    if len(hostnames) != 1:
        return True, None
    hostname = hostnames[0]
    prefix = "vhost:"
    return True, hostname[len(prefix) :] if hostname.startswith(prefix) else None


def _parse_uri_candidate(uri: str, protocol: str) -> _UriCandidate | None:
    """Parse one protocol-aware URI without retaining credentials."""
    parsed_base = _parse_base_uri(uri)
    if parsed_base is None:
        return None
    parsed, authority = parsed_base

    if protocol in ("amqp091", "local"):
        vhost = _amqp091_vhost(parsed.path)
        return _UriCandidate(vhost=vhost, authority=authority) if vhost is not None else None

    if protocol == "amqp10":
        if parsed.path:
            return None
        valid_query, vhost = _amqp10_vhost(parsed.query)
        return _UriCandidate(vhost=vhost, authority=authority) if valid_query else None

    return None


def vhost_from_amqp091_uri(uri: str) -> str:
    """Extract a vhost from one valid AMQP 0-9-1-shaped URI.

    An absent path means RabbitMQ's default vhost ``"/"``. A present but empty
    path means the distinct empty-string vhost. Encoded slashes are allowed
    inside the single vhost segment; raw extra path segments are not.

    Raises:
        ValueError: If ``uri`` is not a valid hierarchical AMQP URI.
    """
    candidate = _parse_uri_candidate(uri, "amqp091")
    if candidate is None or candidate.vhost is None:
        raise ValueError("Invalid AMQP 0-9-1 URI")
    return candidate.vhost


def vhost_from_amqp10_uri(uri: str) -> str | None:
    """Extract a known vhost from one valid RabbitMQ AMQP 1.0 URI.

    RabbitMQ encodes the vhost as ``hostname=vhost:<name>`` in the query.
    ``None`` means the URI is invalid, omits that evidence, or contains an
    ambiguous hostname parameter. Literal plus signs are preserved.
    """
    candidate = _parse_uri_candidate(uri, "amqp10")
    return candidate.vhost if candidate is not None else None


def _uri_candidates(uri: object) -> tuple[str, ...] | None:
    """Normalize one URI or a non-empty failover list without dropping entries."""
    candidates = (uri,) if isinstance(uri, str) else tuple(uri) if isinstance(uri, list) else ()
    if not candidates or not all(isinstance(candidate, str) and candidate for candidate in candidates):
        return None
    return candidates


def _protocol_uri_evidence(uri: object, protocol: str | None) -> _UriEvidence:
    """Parse vhost and authority evidence once for every failover candidate.

    A network endpoint has ``authorities=None`` when any candidate is invalid;
    otherwise its non-empty tuple preserves candidate order and duplicates.
    The ``local`` protocol always has an empty authority tuple because it makes
    no network connection. Its vhost still resolves only from valid URI
    evidence.
    """
    if protocol not in ("amqp091", "amqp10", "local"):
        return _UriEvidence(vhost=None, authorities=None)

    candidates = _uri_candidates(uri)
    if candidates is None:
        return _UriEvidence(vhost=None, authorities=() if protocol == "local" else None)

    parsed_candidates: list[_UriCandidate] = []
    for candidate in candidates:
        parsed = _parse_uri_candidate(candidate, protocol)
        if parsed is None:
            return _UriEvidence(vhost=None, authorities=() if protocol == "local" else None)
        parsed_candidates.append(parsed)

    first_vhost = parsed_candidates[0].vhost
    vhost = (
        first_vhost
        if first_vhost is not None and all(candidate.vhost == first_vhost for candidate in parsed_candidates)
        else None
    )
    authorities = () if protocol == "local" else tuple(candidate.authority for candidate in parsed_candidates)
    return _UriEvidence(vhost=vhost, authorities=authorities)


def parse_amqp10_address(address: object) -> tuple[str, NodeKind, str | None] | None:
    """Parse a RabbitMQ AMQP 1.0 shovel address into a fixed resource.

    The parser accepts these current address-v2 and deprecated address-v1
    forms::

        /queues/:queue              v2 fixed queue
        /exchanges/:exchange/:key   v2 fixed exchange and routing key
        /exchanges/:exchange        v2 fixed exchange and empty routing key
        /amq/queue/:queue           v1 existing queue
        /queue/:queue or :queue     v1 queue
        /exchange/:exchange/:key    v1 fixed exchange and routing/binding key
        /exchange/:exchange         v1 exchange with no fixed routing key
        /topic/:routing-key         v1 amq.topic exchange

    The plural v2 exchange form uses ``""`` when its routing-key segment is
    omitted. Only the singular v1 form can mean that message metadata supplies
    the routing key.

    Returns:
        The resource name, kind, and optional routing key, or ``None`` when
        the address does not identify one fixed resource. For example, the
        AMQP null dynamic-addressing target does not name a resource.
    """
    parsed: tuple[str, NodeKind, str | None] | None = None
    if isinstance(address, str) and address:
        if not address.startswith("/"):
            parsed = (unquote(address), NodeKind.QUEUE, None)
        else:
            match address[1:].split("/"):
                case [kind, name] if kind in ("queue", "queues") and name:
                    parsed = (unquote(name), NodeKind.QUEUE, None)
                case ["amq", "queue", name] if name:
                    parsed = (unquote(name), NodeKind.QUEUE, None)
                case ["exchanges", exchange] if exchange:
                    parsed = (unquote(exchange), NodeKind.EXCHANGE, "")
                case ["exchanges", exchange, routing_key] if exchange:
                    parsed = (unquote(exchange), NodeKind.EXCHANGE, unquote(routing_key))
                case ["exchange", exchange] if exchange:
                    parsed = (unquote(exchange), NodeKind.EXCHANGE, None)
                case ["exchange", exchange, routing_key] if exchange:
                    parsed = (unquote(exchange), NodeKind.EXCHANGE, unquote(routing_key))
                case ["topic", routing_key]:
                    parsed = ("amq.topic", NodeKind.EXCHANGE, unquote(routing_key))
    return parsed


def shovel_side_resource(
    value: Mapping[str, object], side: str, protocol: str | None
) -> tuple[str, NodeKind, str | None] | None:
    """Resolve one shovel side to a fixed queue or exchange.

    The ``local`` and ``amqp091`` protocols use the same ``-queue``,
    ``-exchange``, and ``-exchange-key`` fields for resource identity.

    Returns:
        The resource name, kind, and optional routing key, or ``None`` when
        the side does not identify one fixed resource.
    """
    if protocol in ("amqp091", "local"):
        queue = value.get(f"{side}-queue")
        if isinstance(queue, str):
            return queue, NodeKind.QUEUE, None
        exchange = value.get(f"{side}-exchange")
        if isinstance(exchange, str):
            key = value.get(f"{side}-exchange-key")
            return exchange, NodeKind.EXCHANGE, key if isinstance(key, str) else None
        return None
    if protocol == "amqp10":
        return parse_amqp10_address(value.get(f"{side}-address"))
    return None


def _endpoint_is_confirmed_local(
    *,
    protocol: str | None,
    vhost: str | None,
    authorities: tuple[EndpointAuthority, ...] | None,
    in_cluster_amqp_hosts: frozenset[str],
) -> bool:
    """Return whether positive evidence admits an endpoint to this cluster graph."""
    if vhost is None:
        return False
    if protocol == "local":
        return True
    if authorities is None:
        return False
    return all(authority.host is None or authority.host in in_cluster_amqp_hosts for authority in authorities)


def parse_shovel_endpoint(
    value: Mapping[str, object],
    side: Literal["src", "dest"],
    *,
    in_cluster_amqp_hosts: frozenset[str],
) -> ResourceEndpoint:
    """Normalize all retained evidence for one shovel endpoint.

    ``is_confirmed_local`` is conservative: it is true only for a resolved
    local-protocol endpoint or a resolved AMQP failover set in which every URI
    either has no host component or names a host in the supplied in-cluster
    set. False means unconfirmed, not necessarily remote.
    """
    protocol_value = value.get(f"{side}-protocol", "amqp091")
    protocol = protocol_value if isinstance(protocol_value, str) else None
    uri_evidence = _protocol_uri_evidence(value.get(f"{side}-uri"), protocol)
    resource = shovel_side_resource(value, side, protocol)
    name, kind, routing_key = (None, None, None) if resource is None else resource

    return ResourceEndpoint(
        protocol=protocol,
        authorities=uri_evidence.authorities,
        vhost=uri_evidence.vhost,
        resource_name=name,
        resource_kind=kind,
        routing_key=routing_key,
        is_confirmed_local=_endpoint_is_confirmed_local(
            protocol=protocol,
            vhost=uri_evidence.vhost,
            authorities=uri_evidence.authorities,
            in_cluster_amqp_hosts=in_cluster_amqp_hosts,
        ),
    )
