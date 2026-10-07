# Loaded controller provenance

Trusted evaluator controllers bind their source at module import. A bounded read
accepts only regular files up to 512 KiB and rejects leaf symlinks. The helper
compiles those trusted bytes with the running Python optimization level and
compares the resulting module code object with the module currently executing.
This rejects stale timestamp-based bytecode caches and source edits between
loading and binding. The helper also binds its own source.

Each evaluator uses a fixed set of relevant controller bindings. Unrelated lazy
imports do not change its identity. Before a runner, inspection stage, or worker
job starts, and before it returns evidence, its bound files must still match.
Changed, removed, unreadable, or replaced source causes `EvaluatorChanged`.
Workers persist a specific `evaluator_changed` failure with no artifact or
assessment; restart them after deploying changed controllers. User cancellation
still follows the cancellation path and does not publish evidence.

Artifacts and execution results expose `controller_binding`: a schema version,
relative source-name to SHA-256 map, Python implementation/version/cache tag, and
an aggregate SHA-256 over that identity. Independent and pytest evaluator hashes
also bind their fixed controller identity alongside harness and profile inputs.
Earlier retained historical hashes continue to describe the earlier executions;
they are not rewritten to match a later evaluator.

This is source binding for named controllers, not full host attestation. It does
not inventory every host dependency, authenticate deployment files, detect
runtime monkeypatches, or guarantee detection of a source change that is restored
between checks. Container harnesses retain their existing before/after source
checks. Deploy controller and harness files immutably, and restart long-running
API and worker processes together. Processes started before these guards were
installed must be restarted once to activate them.
