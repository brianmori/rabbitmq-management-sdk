"""Broker round trips for raw names, binding identity, and UTF-8 credentials."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import uuid4

import pytest

from rabbitmq_management_sdk import (
    BindingRequest,
    ClassicQueueRequest,
    Config,
    ExchangeRequest,
    MalformedResponseError,
    QueueRequest,
    RabbitMQClient,
    VhostLimitName,
    VhostLimitRequest,
    VhostRequest,
)
from rabbitmq_management_sdk.http_adapter import factory
from rabbitmq_management_sdk.http_adapter.config import BasicAuthentication

if TYPE_CHECKING:
    from tests.conftest import RabbitSettings

pytestmark = pytest.mark.live


@pytest.mark.parametrize("destination_type", ["queue", "exchange"])
def test_raw_names_and_binding_keys_round_trip(
    rabbitmq_client_compatibility: RabbitMQClient,
    rabbit_config: RabbitSettings,
    destination_type: str,
) -> None:
    admin = rabbitmq_client_compatibility.admin
    vhost = f"sdk-{uuid4().hex}/?#%"
    admin.create_vhost(vhost, VhostRequest())
    try:
        assert admin.get_vhost(vhost).name == vhost
        admin.apply_vhost_limit(vhost, VhostLimitName.MAX_QUEUES, VhostLimitRequest(value=5))
        limits = admin.get_vhost_limits(vhost)
        assert limits is not None and limits.value.max_queues == 5
        admin.delete_vhost_limit(vhost, VhostLimitName.MAX_QUEUES)

        with RabbitMQClient(
            Config(
                host=rabbit_config.host,
                port=rabbit_config.port,
                username=rabbit_config.username,
                password=rabbit_config.password,
                virtual_host=vhost,
            )
        ) as client:
            source = "source/eu?#% café"
            destination = "destination/eu?#% café"
            key = "route/eu?#% café"
            client.exchanges.create(source, ExchangeRequest())
            assert client.exchanges.get(source).name == source
            if destination_type == "queue":
                client.queues.create(destination, QueueRequest(arguments=ClassicQueueRequest()))
                assert client.queues.get(destination).name == destination
                created = client.bindings.create_exchange_to_queue(source, destination, BindingRequest(routing_key=key))
                observed = client.bindings.list_exchange_to_queue(source, destination)
                assert observed == [created]
                client.bindings.delete_exchange_to_queue(source, destination, observed[0].properties_key)
                assert client.bindings.list_exchange_to_queue(source, destination) == []
                client.queues.delete(destination)
            else:
                client.exchanges.create(destination, ExchangeRequest())
                assert client.exchanges.get(destination).name == destination
                created = client.bindings.create_exchange_to_exchange(
                    source, destination, BindingRequest(routing_key=key)
                )
                observed = client.bindings.list_exchange_to_exchange(source, destination)
                assert observed == [created]
                client.bindings.delete_exchange_to_exchange(source, destination, observed[0].properties_key)
                assert client.bindings.list_exchange_to_exchange(source, destination) == []
                client.exchanges.delete(destination)
            assert created.vhost == vhost
            assert created.routing_key == key
            definitions = admin.export_vhost_definitions(vhost)
            assert definitions.original_vhost_name == vhost
            (limits_parameter,) = [p for p in definitions.parameters if p.component == "vhost-limits"]
            assert limits_parameter.value == []
            cluster_definitions = admin.export_definitions()
            assert all(
                p.component != "vhost-limits" or p.value == []
                for p in cluster_definitions.parameters
                if p.vhost == vhost
            )
            client.exchanges.delete(source)
    finally:
        admin.delete_vhost(vhost)


def test_non_ascii_vhost_crud(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    admin = rabbitmq_client_compatibility.admin
    vhost = f"sdk-{uuid4().hex}-café"
    admin.create_vhost(vhost, VhostRequest())
    try:
        assert admin.get_vhost(vhost).name == vhost
    finally:
        admin.delete_vhost(vhost)


def test_non_ascii_vhost_export_surfaces_broker_streaming_failure(
    rabbitmq_client_compatibility: RabbitMQClient,
) -> None:
    admin = rabbitmq_client_compatibility.admin
    vhost = f"sdk-{uuid4().hex}-café"
    admin.create_vhost(vhost, VhostRequest())
    try:
        try:
            definitions = admin.export_vhost_definitions(vhost)
        except MalformedResponseError as error:
            cause = error.__cause__
            if isinstance(cause, json.JSONDecodeError) and cause.doc.rstrip().endswith('"explanation":'):
                pytest.xfail("RabbitMQ 4.2/4.3 truncates non-ASCII vhost exports at the explanation field")
            raise
        assert definitions.original_vhost_name == vhost
    finally:
        admin.delete_vhost(vhost)


def test_default_vhost_is_available_without_preencoding(rabbitmq_client_compatibility: RabbitMQClient) -> None:
    admin = rabbitmq_client_compatibility.admin
    assert admin.get_vhost("/").name == "/"
    assert admin.export_vhost_definitions("/").original_vhost_name == "/"


def test_non_ascii_username_and_password_authenticate(
    rabbitmq_client_compatibility: RabbitMQClient,
    rabbit_config: RabbitSettings,
) -> None:
    username = f"sdk-{uuid4().hex}-usér"
    password = "päss-秘密"
    path = f"/api/users/{quote(username, safe='')}"
    auth = BasicAuthentication(rabbit_config.username, rabbit_config.password)
    with factory.create_adapter(
        rabbit_config.host,
        port=rabbit_config.port,
        default_headers={"Authorization": auth.auth_header},
    ) as admin:
        admin.request(method="PUT", path=path, json={"password": password, "tags": "administrator"})
        try:
            with RabbitMQClient(
                Config(
                    host=rabbit_config.host,
                    port=rabbit_config.port,
                    username=username,
                    password=password,
                )
            ) as client:
                assert client.version == rabbitmq_client_compatibility.version
                assert client.admin.get_vhost("/").name == "/"
        finally:
            admin.request(method="DELETE", path=path)
