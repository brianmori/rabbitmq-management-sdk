import pytest


@pytest.mark.unit
def test_true() -> None:
    assert True


@pytest.mark.integration
def test_addition() -> None:
    assert 1 + 1 == 2


@pytest.mark.live
def test_live_skipped() -> None:
    pytest.skip("live test — requires RabbitMQ")
