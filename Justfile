# List all available recipes
default:
    @just --list

# Lint and format check
check:
    uv run ruff check
    uv run ruff format --check
    uv run mypy

# Auto-fix everything
fix:
    uv run ruff check --fix --show-fixes
    uv run ruff format

# Run non-live tests
test:
    uv run pytest -m "unit or integration" --cov --cov-report=term-missing -v

# Run live RabbitMQ tests
test-live-rmq42:
    docker compose -f docker-compose.rabbitmq-42.yaml up -d
    uv run pytest -m live
    docker compose -f docker-compose.rabbitmq-42.yaml down --volumes

test-live-rmq43:
    docker compose -f docker-compose.rabbitmq-43.yaml up -d
    uv run pytest -m live
    docker compose -f docker-compose.rabbitmq-43.yaml down --volumes

# Mypy check
typecheck:
    uv run mypy .

# Build the library for distribution
build:
    uv build

# Clean up caches
clean:
    rm -rf .pytest_cache .ruff_cache dist/

