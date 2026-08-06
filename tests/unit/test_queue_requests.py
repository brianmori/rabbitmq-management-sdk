"""Tests for queue request values shared with other resource domains."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rabbitmq_management_sdk import (
    ClassicQueueRequest,
    OverflowBehaviour,
    QuorumQueueOverflow,
    QuorumQueueRequest,
)


@pytest.mark.unit
def test_classic_queue_accepts_shared_reject_publish_dlx_behaviour() -> None:
    request = ClassicQueueRequest(overflow=OverflowBehaviour.REJECT_PUBLISH_DLX)

    assert request.model_dump(by_alias=True, exclude_none=True)["x-overflow"] == "reject-publish-dlx"


@pytest.mark.unit
def test_quorum_queue_keeps_its_narrower_overflow_domain() -> None:
    request = QuorumQueueRequest(overflow=QuorumQueueOverflow.REJECT_PUBLISH)
    assert request.overflow == "reject-publish"

    with pytest.raises(ValidationError):
        QuorumQueueRequest.model_validate({"x-overflow": "reject-publish-dlx"})
