# List all available recipes
default:
    @just --list

# Lint and format check
check:
    uv run ruff check src/ tests/
    uv run ruff format --check src/ tests/
    uv run mypy src/ tests/

# Auto-fix everything
fix:
    uv run ruff check --fix --show-fixes src/ tests/
    uv run ruff format src/ tests/

# Run non-live tests
test:
    uv run pytest -m "unit or integration" --cov --cov-report=term-missing -v

# Run live tests against every supported RabbitMQ version
test-live:
    #!/usr/bin/env bash
    set -uo pipefail

    status=0
    for recipe in test-live-rmq42 test-live-rmq43; do
        just "$recipe" || status=$?
    done
    exit "$status"

# Run live tests against one RabbitMQ version
test-live-rmq42:
    just _test-live-rabbitmq docker-compose.rabbitmq-42.yaml

test-live-rmq43:
    just _test-live-rabbitmq docker-compose.rabbitmq-43.yaml

_test-live-rabbitmq compose_file:
    #!/usr/bin/env bash
    set -Eeuo pipefail

    compose=(docker compose -f "{{ compose_file }}")
    cleanup() {
        "${compose[@]}" down --volumes --remove-orphans
    }

    trap cleanup EXIT
    export RABBIT_HOST=localhost
    export RABBIT_PORT=15672
    export RABBIT_USER=sdk_test
    export RABBIT_PASS=sdk_test

    # Recover from a previously interrupted run and ensure a fresh data volume.
    cleanup
    "${compose[@]}" up -d --wait --wait-timeout 120
    uv run pytest -m live

# Mypy check
typecheck:
    uv run mypy src/ tests/

# Audit installed dependencies for known vulnerabilities
audit:
    uv run pip-audit

# Build the library for distribution
build:
    uv build

# Clean up caches
clean:
    rm -rf .pytest_cache .ruff_cache dist/
