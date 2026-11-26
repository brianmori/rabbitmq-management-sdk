"""Version requirements for settings emitted by v4 management writes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rabbitmq_management_sdk.exceptions import UnsupportedBrokerFeatureError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rabbitmq_management_sdk.client.config import RabbitMQVersion

type _Version = tuple[int, int, int]

_RABBITMQ_43 = (4, 3, 0)
QUORUM_POLICY_WRITE_VERSIONS = dict.fromkeys(
    ("delayed-retry-type", "delayed-retry-min", "delayed-retry-max", "consumer-disconnected-timeout"),
    _RABBITMQ_43,
)
QUORUM_QUEUE_WRITE_VERSIONS = {f"x-{key}": minimum for key, minimum in QUORUM_POLICY_WRITE_VERSIONS.items()}
SHOVEL_WRITE_VERSIONS = {"src-consumer-name": _RABBITMQ_43}


def validate_write_version(
    payload: Mapping[str, object],
    broker_version: RabbitMQVersion | None,
    minimum_versions: Mapping[str, _Version],
) -> None:
    """Reject version-gated settings in the serialized request before sending it."""
    if broker_version is None:
        return

    detected = (broker_version.major, broker_version.minor, broker_version.patch)
    for feature, minimum in minimum_versions.items():
        if feature in payload and detected < minimum:
            raise UnsupportedBrokerFeatureError(
                feature=feature,
                broker_version=str(broker_version),
                minimum_version=".".join(map(str, minimum)),
            )
