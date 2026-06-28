# Contributing to rabbitmq-management-sdk

Thank you for your interest in contributing! We welcome issues, bug fixes, documentation
improvements, and feature requests.

## How to Contribute

### 1. Report Bugs or Suggest Features
* Check the existing [GitHub Issues](https://github.com/brianmori/rabbitmq-management-sdk/issues) to
  see if your topic is already being discussed.
* If not, open a new Issue with a clear title and description (and, for bugs, the broker version and
  a minimal reproduction).

### 2. Local Development Setup
We use [`uv`](https://docs.astral.sh/uv/) for environment management and
[`just`](https://github.com/casey/just) as a task runner.

1. Fork and clone the repository:
   ```bash
   git clone https://github.com/<your-username>/rabbitmq-management-sdk.git
   cd rabbitmq-management-sdk
   ```

2. Install dependencies (including the dev toolchain):
   ```bash
   uv sync --group dev
   ```

### 3. Code Quality Standards
Before submitting your changes, make sure they pass linting, formatting, and type checks:

```bash
just check    # ruff lint + ruff format --check + mypy
just fix      # auto-fix lint issues and reformat
```

Run the test suite (no broker required for unit + integration):

```bash
just test
```

If your change touches live behaviour, run the live tests against a disposable broker:

```bash
just test-live-rmq43   # spins up RabbitMQ 4.3 in Docker, runs live tests, tears it down
```

### 4. Submit a Pull Request
1. Create a descriptive branch name (`git checkout -b feature/cool-new-thing`).
2. Make your changes with clear, focused commits.
3. Push to your fork (`git push origin feature/cool-new-thing`).
4. Open a Pull Request against the `main` branch.
5. Ensure your PR description clearly states what problem is being solved.

> **PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/)**
> (e.g. `feat(queues): add stream queue support`, `fix(admin): handle empty vhost limits`). This is
> enforced by CI and keeps the changelog meaningful.

## Code of Conduct
This project is released with a [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to
abide by its terms.
