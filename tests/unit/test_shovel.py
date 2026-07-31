"""Test pure shovel parsing against RabbitMQ's URI and addressing specifications."""

import pytest

from rabbitmq_management_sdk.topology import EndpointAuthority, NodeKind, ResourceEndpoint
from rabbitmq_management_sdk.topology.shovel import (
    parse_amqp10_address,
    parse_shovel_endpoint,
    shovel_side_resource,
    vhost_from_amqp091_uri,
    vhost_from_amqp10_uri,
)

pytestmark = pytest.mark.unit


def _parse_endpoint(
    uri: object,
    protocol: object = "amqp091",
    *,
    in_cluster_amqp_hosts: frozenset[str] = frozenset(),
) -> ResourceEndpoint:
    return parse_shovel_endpoint(
        {
            "src-protocol": protocol,
            "src-uri": uri,
            "src-queue": "orders.q",
        },
        "src",
        in_cluster_amqp_hosts=in_cluster_amqp_hosts,
    )


class TestVhostFromAmqp091Uri:
    """RabbitMQ URI spec: https://www.rabbitmq.com/docs/uri-spec.
    "Absent" vhost (no "/" after host) and "empty" vhost ("/" with
    nothing after it) are documented as genuinely different things."""

    @pytest.mark.parametrize(
        ("uri", "expected"),
        [
            ("amqp://", "/"),
            ("amqp://:@/", ""),
            ("amqp://user@", "/"),
            ("amqp://user:pass@", "/"),
            ("amqp://:10000", "/"),
            ("amqp://[::1]", "/"),
            ("amqp://user@/my-vhost", "my-vhost"),
            ("amqp://server-name", "/"),
            ("amqp://user:password@server-name/my-vhost", "my-vhost"),
            (
                "amqps://user:password@server-name?cacertfile=/path/to/cacert.pem&certfile=/path/to/cert.pem"
                "&keyfile=/path/to/key.pem&verify=verify_peer",
                "/",
            ),
            (
                "amqps://server-name?cacertfile=/path/to/cacert.pem&certfile=/path/to/cert.pem"
                "&keyfile=/path/to/key.pem&verify=verify_peer&auth_mechanism=external",
                "/",
            ),
            ("amqp://user:pass@server-name:5672/my-vhost", "my-vhost"),  # port shouldn't confuse path parsing
        ],
    )
    def test_documented_examples(self, uri: str, expected: str) -> None:
        assert vhost_from_amqp091_uri(uri) == expected

    def test_bare_trailing_slash_is_empty_string_not_default(self) -> None:
        """The spec's sharpest edge case: a bare trailing "/" means the
        vhost IS the empty string, not the default vhost "/"."""
        assert vhost_from_amqp091_uri("amqp://server-name/") == ""

    @pytest.mark.parametrize("encoded", ["%2f", "%2F"])
    def test_percent_encoded_default_vhost(self, encoded: str) -> None:
        assert vhost_from_amqp091_uri(f"amqp://server-name/{encoded}") == "/"

    def test_invalid_uri_raises_without_echoing_credentials(self) -> None:
        with pytest.raises(ValueError, match="Invalid AMQP 0-9-1 URI") as exc_info:
            vhost_from_amqp091_uri("amqp://user:secret:extra@server-name/orders")

        assert "secret" not in str(exc_info.value)


class TestVhostFromAmqp10Uri:
    """RabbitMQ has no protocol-level vhost concept in AMQP 1.0; it
    overloads the connection `hostname` field, carried here as the
    `hostname` query parameter: `?hostname=vhost:name`."""

    def test_real_shovel_uri(self) -> None:
        uri = "amqp://lab:lab@localhost:5672?hostname=vhost:my-vhost&sasl=plain"
        assert vhost_from_amqp10_uri(uri) == "my-vhost"

    def test_rabbitmqadmin_docs_example(self) -> None:
        uri = "amqp://username:s3KrE7@source.hostname:5672?hostname=vhost:src-vhost"
        assert vhost_from_amqp10_uri(uri) == "src-vhost"

    def test_explicit_default_vhost(self) -> None:
        assert vhost_from_amqp10_uri("amqp://user@host:5672?hostname=vhost:/&sasl=plain") == "/"

    def test_hostname_absent_returns_none_not_default(self) -> None:
        """Can't assume "/" here: the fallback is the broker's configured
        default_vhost, which isn't derivable from the URI."""
        assert vhost_from_amqp10_uri("amqp://user@host:5672?sasl=plain") is None

    def test_hostname_present_but_not_vhost_prefixed_returns_none(self) -> None:
        uri = "amqp://user@host:5672?hostname=some.real.tls.sni.name&sasl=plain"
        assert vhost_from_amqp10_uri(uri) is None

    def test_literal_plus_in_vhost_name_is_preserved(self) -> None:
        """urllib.parse.parse_qs would decode '+' as a space (form-encoding
        convention) -- this must NOT use that, or a vhost containing a
        literal '+' gets silently corrupted."""
        assert vhost_from_amqp10_uri("amqp://user@host:5672?hostname=vhost:my+vhost") == "my+vhost"

    def test_percent_encoded_plus_in_vhost_name(self) -> None:
        assert vhost_from_amqp10_uri("amqp://user@host:5672?hostname=vhost:my%2Bvhost") == "my+vhost"


class TestParseShovelEndpoint:
    def test_combines_normalized_endpoint_evidence(self) -> None:
        endpoint = parse_shovel_endpoint(
            {
                "src-protocol": "amqp091",
                "src-uri": "amqps://user:secret@RABBIT.example/orders",
                "src-exchange": "events",
                "src-exchange-key": "order.#",
            },
            "src",
            in_cluster_amqp_hosts=frozenset({"rabbit.example"}),
        )

        assert endpoint.protocol == "amqp091"
        assert endpoint.authorities == (EndpointAuthority(scheme="amqps", host="rabbit.example", port=5671),)
        assert endpoint.vhost == "orders"
        assert endpoint.resource_name == "events"
        assert endpoint.resource_kind == NodeKind.EXCHANGE
        assert endpoint.routing_key == "order.#"
        assert endpoint.is_confirmed_local is True
        assert "user" not in repr(endpoint.authorities)
        assert "secret" not in repr(endpoint.authorities)

    def test_percent_decodes_and_casefolds_host_before_locality_check(self) -> None:
        endpoint = _parse_endpoint(
            "AMQPS://user:secret@RABB%49T.Example/orders",
            in_cluster_amqp_hosts=frozenset({"rabbit.example"}),
        )

        assert endpoint.authorities == (EndpointAuthority(scheme="amqps", host="rabbit.example", port=5671),)
        assert endpoint.vhost == "orders"
        assert endpoint.is_confirmed_local is True

    def test_percent_encoded_userinfo_delimiters_remain_valid(self) -> None:
        endpoint = _parse_endpoint(
            "amqp://us%40er:pa%3Ass@rabbit.example/orders",
            in_cluster_amqp_hosts=frozenset({"rabbit.example"}),
        )

        assert endpoint.authorities == (EndpointAuthority(scheme="amqp", host="rabbit.example", port=5672),)
        assert endpoint.vhost == "orders"
        assert endpoint.is_confirmed_local is True

    def test_rabbitmq_percent_encoded_components_example(self) -> None:
        endpoint = _parse_endpoint(
            "amqp://user%61:%61pass@ho%61st:10000/v%2fhost",
            in_cluster_amqp_hosts=frozenset({"hoast"}),
        )

        assert endpoint.authorities == (EndpointAuthority(scheme="amqp", host="hoast", port=10000),)
        assert endpoint.vhost == "v/host"
        assert endpoint.is_confirmed_local is True

    def test_preserves_failover_order_and_duplicates(self) -> None:
        endpoint = _parse_endpoint(
            [
                "amqp://rabbit-2/orders",
                "amqp://rabbit-1:5673/orders",
                "amqp://rabbit-2/orders",
            ]
        )

        assert endpoint.authorities == (
            EndpointAuthority(scheme="amqp", host="rabbit-2", port=5672),
            EndpointAuthority(scheme="amqp", host="rabbit-1", port=5673),
            EndpointAuthority(scheme="amqp", host="rabbit-2", port=5672),
        )
        assert endpoint.vhost == "orders"

    def test_requires_every_failover_host_to_be_confirmed(self) -> None:
        endpoint = parse_shovel_endpoint(
            {
                "dest-protocol": "amqp091",
                "dest-uri": ["amqp://rabbit-1/orders", "amqp://rabbit-2/orders"],
                "dest-queue": "orders.q",
            },
            "dest",
            in_cluster_amqp_hosts=frozenset({"rabbit-1"}),
        )

        assert endpoint.is_confirmed_local is False
        assert endpoint.resource_name == "orders.q"
        assert endpoint.authorities is not None
        assert {authority.host for authority in endpoint.authorities} == {"rabbit-1", "rabbit-2"}

    @pytest.mark.parametrize(
        "uri",
        [
            "amqp:/orders",
            "amqp:orders",
            "mqtt://rabbit/orders",
        ],
    )
    def test_requires_supported_hierarchical_amqp_scheme(self, uri: str) -> None:
        endpoint = _parse_endpoint(uri)

        assert endpoint.authorities is None
        assert endpoint.vhost is None
        assert endpoint.is_confirmed_local is False

    @pytest.mark.parametrize(
        "uri",
        [
            "amqp://rabbit/%ZZ",
            "amqp://rabbit/%FF",
            "amqp://[broken/orders",
            "amqp://[v1.foo^bar]/orders",
            "amqp://rabbit:not-a-port/orders",
            "amqp://rabbit:0/orders",
            "amqp://rabbit:65536/orders",
            "amqp://rabbit/orders#fragment",
            "amqp://rabbit/my vhost",
            "amqp://evil@@/orders",
            "amqp://user:pa:ss@rabbit/orders",
            "amqp://%FF@rabbit/orders",
            r"amqp://rabbit\evil/orders",
        ],
    )
    def test_malformed_candidate_invalidates_all_network_evidence(self, uri: str) -> None:
        endpoint = _parse_endpoint(uri)

        assert endpoint.authorities is None
        assert endpoint.vhost is None
        assert endpoint.is_confirmed_local is False

    def test_raw_multi_segment_amqp091_vhost_is_invalid(self) -> None:
        endpoint = _parse_endpoint("amqp://rabbit/tenant/orders")

        assert endpoint.authorities is None
        assert endpoint.vhost is None

    def test_percent_encoded_slash_is_one_valid_amqp091_vhost_segment(self) -> None:
        endpoint = _parse_endpoint(
            "amqp://rabbit/tenant%2Forders",
            in_cluster_amqp_hosts=frozenset({"rabbit"}),
        )

        assert endpoint.authorities == (EndpointAuthority(scheme="amqp", host="rabbit", port=5672),)
        assert endpoint.vhost == "tenant/orders"
        assert endpoint.is_confirmed_local is True

    @pytest.mark.parametrize(
        "uri",
        [
            "amqp://rabbit/?hostname=vhost:orders",
            "amqp://rabbit/path?hostname=vhost:orders",
        ],
    )
    def test_amqp10_rejects_uri_paths(self, uri: str) -> None:
        endpoint = _parse_endpoint(uri, "amqp10")

        assert endpoint.authorities is None
        assert endpoint.vhost is None

    @pytest.mark.parametrize(
        "query",
        [
            "sasl=plain",
            "hostname=broker.example",
            "hostname=vhost:orders&hostname=vhost:orders",
            "hostname=vhost:orders&hostname=vhost:backup",
        ],
    )
    def test_amqp10_unknown_or_duplicate_hostname_retains_authority(self, query: str) -> None:
        endpoint = _parse_endpoint(f"amqp://broker.example?{query}", "amqp10")

        assert endpoint.authorities == (EndpointAuthority(scheme="amqp", host="broker.example", port=5672),)
        assert endpoint.vhost is None
        assert endpoint.is_confirmed_local is False

    def test_amqp10_rejects_invalid_utf8_hostname_evidence(self) -> None:
        endpoint = _parse_endpoint("amqp://broker.example?hostname=vhost:%FF", "amqp10")

        assert endpoint.authorities is None
        assert endpoint.vhost is None
        assert endpoint.is_confirmed_local is False

    def test_invalid_failover_candidate_invalidates_complete_network_set(self) -> None:
        endpoint = _parse_endpoint(
            ["amqp://rabbit-1/orders", "amqp://evil@@/orders"],
            in_cluster_amqp_hosts=frozenset({"rabbit-1"}),
        )

        assert endpoint.authorities is None
        assert endpoint.vhost is None
        assert endpoint.is_confirmed_local is False

    def test_mixed_failover_vhosts_retain_authorities_but_not_vhost(self) -> None:
        endpoint = _parse_endpoint(
            ["amqp://rabbit-1/orders", "amqp://rabbit-2/backup"],
            in_cluster_amqp_hosts=frozenset({"rabbit-1", "rabbit-2"}),
        )

        assert endpoint.authorities == (
            EndpointAuthority(scheme="amqp", host="rabbit-1", port=5672),
            EndpointAuthority(scheme="amqp", host="rabbit-2", port=5672),
        )
        assert endpoint.vhost is None
        assert endpoint.is_confirmed_local is False

    @pytest.mark.parametrize("protocol", [None, 123, "mqtt"])
    def test_unknown_or_non_string_protocol_is_unresolved(self, protocol: object) -> None:
        endpoint = _parse_endpoint("amqp://rabbit/orders", protocol)

        assert endpoint.authorities is None
        assert endpoint.vhost is None
        assert endpoint.is_confirmed_local is False

    def test_local_protocol_has_no_network_authority(self) -> None:
        endpoint = _parse_endpoint("amqp://ignored/orders", "local")

        assert endpoint.authorities == ()
        assert endpoint.vhost == "orders"
        assert endpoint.is_confirmed_local is True

    def test_local_failover_list_requires_one_shared_vhost(self) -> None:
        endpoint = _parse_endpoint(
            ["amqp://ignored-1/orders", "amqps://ignored-2/orders"],
            "local",
        )

        assert endpoint.authorities == ()
        assert endpoint.vhost == "orders"
        assert endpoint.is_confirmed_local is True

    @pytest.mark.parametrize(
        "uri",
        [
            [],
            "amqp:/orders",
            "amqp://ignored/tenant/orders",
            "amqp://evil@@/orders",
            "amqp://[v1.foo^bar]/orders",
            ["amqp://ignored/orders", "amqp://ignored/backup"],
            ["amqp://ignored/orders", "amqp://evil@@/orders"],
        ],
    )
    def test_invalid_or_ambiguous_local_uri_is_not_confirmed(self, uri: object) -> None:
        endpoint = _parse_endpoint(uri, "local")

        assert endpoint.authorities == ()
        assert endpoint.vhost is None
        assert endpoint.is_confirmed_local is False


class TestParseAmqp10Address:
    """RabbitMQ 4.2 AMQP 1.0 address v2 and deprecated v1 formats."""

    def test_real_shovel_address(self) -> None:
        assert parse_amqp10_address("/queues/src.queue.10") == ("src.queue.10", NodeKind.QUEUE, None)

    def test_exchange_with_routing_key(self) -> None:
        assert parse_amqp10_address("/exchanges/out.ex/some.key") == (
            "out.ex",
            NodeKind.EXCHANGE,
            "some.key",
        )

    def test_v2_exchange_without_routing_key_uses_fixed_empty_key(self) -> None:
        assert parse_amqp10_address("/exchanges/out.ex") == ("out.ex", NodeKind.EXCHANGE, "")

    def test_v1_exchange_without_routing_key_has_no_fixed_key(self) -> None:
        assert parse_amqp10_address("/exchange/out.ex") == ("out.ex", NodeKind.EXCHANGE, None)

    def test_v1_exchange_with_explicit_empty_routing_key_preserves_it(self) -> None:
        assert parse_amqp10_address("/exchange/out.ex/") == ("out.ex", NodeKind.EXCHANGE, "")

    def test_topic_address_resolves_to_amq_topic(self) -> None:
        assert parse_amqp10_address("/topic/routing.key.here") == (
            "amq.topic",
            NodeKind.EXCHANGE,
            "routing.key.here",
        )

    def test_bare_address_is_deprecated_v1_queue_shorthand(self) -> None:
        assert parse_amqp10_address("bare.queue.name") == ("bare.queue.name", NodeKind.QUEUE, None)

    def test_v1_existing_queue_address(self) -> None:
        assert parse_amqp10_address("/amq/queue/existing.q") == ("existing.q", NodeKind.QUEUE, None)

    def test_percent_encoded_segments_are_decoded(self) -> None:
        assert parse_amqp10_address("/queues/my%2Bqueue") == ("my+queue", NodeKind.QUEUE, None)

    @pytest.mark.parametrize(
        "address",
        [
            None,
            "",
            123,
            "/unrecognized-segment/name",
            "/queues/name/unencoded/extra",
            "/exchanges/name/unencoded/extra",
        ],
    )
    def test_unresolvable_returns_none(self, address: object) -> None:
        assert parse_amqp10_address(address) is None


class TestShovelSideResource:
    def test_amqp091_queue(self) -> None:
        value = {"src-queue": "orders"}
        assert shovel_side_resource(value, "src", "amqp091") == ("orders", NodeKind.QUEUE, None)

    def test_amqp091_exchange_with_key(self) -> None:
        value = {"src-exchange": "events.ex", "src-exchange-key": "order.#"}
        assert shovel_side_resource(value, "src", "amqp091") == (
            "events.ex",
            NodeKind.EXCHANGE,
            "order.#",
        )

    def test_amqp091_neither_queue_nor_exchange_returns_none(self) -> None:
        assert shovel_side_resource({}, "dest", "amqp091") is None

    def test_amqp10_delegates_to_address_parsing(self) -> None:
        value = {"dest-address": "/queues/dst.queue.10"}
        assert shovel_side_resource(value, "dest", "amqp10") == ("dst.queue.10", NodeKind.QUEUE, None)

    def test_unrecognized_protocol_returns_none(self) -> None:
        assert shovel_side_resource({"src-queue": "x"}, "src", "mqtt") is None


class TestShovelSideResourceLocal:
    def test_local_protocol_queue(self) -> None:
        """Real shape: test-shovel-local's src-queue/dest-queue fields,
        identical to amqp091's."""
        value = {"src-queue": "src.q"}
        assert shovel_side_resource(value, "src", "local") == ("src.q", NodeKind.QUEUE, None)

    def test_local_protocol_exchange_with_key(self) -> None:
        value = {"src-exchange": "events.ex", "src-exchange-key": "order.#"}
        assert shovel_side_resource(value, "src", "local") == (
            "events.ex",
            NodeKind.EXCHANGE,
            "order.#",
        )

    def test_local_protocol_neither_queue_nor_exchange_returns_none(self) -> None:
        assert shovel_side_resource({}, "dest", "local") is None
