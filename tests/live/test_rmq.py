from typing import TYPE_CHECKING

import pytest

from rabbitmq_management_sdk.http_adapter import HttpAdapter, HttpResponse, factory
from rabbitmq_management_sdk.http_adapter.config import BasicAuthentication

if TYPE_CHECKING:
    from conftest import RabbitSettings


@pytest.mark.live
def test_rabbitmq_overview(rc: RabbitSettings) -> None:

    ba: BasicAuthentication = BasicAuthentication(username=rc.username, password=rc.password)
    ha: HttpAdapter = factory.create_adapter(
        f"{rc.scheme}://{rc.host}:{rc.port}", default_headers={"Authorization": ba.auth_header}
    )
    hr: HttpResponse = ha.request(method="GET", path="/api/overview")

    assert hr.status_code == 200
