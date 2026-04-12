from pydantic import BaseModel, ConfigDict


class RabbitMQBase(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)
