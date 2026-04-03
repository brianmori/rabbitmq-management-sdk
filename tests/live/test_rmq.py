import pytest
from rabbitmq_management_sdk.transport import factory, HttpAdapter, HttpResponse


@pytest.mark.live
def test_gh() -> None:
    ha: HttpAdapter = factory.create_adapter("https://www.github.com")
    hr : HttpResponse = ha.request(method="GET", path= "/")
    assert hr.status_code == 200