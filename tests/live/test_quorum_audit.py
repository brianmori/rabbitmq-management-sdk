"""Quorum audit compatibility on disposable RabbitMQ 4.2 and 4.3 brokers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk import (
    ClusterAuditor,
    DeadLetterStrategy,
    ExchangeRequest,
    OverflowBehaviour,
    PolicyApplyTo,
    PolicyDefinition,
    PolicyRequest,
    QueueRequest,
    QuorumQueueRequest,
)
from rabbitmq_management_sdk.topology import ConfigurationStatus, ConfigurationUncertainty, QuorumConfigurationIssue

if TYPE_CHECKING:
    from rabbitmq_management_sdk import RabbitMQClient

pytestmark = pytest.mark.live


def test_quorum_audit_records_version_semantics_and_uncaptured_global_defaults(live_client: RabbitMQClient) -> None:
    live_client.queues.create("work", QueueRequest(arguments=QuorumQueueRequest(delivery_limit=-1)))
    auditor = ClusterAuditor(
        live_client.admin.export_definitions(),
        queues=live_client.queues.list_all(disable_stats=True),
        exchanges=live_client.exchanges.list_all(disable_stats=True),
    )
    (result,) = auditor.quorum_delivery_configuration()
    config = result.configuration
    assert config.delivery_limit.value == -1
    assert config.delivery_count_basis.value == ("failed" if live_client.version.minor == 3 else "acquired")
    assert config.effective_dead_letter_strategy.value == "disabled"
    assert not result.issues
    if live_client.version.minor == 2:
        assert config.delayed_retry_type.status == ConfigurationStatus.NOT_APPLICABLE
        assert config.consumer_timeout.status == ConfigurationStatus.NOT_APPLICABLE
    else:
        assert config.delayed_retry_type.value == "disabled"
        assert config.consumer_timeout.uncertainties == (ConfigurationUncertainty.BROKER_DEFAULT_NOT_CAPTURED,)
    assert auditor.audit().quorum_delivery_configuration == (result,)


def test_audit_reports_broker_dead_letter_and_overflow_fallback(live_client: RabbitMQClient) -> None:
    live_client.exchanges.create("dlx", ExchangeRequest(type="fanout"))
    live_client.policies.create(
        "fallback",
        PolicyRequest(
            pattern="^work$",
            priority=1,
            apply_to=PolicyApplyTo.QUORUM_QUEUES,
            definition=PolicyDefinition(
                dead_letter_exchange="dlx",
                dead_letter_strategy=DeadLetterStrategy.AT_LEAST_ONCE,
                overflow=OverflowBehaviour.REJECT_PUBLISH_DLX,
            ),
        ),
    )
    live_client.queues.create("work", QueueRequest(arguments=QuorumQueueRequest()))
    auditor = ClusterAuditor(
        live_client.admin.export_definitions(),
        queues=live_client.queues.list_all(disable_stats=True),
        exchanges=live_client.exchanges.list_all(disable_stats=True),
    )
    (result,) = auditor.quorum_delivery_configuration()
    assert result.configuration.overflow.value == "drop-head"
    assert result.configuration.dead_letter_strategy.value == "at-least-once"
    assert result.configuration.effective_dead_letter_strategy.value == "at-most-once"
    assert QuorumConfigurationIssue.AT_LEAST_ONCE_FALLBACK in result.issues
