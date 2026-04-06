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
test-live:
    uv run pytest -m live

# Mypy check
typecheck:
    uv run mypy .

# Build the library for distribution
build:
    uv build

# Clean up caches
clean:
    rm -rf .pytest_cache .ruff_cache dist/

