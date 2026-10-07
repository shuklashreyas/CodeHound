# Python repository impact

Every queued verification now runs a bounded source inspection on both pinned revisions. The inspector uses Python's AST parser in the same restricted Docker environment as other execution. It never imports or executes repository modules.

The evidence includes:

- New syntax errors in changed or added Python files, distinguished from existing baseline syntax errors.
- A file-level import graph for each revision.
- Untouched downstream files with a shortest static import chain to a changed path.
- Coverage counts, unresolved imports, ambiguous module names, exclusions, and incomplete inspections.

A deleted or renamed dependency may still have importers in the baseline graph, so both graphs are retained. This is useful for choosing additional regression checks. It does not show that an affected file is broken or that an import runs in production.

## Resolution and limits

Imports are resolved against repository-relative module names and inferred `src/` roots. Relative imports and `from package import module` are supported. Duplicate module names are left unresolved. Conditional imports are included conservatively; dynamic imports, plugin loading, external packages, and other languages are not resolved.

Each revision is limited to 2,000 Python files, 16 MiB of Python source, 1 MiB per file, 50,000 AST nodes per file, 1,000 import statements per file, and 30 seconds. Symlinks are not followed. `.git`, virtual environments, `node_modules`, caches, `dist`, `build`, and `.tox` directories are excluded. Large output or budget exhaustion produces incomplete coverage rather than a clean result.

Up to 100 affected files and 200 incomplete-inspection entries are displayed per result. Import trails longer than 31 steps are shortened with the changed origin retained; their numeric distance remains exact. The trusted inspector/controller fingerprint and Docker image ID are recorded.

Syntax follows the configured image's Python version. A project requiring another interpreter may need a different trusted image. This is not Ruff, mypy, security scanning, or a guarantee of regression safety; those checks remain separate work.

For the standalone verification CLI, enable this with `--inspect-python`. Queued worker jobs enable it automatically. Evidence is persisted under `python_impact`, displayed on the dashboard, and included in execution exports.
