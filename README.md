# rabbitmq-admin-sdk

A modern, typed Python SDK for the RabbitMQ HTTP Management API.

## Development setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency and environment management.

```bash
uv sync --group dev
```

That command creates/syncs the local environment with all development dependencies needed for linting, typing, and
tests (for example: `ruff`, `pytest`, `mypy`, and `python-dotenv`).