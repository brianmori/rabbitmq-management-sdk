# Contributing to rabbitmq-management-sdk

Start with [AGENTS.md](AGENTS.md) for implementation and review contracts and
[the documentation task map](docs/README.md#contributor-task-map) for the guide
relevant to your change. Documentation records intended behavior; inspect the
current source and tests when reviewing it.

## Report bugs or suggest features

Check the [existing issues](https://github.com/brianmori/rabbitmq-management-sdk/issues)
before opening one. Include the broker and Python versions, a minimal
reproduction, expected and observed behavior, and sanitized evidence. Keep SDK
failures separate from demonstrated broker behavior and unsupported scenarios.

## Local development setup

Use [uv](https://docs.astral.sh/uv/) for dependencies and
[just](https://just.systems/man/en/) for repository recipes. From a clone of the
repository, select an interpreter explicitly and install the locked dev group:

```bash
uv python install 3.12
UV_PYTHON=3.12 uv sync --locked --group dev
```

Installing a Python version does not select it for later `uv` commands. Set
`UV_PYTHON` on each recipe as shown below, or export it for your shell session.
The CI matrix uses Python 3.12, 3.13, and 3.14; quality and live jobs use 3.12.
See the [workflow](.github/workflows/ci.yaml) and [recipes](justfile) for the
current commands rather than assuming documentation is a CI sign-off.

## Checks and supported versions

Every PR must be independently importable and pass lint, formatting, strict
mypy, and non-live tests:

```bash
UV_PYTHON=3.12 just check
UV_PYTHON=3.12 just test
```

`just check` checks formatting without changing files. `just fix` applies Ruff
fixes and formatting; run it deliberately and inspect its changes. Use
`just audit` for the dependency vulnerability scan and `just build` to build
artifacts when relevant to the change.

| Marker | Location | Scope |
| --- | --- | --- |
| `unit` | `tests/unit/` | Isolated values, algorithms, and facade tests; some use temporary files, no broker |
| `integration` | `tests/integration/` | Mocked managers/adapters and captured broker evidence |
| `live` | `tests/live/` | Round trips against disposable brokers |

Changes to broker-dependent behavior should run against both supported live
targets, with version-specific cases explicitly gated:

```bash
UV_PYTHON=3.12 just test-live          # RabbitMQ 4.2 and 4.3, sequentially
# Or run each target separately:
UV_PYTHON=3.12 just test-live-rmq42
UV_PYTHON=3.12 just test-live-rmq43
```

The live recipes supply disposable credentials and clean up their containers,
networks, and test volumes, including after failures. Do not point them at
production or reuse their test credentials. Direct live pytest runs load `.env`;
use [.env.example](.env.example) as the template. A v4 manager branch does not
establish testing of RabbitMQ 4.0/4.1, every plugin, or enterprise variants.

Report commands actually run, the versions exercised, and any failures or
unverified behavior. Declaration/read-back tests are not evidence of message
retry timing or failover. Follow the [capture contract](docs/capture-contract.md)
and each bundle's README when adding or regenerating broker evidence.

## Implementation checklist

For a new resource domain:

1. Add `src/rabbitmq_management_sdk/resources/v4/<name>/` with `__init__.py`,
   `schemas/<name>_request.py`, `schemas/<name>_response.py`, optional shared
   schemas, and `services.py`.
2. Use the appropriate request/response base and cluster/vhost manager
   constructor. Preserve extensible response maps and SDK exception boundaries.
3. Add a version-gated `RabbitMQClient` property and mocked integration tests;
   add live tests where the broker establishes behavior that mocks cannot.

For topology changes:

1. Update model invariants, then parser/helper construction without adding I/O.
2. Add focused model/parser tests and relevant captured integration assertions.
   Keep public value invariants in `tests/unit/test_models.py`, use shared
   builders in `tests/shared/parser_fixtures.py`, and test the public parser
   entry point rather than private builder phases.
3. Update ordering for identity-bearing changes and cycle classification when
   a new route can start a separate publication operation.
4. Add individual `ClusterAuditor` methods and aggregate report fields where
   applicable. Preserve supported imports, frozen values, canonical ordering,
   `from_topology()` behavior, and trusted same-version process transfer.
5. Update construction inputs, the [audit capability map](docs/topology-auditing.md#implemented-capabilities-and-evidence),
   feature guide, and documentation index. Use the map for test ownership.

For a new API major, add the version enum if absent (`V5` is already reserved),
create the corresponding resource package, and add explicit client routing.
An enum entry or version override alone does not implement compatibility.

Split large work into independently passing changes: models/construction first,
then ordering/algorithms, then dependent aggregation and integrations. Do not
export a module before it exists. Start each non-stacked PR from the updated
target branch after its prerequisites merge.

## Designing a new audit

For a substantial feature, write a focused design note with status **Proposed**
before implementation. An issue or a note under `docs/design/` can hold it;
there is no special loader or required proposal filename. Link the note from
its task or PR so the scope is discoverable. Record:

- the operational question, a concrete example, and explicit non-goals;
- required inputs, capture scope, identity, and evidence retained or discarded;
- public result values, uncertainty states, provenance, and caller policy;
- behavior through `from_topology()`, file loading, and process transfer;
- version-specific broker evidence and setting-specific precedence;
- compatibility implications, bounded-work expectations, and acceptance tests.

Do not document proposed classes/methods as supported APIs. Once implemented,
update the status and move the maintained contract/examples into the feature
and construction guides. Avoid duplicating the same rule across a design note,
AGENTS.md, and several user guides. Federation remains a future separate
composition; the existing single-cluster graph must not be relaxed for it.

## Documentation changes

Assume Python experience and explain RabbitMQ and graph terms before using them.
Start with a concrete route and result. Use Mermaid when arrows or algorithm
state clarify the explanation; distinguish configured possibilities from runtime
flow. Docstrings follow the Google Python Style Guide.

Give runnable examples complete imports and prerequisites. Label partial
snippets and identify where existing variables come from. Prefer supported
facade imports. Verify changed examples against current schemas/captures,
relative links and anchors, referenced symbols/test paths, and any changed
Mermaid syntax. Link broker-specific statements to official versioned evidence
and state the versions actually reproduced.

The repository has no dedicated documentation checker recipe. Do not claim
link, Mermaid, or snippet verification runs in CI merely because code checks
pass. For documentation-only work, describe the concrete documentation checks
performed and avoid implying broker behavior was retested.

## Release verification

Keep `project.license-files` in `pyproject.toml`. After `uv build`, inspect both
the wheel and source archive for the expected `LICENSE` contents and license-file
metadata. The current CI package job runs `uv build` only; it does not verify
archive contents, and no distribution-checker script is present.

Install built wheels and source distributions in isolated environments and
verify supported imports and representative captures without the editable
checkout. Pytest adds `src` to its path by default; disable that override with
`-o pythonpath=` when testing an installed artifact. Test supported Python
versions with explicit interpreter selection.

A downstream application's exact SDK pin must include the API it uses; updating
this repository's lockfile does not upgrade consumer environments. Inspect the
working tree before a release: a build using local-only required files does not
establish fresh-checkout reproducibility.

## Submit a pull request

Use a descriptive branch and focused commits. Open the PR against `main`, explain
the problem and resulting behavior, and include relevant verification. PR titles
must follow [Conventional Commits](https://www.conventionalcommits.org/), such as
`feat(queues): add stream queue support` or `fix(admin): handle empty vhost limits`;
this is enforced by CI.

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
