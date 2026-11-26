"""Normalize credential-free shovel endpoint and locality evidence.

Locality is conservative. A shovel endpoint contributes a resource edge only
when it identifies a fixed queue or exchange and positive evidence confirms
that resource belongs to the captured cluster. An unconfirmed endpoint is not
necessarily remote.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import SplitResult, unquote, unquote_plus, urlsplit

from rabbitmq_management_sdk.topology.models import (
    EndpointAuthority,
    NodeId,
    NodeKind,
    ResourceEndpoint,
    ShovelAckMode,
    ShovelAcknowledgement,
    ShovelAckSource,
    ShovelEndpointTransport,
    ShovelTransport,
)

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
_URI_QUERY_CHARACTERS = _URI_COMPONENT_CHARACTERS | frozenset("%:@/?")
_FORM_DECIMAL_REFERENCE = re.compile(r"&#(?:([0-9]*);)?")
_SHOVEL_PROTOCOLS = frozenset({"amqp091", "amqp10", "local"})
_EXPLICIT_ACK_MODES = {mode.value: mode for mode in ShovelAckMode if mode != ShovelAckMode.UNKNOWN}


def parse_shovel_acknowledgement(
    value: Mapping[str, object],
    source_protocol: str | None,
    destination_protocol: str | None,
    *,
    supported_broker: bool,
) -> ShovelAcknowledgement:
    """Resolve the captured mode or the supported dynamic shovel default."""
    if "ack-mode" in value:
        supplied = value["ack-mode"]
        mode = _EXPLICIT_ACK_MODES.get(supplied) if isinstance(supplied, str) else None
        if mode is None:
            return ShovelAcknowledgement(source=ShovelAckSource.INVALID_VALUE)
        return ShovelAcknowledgement(mode, ShovelAckSource.EXPLICIT)
    if supported_broker and source_protocol in _SHOVEL_PROTOCOLS and destination_protocol in _SHOVEL_PROTOCOLS:
        return ShovelAcknowledgement(ShovelAckMode.ON_CONFIRM, ShovelAckSource.BROKER_DEFAULT)
    return ShovelAcknowledgement()


def confirmed_local_resource_id(endpoint: ResourceEndpoint, *, cluster_id: str | None) -> NodeId | None:
    """Identify a fixed, confirmed-local endpoint without requiring a declaration."""
    if (
        not endpoint.is_confirmed_local
        or endpoint.vhost is None
        or endpoint.resource_name is None
        or endpoint.resource_kind is None
    ):
        return None
    return NodeId(cluster_id=cluster_id, vhost=endpoint.vhost, name=endpoint.resource_name, kind=endpoint.resource_kind)


@dataclass(frozen=True, slots=True)
class _UriCandidate:
    """Validated evidence from one URI in a shovel failover set."""

    vhost: str | None
    authority: EndpointAuthority
    has_explicit_port: bool


@dataclass(frozen=True, slots=True)
class _UriEvidence:
    """Combined vhost, authority, and locality evidence for a failover set."""

    vhost: str | None
    authorities: tuple[EndpointAuthority, ...] | None
    transport: ShovelEndpointTransport
    is_confirmed_local: bool = False


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


def _parse_base_uri(uri: str, protocol: str) -> tuple[SplitResult, EndpointAuthority] | None:
    """Validate common AMQP URI syntax and normalize its safe authority."""
    if not uri or _contains_disallowed_raw_character(uri) or not _has_valid_percent_encoding(uri) or "#" in uri:
        return None

    raw_scheme, separator, _remainder = uri.partition("://")
    scheme = raw_scheme if protocol == "amqp10" else raw_scheme.casefold()
    if not separator or scheme not in _DEFAULT_PORT_BY_SCHEME:
        return None

    try:
        parsed = urlsplit(uri)
        raw_authority_is_valid = _has_valid_raw_authority(parsed.netloc)
        parsed_port = parsed.port
        port = parsed_port if parsed_port is not None else _DEFAULT_PORT_BY_SCHEME[scheme]
        host = parsed.hostname
        # The AMQP 1.0 client passes the parsed host directly to its socket.
        if host is not None and protocol != "amqp10":
            host = unquote(host, errors="strict")
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


def _form_decimal_character(match: re.Match[str]) -> str:
    """Decode one decimal reference as Erlang's form decoder does."""
    digits = match[1]
    if digits is None:
        raise ValueError("Invalid decimal reference in AMQP 1.0 URI query")
    character = chr(int(digits.lstrip("0") or "0"))
    character.encode("utf-8")  # Reject surrogate code points.
    return character


def _decode_form_component(component: str) -> str:
    """Decode URL form escapes and then decimal character references once."""
    return _FORM_DECIMAL_REFERENCE.sub(_form_decimal_character, unquote_plus(component, errors="strict"))


def _amqp10_vhost(query: str) -> tuple[bool, str | None]:
    """Resolve RabbitMQ's AMQP 1.0 hostname convention from one query."""
    if any(character not in _URI_QUERY_CHARACTERS for character in query):
        return False, None

    hostnames: list[str] = []
    try:
        for pair in query.split("&") if query else ():
            raw_key, _separator, raw_value = pair.partition("=")
            key = _decode_form_component(raw_key)
            value = _decode_form_component(raw_value)
            if key == "hostname":
                hostnames.append(value)
        if len(hostnames) != 1:
            return True, None
        # RabbitMQ passes the decoded hostname code points to list_to_binary.
        # Resolve a vhost only when those wire bytes identify valid UTF-8.
        hostname = hostnames[0].encode("latin-1").decode("utf-8")
    except (OverflowError, ValueError):
        return False, None

    prefix = "vhost:"
    return True, hostname[len(prefix) :] if hostname.startswith(prefix) else None


def _parse_uri_candidate(uri: str, protocol: str) -> _UriCandidate | None:
    """Parse one protocol-aware URI without retaining credentials."""
    parsed_base = _parse_base_uri(uri, protocol)
    if parsed_base is None:
        return None
    parsed, authority = parsed_base

    has_explicit_port = parsed.port is not None
    if protocol in ("amqp091", "local"):
        vhost = _amqp091_vhost(parsed.path)
        return _UriCandidate(vhost, authority, has_explicit_port) if vhost is not None else None

    if protocol == "amqp10":
        if parsed.path:
            return None
        valid_query, vhost = _amqp10_vhost(parsed.query)
        return _UriCandidate(vhost, authority, has_explicit_port) if valid_query else None

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
    ambiguous hostname parameter. A raw plus in the query decodes to a space;
    use ``%2B`` for a literal plus.
    """
    candidate = _parse_uri_candidate(uri, "amqp10")
    return candidate.vhost if candidate is not None else None


def _is_direct_uri(candidate: _UriCandidate, protocol: str) -> bool:
    """Check the hostless URI form used by RabbitMQ's direct AMQP client."""
    return (
        protocol in ("amqp091", "local")
        and candidate.authority.host is None
        and candidate.authority.scheme == "amqp"
        and not candidate.has_explicit_port
    )


def _candidate_transport(candidate: _UriCandidate | None, protocol: str, *, supported_broker: bool) -> ShovelTransport:
    """Classify protocol and authority evidence independently of locality."""
    if candidate is None:
        return ShovelTransport.UNKNOWN
    authority = candidate.authority
    if authority.host is None:
        if _is_direct_uri(candidate, protocol) and (supported_broker or protocol == "local"):
            return ShovelTransport.LOCAL
        return ShovelTransport.UNKNOWN
    if protocol == "local":
        return ShovelTransport.LOCAL
    return ShovelTransport.NETWORK_AMQPS if authority.scheme == "amqps" else ShovelTransport.NETWORK_AMQP


def _protocol_uri_evidence(
    uri: object, protocol: str | None, *, in_cluster_amqp_hosts: frozenset[str], supported_broker: bool
) -> _UriEvidence:
    """Parse candidate vhost and authority evidence and confirm locality.

    A network endpoint has ``authorities=None`` when any candidate is invalid;
    otherwise its non-empty tuple preserves candidate order and duplicates.
    The ``local`` protocol always has an empty authority tuple because it makes
    no network connection. Its vhost still resolves only from valid URI
    evidence. Transport preserves the classifications of individual entries
    even when their combined authority evidence cannot be normalized.
    """
    candidates: tuple[object, ...] = (uri,) if isinstance(uri, str) else tuple(uri) if isinstance(uri, list) else ()
    if protocol is None or protocol not in _SHOVEL_PROTOCOLS:
        return _UriEvidence(None, None, ShovelEndpointTransport((ShovelTransport.UNKNOWN,) * len(candidates)))

    parsed = tuple(_parse_uri_candidate(item, protocol) if isinstance(item, str) else None for item in candidates)
    transport = ShovelEndpointTransport(
        tuple(_candidate_transport(item, protocol, supported_broker=supported_broker) for item in parsed)
    )
    parsed_candidates = tuple(item for item in parsed if item is not None)
    if not parsed_candidates or len(parsed_candidates) != len(candidates):
        return _UriEvidence(None, () if protocol == "local" else None, transport)

    first_vhost = parsed_candidates[0].vhost
    vhost = (
        first_vhost
        if first_vhost is not None and all(candidate.vhost == first_vhost for candidate in parsed_candidates)
        else None
    )
    authorities = () if protocol == "local" else tuple(candidate.authority for candidate in parsed_candidates)
    is_confirmed_local = vhost is not None and all(
        _is_direct_uri(candidate, protocol)
        if candidate.authority.host is None
        else protocol == "local" or candidate.authority.host in in_cluster_amqp_hosts
        for candidate in parsed_candidates
    )
    return _UriEvidence(vhost, authorities, transport, is_confirmed_local)


def _amqp10_address_v2(address: str, side: Literal["src", "dest"]) -> tuple[str, NodeKind, str | None] | None:
    """Decode the captured v2 components without accepting malformed escapes."""
    if not _has_valid_percent_encoding(address):
        return None
    try:
        match address.split("/"):
            case ["", "queues", name] if name:
                return unquote(name, errors="strict"), NodeKind.QUEUE, None
            case ["", "exchanges", name] if name and side == "dest":
                return unquote(name, errors="strict"), NodeKind.EXCHANGE, ""
            case ["", "exchanges", name, key] if name and side == "dest":
                return unquote(name, errors="strict"), NodeKind.EXCHANGE, unquote(key, errors="strict")
    except UnicodeDecodeError:
        return None
    return None


def _amqp10_address_v1(address: str, side: Literal["src", "dest"]) -> tuple[str, NodeKind, str | None] | None:
    """Decode legacy address components after splitting on raw slashes."""
    match address.split("/"):
        case [name] | ["", "queue", name] | ["", "amq", "queue", name] if name:
            return name.replace("%2F", "/"), NodeKind.QUEUE, None
        case ["", "exchange", name] if name:
            return name.replace("%2F", "/"), NodeKind.EXCHANGE, None
        case ["", "exchange", name, routing_key] if name:
            key = None if side == "dest" and not routing_key else routing_key.replace("%2F", "/")
            return name.replace("%2F", "/"), NodeKind.EXCHANGE, key
        case ["", "topic", routing_key] if routing_key:
            return "amq.topic", NodeKind.EXCHANGE, routing_key.replace("%2F", "/")
    return None


def parse_amqp10_address(address: object, *, side: Literal["src", "dest"]) -> tuple[str, NodeKind, str | None] | None:
    """Parse a RabbitMQ AMQP 1.0 shovel address into a fixed resource.

    The parser accepts these current address-v2 and deprecated address-v1
    forms::

        /queues/:queue              v2 source or target queue
        /exchanges/:exchange/:key   v2 target exchange and routing key
        /exchanges/:exchange        v2 target exchange and empty routing key
        /amq/queue/:queue           v1 existing queue
        /queue/:queue or :queue     v1 queue
        /exchange/:exchange/:key    v1 fixed exchange and routing/binding key
        /exchange/:exchange         v1 exchange with no fixed routing key
        /topic/:routing-key         v1 amq.topic exchange

    The plural v2 exchange form uses ``""`` when its routing-key segment is
    omitted. A v2 exchange source is unsupported and remains unresolved. On a
    singular v1 target, both an omitted key and an empty trailing segment mean
    that message metadata supplies the routing key. Deprecated v1 addresses
    decode only uppercase ``%2F`` and require the broker to permit the
    ``amqp_address_v1`` feature.

    Returns:
        The resource name, kind, and optional routing key, or ``None`` when
        the address does not identify one fixed resource. For example, the
        AMQP null dynamic-addressing target does not name a resource.
    """
    if not isinstance(address, str) or not address:
        return None
    if address.startswith(("/queues/", "/exchanges/")):
        return _amqp10_address_v2(address, side)
    return _amqp10_address_v1(address, side)


def shovel_side_resource(
    value: Mapping[str, object], side: Literal["src", "dest"], protocol: str | None
) -> tuple[str, NodeKind, str | None] | None:
    """Resolve one shovel side to a fixed queue or exchange.

    The ``local`` and ``amqp091`` protocols use the same ``-queue``,
    ``-exchange``, and ``-exchange-key`` fields for resource identity. An empty
    source exchange key selects a generated queue's binding key, which cannot
    be resolved from the parameter; an empty destination key stays literal.

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
            fixed_key = key if isinstance(key, str) and not (side == "src" and key == "") else None
            return exchange, NodeKind.EXCHANGE, fixed_key
        return None
    if protocol == "amqp10":
        return parse_amqp10_address(value.get(f"{side}-address"), side=side)
    return None


def parse_shovel_endpoint(
    value: Mapping[str, object],
    side: Literal["src", "dest"],
    *,
    in_cluster_amqp_hosts: frozenset[str],
    supported_broker: bool = False,
) -> ResourceEndpoint:
    """Normalize all retained evidence for one shovel endpoint.

    A confirmed endpoint requires a shared vhost and local evidence for each
    candidate. Hostless candidates require the direct AMQP URI form. Hosted
    network candidates require the operator's in-cluster host mapping.
    """
    protocol_value = value.get(f"{side}-protocol", "amqp091")
    protocol = protocol_value if isinstance(protocol_value, str) else None
    uri_evidence = _protocol_uri_evidence(
        value.get(f"{side}-uri"),
        protocol,
        in_cluster_amqp_hosts=in_cluster_amqp_hosts,
        supported_broker=supported_broker,
    )
    resource = shovel_side_resource(value, side, protocol)
    name, kind, routing_key = (None, None, None) if resource is None else resource

    return ResourceEndpoint(
        protocol=protocol,
        authorities=uri_evidence.authorities,
        vhost=uri_evidence.vhost,
        resource_name=name,
        resource_kind=kind,
        routing_key=routing_key,
        is_confirmed_local=uri_evidence.is_confirmed_local,
        transport=uri_evidence.transport,
    )
