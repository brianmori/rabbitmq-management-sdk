import base64
import random
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TimeoutConfig:
    connect: float = 5.0
    read: float = 30.0
    write: float = 30.0
    pool: float = 5.0


@dataclass(frozen=True)
class BasicAuthentication:
    username: str
    password: str

    @property
    def auth_header(self) -> str:
        credential = f"{self.username}:{self.password}"
        token = base64.b64encode(credential.encode("ascii")).decode("ascii")
        return f"Basic {token}"


class BackoffStrategy(Protocol):
    def wait_time(self, attempt: int) -> float: ...


@dataclass(frozen=True)
class NoBackoff:
    def wait_time(self, _attempt: int) -> float:
        return 0.0


@dataclass(frozen=True)
class ConstantBackoff:
    wait: float = 1.0

    def wait_time(self, _attempt: int) -> float:
        return self.wait


@dataclass(frozen=True)
class ExponentialBackoff:
    factor: float = 0.5
    max_wait: float = 60.0

    def wait_time(self, attempt: int) -> float:
        return float(min(self.factor * pow(2, attempt), self.max_wait))


@dataclass(frozen=True)
class ExponentialBackoffWithJitter:
    factor: float = 0.5
    max_wait: float = 60.0

    def wait_time(self, attempt: int) -> float:
        cap = min(self.factor * (2**attempt), self.max_wait)
        return random.uniform(0, cap)
