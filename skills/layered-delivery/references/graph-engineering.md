# Graph Engineering

## Authority

- Human authority: the reviewed hierarchy and root `development-plan.md`.
- Compiled authority: the deterministic Delivery Graph stored in SQLite.
- Runtime fact authority: graph-fingerprint-bound, hash-chained graph events. Graph and node runs are replayable query snapshots.
- Human projections: `execution-graph.md`, `frontier.md`, and `run-timeline.md`; never infer machine state from them.

The user does not define arbitrary graph nodes or edges. `prepare-hierarchy` compiles the validated hierarchy into one graph with two typed views:

- Execution Graph: Task execution, Task/Capability/Delivery gates, dependencies, success edges, and joins.
- Governance Graph: gates, root review, user confirmation, and governance transitions.

## Frozen graph contract

Preparation returns both `hierarchyFingerprint` and `graphFingerprint`. One `freeze-hierarchy` confirmation freezes the complete hierarchy, development mode, and compiled graph.

After freeze:

- do not add or remove graph nodes;
- do not rewrite dependency or join edges;
- do not skip gate, review, or confirmation nodes;
- runtime may choose Agent count, parallelism, and owner;
- retry and remediation create attempts, not new graph definitions.

## Frontier

Use:

```text
python -X utf8 <skill-root>/scripts/hdg.py graph-frontier --item <root-or-subtree-id> --json
```

Possible actions:

| Action | Required response |
|---|---|
| `DISPATCH_TASK` | Dispatch that Task with a unique owner and operationId |
| `RUN_GATE` | Build and submit evidence for that work item gate |
| `REQUEST_REVIEW` | Perform isolated independent review or obtain accepted human review |
| `REQUEST_USER_CONFIRMATION` | Present the final result and obtain distinct user confirmation |

`ready-tasks` returns only the `workItemId` values of current `DISPATCH_TASK` actions. Do not implement separate readiness logic in the host.

`blocked` explains why nodes are not actionable, including predecessor nodes, scope conflicts, isolation, or an unfrozen requirement. Resolve the recorded condition and query the frontier again; do not route around it.

The frontier also returns `criticalPath`, including the longest remaining path, the next join, and whether the path is blocked. The controller renders the same information, current actions, parallel groups, and blockers into the bilingual `frontier.md` dashboard.

## Status and events

Use:

```text
python -X utf8 <skill-root>/scripts/hdg.py graph-status --item <root-or-subtree-id> --json
python -X utf8 <skill-root>/scripts/hdg.py graph-events --item <root-or-subtree-id> --json
python -X utf8 <skill-root>/scripts/hdg.py graph-replay --item <root-or-subtree-id> --json
```

`graph-status` returns the graph fingerprint, graph run, typed nodes, edges, current node status, attempt, owner, operationId, and blockers.

`graph-events` returns the ordered graph-fingerprint-bound, hash-chained event stream. Normal lifecycle events include graph start, Task claim/result, gate result, review, and final confirmation. Retry and remediation add their own events.

For artifact-driven events, the controller stores a bound evidence wrapper containing the original artifact and a binding over `runId`, `nodeId`, `attempt`, `graphFingerprint`, and the artifact hash. The binding has its own canonical SHA-256. Hosts submit only the original artifact; never manufacture binding fields or reuse a bound artifact at another graph coordinate.

`graph-replay` applies the complete event stream from `GRAPH_RUN_STARTED`, reconstructs every node attempt and graph status, computes a replay fingerprint, and reports any mismatch with the graph/node run snapshots. A mismatch blocks normal status and frontier queries.

If the event and evidence chains validate and only query snapshots are damaged, an explicitly confirmed recovery may run:

```text
python -X utf8 <skill-root>/scripts/hdg.py rebuild-graph-run --item <root-id> --confirmed --json
```

This rebuilds graph/node run snapshots from events and records the recovery interaction. It never edits the frozen graph, events, or evidence.

Never modify graph tables, registry rows, attempts, or events directly.

## Retry

`retry-item` is legal only for an unclaimed BLOCKED work item with the current baseline fingerprint. It creates a new attempt for the failed execution or gate node and preserves the graph fingerprint.

Do not reuse the failed operationId for a new Task dispatch.

## Remediation invalidation

`remediate-task` applies only when the goal, requirements, acceptance, interfaces, data contract, test commands, topology, and external authority remain unchanged.

The controller starts at the Task execution node and follows outgoing graph edges. It invalidates progressed downstream nodes that depend on the repaired result, including consumers and aggregate gates. It creates new attempts for invalidated progressed nodes while keeping the original graph definition and baseline.

If an affected downstream Task has an active claim, remediation is blocked. Release or finish that claim before retrying the remediation command.

Completed requirements are immutable; later changes require a new requirement.

## Bilingual graph projections

All architecture and generated graph diagrams use `中文 / English` labels. The graph projection must distinguish:

- `执行图 / Execution Graph`
- `治理图 / Governance Graph`
- node kind and work item ID
- typed edges such as `成功 / Success`, `通过后 / Requires Pass`, and `全部汇聚 / All Of`

The generated files are read-only projections and can be rebuilt with `refresh-projections`.
