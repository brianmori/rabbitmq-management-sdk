# Capture and evidence contract

An offline audit answers questions about the configuration you supplied. For
example, a missing binding can mean a missing route, but it can also mean that
only the first page of bindings was saved. Schema validation cannot distinguish
those situations. Capture scope and provenance are part of interpreting results.

This guide covers the existing capture workflow and contributor requirements.
It does not introduce a capture-manifest API, completeness detector, or runtime
telemetry model. See the [construction guide](topology-graph-construction.md)
for translation rules and [audit guide](topology-auditing.md#audit-from-files)
for executable loading examples.

## Collect compatible inputs

| Input | Required shape and scope | What it establishes |
| --- | --- | --- |
| Definitions | Cluster-wide `ClusterDefinitionsResponse` / `admin.export_definitions()` | Declarations, routing configuration, policies and runtime parameters, ordinary and topic permissions, vhost count caps, available version and cluster metadata |
| Queue observations | Complete queue array, with every page collected | Observed types and exclusivity, regular/operator policy selection, effective policy definition, and available quorum members, leader, and online members |
| Exchange observations | Complete exchange array from the same broker/window | Selected policies when reported and referenced exchanges omitted from definitions |
| Local host mapping | Caller-supplied `in_cluster_amqp_hosts` | Which hosted shovel endpoint candidates belong to this cluster |
| Failure-domain mapping | Optional `failure_domains` on the resilience or aggregate audit | Caller-defined broker assignments for failure scenarios |

Supply queue and exchange observations together or omit both. The public facade
requires the pair; policy-dependent routes may require it even when quorum
membership is irrelevant. A single-vhost definitions export has a different
shape and cannot replace the cluster-wide input. Do not widen a failed scoped
request into a cluster-wide request as an implicit workaround.

Use the managers' complete `list_all` conveniences where available, or collect
and flatten every page yourself before file loading. An empty successful array
is different from a failed request, a page object, or a truncated JSON document.
Do not recover capture failures by substituting `{}` or `[]`.

Capture all inputs in one broker/window, preferably while configuration is
quiescent. Sequential HTTP calls are not an atomic snapshot. Account permissions,
filters, pagination, and concurrent changes can limit what was observed. The
SDK validates shapes and selected consistency rules, not those capture claims.
Disabling statistics does not remove the need for complete resource lists.

Filtered observations still need the fields required by their response schemas.
Check [QueueResponse](../src/rabbitmq_management_sdk/resources/v4/queues/schemas/queue_response.py)
and [ExchangeResponse](../src/rabbitmq_management_sdk/resources/v4/exchanges/schemas/exchange_response.py)
before selecting columns. A field present in raw JSON is not necessarily retained
by the schema or graph. For quorum resilience, include `members`, `leader`,
and `online` to retain the available placement evidence. See the
[quorum resilience guide](quorum-resilience.md#capture-and-interpret-the-evidence).

Include `exclusive` to assess non-durable classic queue declarations against
RabbitMQ 4.3 defaults. The observation must match a declared queue's identity,
type and durability. The definitions export omits exclusive queues, so even
paired observations do not make the declared-queue check an inventory of
temporary queues. [Declaration compatibility](declaration-compatibility.md).

## Preserve the meaning of missing evidence

| Captured state | Interpretation |
| --- | --- |
| A definitions section is omitted | Some schema collections default to empty; successful parsing does not prove that section was captured or empty on the broker. |
| A section is explicitly `[]` | The capture contains an empty list; completeness still depends on acquisition scope. |
| `permissions` or `topic_permissions` is omitted | That declaration section is unavailable. An explicit empty list instead supplies a section with no entries. This says nothing about effective authorization. |
| A matching vhost limit runtime parameter has `value: []` | Both caps are explicitly empty in that parameter. A missing `parameters` section does not establish this. A supplied embedded vhost limit object can provide fallback evidence. |
| A recognized argument-map key is omitted | No value for that key was captured. Policy/default resolution may still be needed. |
| An exchange's entire argument map is omitted | A matching observation can supply the map. If both maps are unavailable, the capture cannot rule out a direct alternate. An explicit `arguments: {}` captures a map with no direct alternate. |
| An argument-map key is explicitly `null` | Preserve the null. Quorum configuration records it as uncertain evidence rather than silently substituting a default. |
| Observed `policy` or `operator_policy` is explicitly `null` | An explicit null records no selection for that policy kind. An omitted selector can leave quorum, policy inventory, dead-letter, and alternate-exchange evidence uncertain. |
| The `policies` section is omitted | The capture can leave a policy-derived DLX or alternate exchange unknown when direct settings and observations do not settle the route. An explicit `policies: []` records an empty list. |
| `effective_policy_definition` is `{}` versus `null` | An empty definition is a captured map; null does not establish an empty effective definition. Conflicts with other evidence remain uncertain. |
| `members` is missing or empty | Quorum replication factor is unknown (`None`), not zero or the configured initial group size. |
| `online` is omitted or null versus `[]` | Omission or null leaves online evidence unknown. An empty list reports zero online members. |
| `leader` is omitted, null, or empty | The reported leader identity is unknown. |
| `exclusive` is omitted or null | Exclusivity is unknown. An explicit `false` establishes a non-exclusive queue when the observation agrees with the declaration. |
| Broker version is omitted or unsupported by the resolver | Quorum effective configuration remains unknown; a client version override does not fill capture metadata. |
| Queue argument map or policy sections are omitted | The limits audit leaves affected settings unknown unless other captured evidence resolves them. |
| A current stream policy reports `stream-max-segment-size-bytes` | The stream retention audit preserves the policy value as evidence. The applied setting requires creation-time or runtime configuration evidence. |
| Dynamic shovel `ack-mode` is omitted versus explicitly null | Omission can use the documented default for a captured 4.2 or 4.3 version and supported protocols. Explicit null is invalid evidence. |

These are field-specific meanings, not a universal null convention. Inspect the
consumer of each field. The quorum resolver checks Pydantic field presence;
other response fields and collections can have defaults that hide omission if
you look only at their values.

Keep original wire keys and explicit nulls in response argument maps. When
serializing validated responses, use wire names and preserve omitted fields
where relevant (`by_alias=True`, `exclude_unset=True`); do not use
`exclude_none=True` for evidence retention. Such a dump is still not a lossless
archive of raw JSON: unknown model fields may already have been ignored. See
[resource-model serialization](resource-models.md#constructing-and-serializing-argument-maps).

`ConfigurationValue` distinguishes `known`, `unset`, `unknown`, and
`not_applicable`. A finding without a value is not automatically disabled or
compliant. The [quorum audit guide](quorum-queue-auditing.md#values-and-evidence)
defines the current result contract and setting-specific policy resolution.
See [queue limits and retention](queue-limits.md#read-capture-evidence) for
the field presence and effective policy evidence used by the limits audit.
See the [vhost permissions and limits inventory](vhost-permissions.md) for
cluster definitions evidence for vhost-level queue and connection caps.
See [reverse policy lookup](policy-inventory.md) for resource-level selection
evidence and the limits of standard exchange observations.

## Record provenance alongside fixtures

For a new broker-derived fixture bundle, document:

- exact broker version and image digest where available, enabled plugins and
  relevant broker configuration;
- capture date/window, endpoint/query parameters, pagination completion, and
  permissions or scope restrictions;
- whether the data is a full cluster capture, a sanitized capture, or a
  deliberately scoped projection for one test;
- which fields were removed or renamed, and how identities remain consistent
  across definitions, observations, endpoints, and expected results;
- the reproducible scenario, expected findings, test files, and deliberate
  regeneration command;
- what the fixture establishes: declaration acceptance/read-back, configured
  routing, or separately observed runtime behavior.

Use a bundle README and, when useful, a provenance sidecar with artifact hashes.
The current auditor does not load or enforce those sidecars. A projection of an
isolated test vhost must not be described as evidence of full-cluster coverage.
One-node captures cannot demonstrate tolerance of failures across broker nodes.

## Sanitize without changing the scenario

Definitions can contain password hashes, users and grants, internal identifiers,
and credentials in shovel/federation URIs. Keep original operational captures
outside version control and sanitize a separate copy:

1. Remove credential material and unrelated sensitive sections. User/grant data
   needed for a test should be synthetic and described as such.
2. Rebuild credential-free URI values; inspect query parameters as well as
   userinfo for secrets. Do not print raw URIs or failure messages during review.
3. Replace sensitive resource, cluster, member, and host identifiers consistently
   across the bundle. Preserve distinctions that affect routing or locality.
4. Preserve relevant argument keys, nulls, policy selections, failover lists,
   and protocol semantics. Sanitization must not manufacture a healthier graph.
5. Review the resulting files and replay the expected results against them.

Historical fixture exceptions must stay explicit. The
[4.3 complex-topology demo](../tests/integration/rmq-4.3/complex-topology/README.md)
is documented as an unmodified, importable export from a disposable broker and
retains that broker's disposable account hash. It is not a sanitized production
export and is not a precedent for retaining real credentials in new fixtures.
Do not rewrite it incidentally when editing documentation.

The [quorum configuration bundle](../tests/integration/rmq-4.3/quorum-configuration/README.md)
uses scoped projections and sanitized identities. It proves configuration
acceptance and read-back, not retry timing or disconnected-consumer behavior.
Keep each bundle's stated scope and exceptions when reusing its evidence.

## Regenerate deliberately and verify the boundary

Ordinary tests should replay checked-in evidence, not regenerate it. Follow the
bundle's explicit recapture procedure against a disposable broker, review the
new version and sanitization, and update expected results only when the broker
evidence justifies them. Do not overwrite captures as a side effect of an audit.

Use public `ClusterAuditor` entry points for capture integration tests and the
split parser/model suites for focused translation invariants. Check missing
values, invalid containers, and contradictory observations as well as successful
findings. File/JSON/schema failures through the facade must remain SDK exceptions
with causes preserved; see [loading errors](topology-auditing.md#diagnose-loading-and-analysis-errors).

The stored graph cannot recreate omitted definitions, credentials, statistics,
or provenance. `from_topology()` preserves retained graph evidence but does not
establish its origin or completeness. Pickles are for trusted compatible-version
process transfer, not untrusted input or a durable capture format.
