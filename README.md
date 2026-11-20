# LondonDataServices.DataPlatformTooling

This is a Python package shared by the platform repository and its dataset repositories. It centralizes dbt and adapter versions, setup commands, and pre-commit hooks so those tools and policies do not drift between repositories. It is not a dbt package and is not installed with `dbt deps`.

## Package contents

- `pyproject.toml` defines the package metadata, Python requirement, runtime dependencies, optional development dependencies, and console scripts.
- `PYTHON_VERSION` is the major/minor Python version used for the platform's shared virtual environment.
- `src/dbt_platform_tooling/setup_tools.py` implements the `install-infisical`, `update-infisical`, and `attach-precommit-hooks` commands.
- `src/dbt_platform_tooling/generate_source_tables.py` implements shared dbt source DDL and DuckDB initialisation.
- `src/dbt_platform_tooling/sync_sources.py` synchronizes source definitions with built dbt models.
- `src/dbt_platform_tooling/pre-commit-config.yaml` contains the shared hook definitions. It is included as package data so the installed commands can find it.

The package currently requires Python 3.13 or newer. Its runtime dependencies pin `dbt-core`, `dbt-duckdb`, and `dbt-snowflake` to the compatible `1.11.*` release series, alongside their shared CLI and pre-commit tooling. Optional `dev` dependencies provide SQLFluff and dbt coverage tools.

## Bootstrap the platform

From the platform repository root, run the root setup script in Windows PowerShell:

```powershell
.\setup.ps1
```

The script initializes the Git submodules, reads the Python version from this package's `PYTHON_VERSION`, and creates or reuses the shared `.venv`. If the venv's Python major/minor version differs from the pin, it recreates the venv. It then installs this package with its `dev` extras in editable mode and runs both console commands.

You can provide a different venv directory. Relative paths are resolved from the platform repository root:

```powershell
.\setup.ps1 -VenvDir C:\venvs\london-data-platform
```

The required Python version must be available through the Windows `py` launcher (currently `py -3.13`). Setup also requires Git. `install-infisical` uses Winget on Windows and the Infisical Debian installation script on Linux. It is safe to rerun; an existing CLI is left in place, and installation failures are reported as warnings rather than aborting setup.

The project setup scripts use `scripts/use-platform-venv.ps1` to validate and
reuse this environment rather than creating their own. If root setup used
`-VenvDir`, pass the same value to the project script; relative paths are
resolved from the platform root. For example:

```powershell
.\LondonDataServices.OLIDS_Share\setup.ps1 -VenvDir C:\venvs\london-data-platform
```

The VS Code workspace defaults to the root `.venv` on Windows. Select a custom
environment manually in VS Code. Existing project CI workflows and their
requirements files still install dependencies independently; shared CI
templates and selective submodule checkout are future work, not provided by
this bootstrap.

## Console commands

The scripts are registered in `pyproject.toml` and become available after installing the package:

- `install-infisical` installs the Infisical CLI when it is missing. Windows uses the verified Winget package ID `infisical.infisical`; Linux uses the upstream Debian setup script. Unsupported operating systems and install failures produce warnings.
- `update-infisical` updates an installed Infisical CLI with Winget on Windows or APT on Linux. Run `install-infisical` first if the CLI is not installed.
- `attach-precommit-hooks [--root PATH]` configures the platform root and each direct Git submodule. `--root` defaults to the current working directory, so run it from the platform repository root unless supplying a path.
- `generate-source-tables --source NAME --yaml PATH` generates source table DDL or executes it against a local DuckDB file.
- `sync-sources --yaml PATH --manifest PATH --catalog PATH --package NAME --database NAME --source NAME=SCHEMA` previews source metadata changes from built dbt models. Repeat `--source` for each independent schema; add `--write` to apply them.

The hook installer merges the shared hook definitions with each repository's existing `.pre-commit-config.yaml`. It writes the effective config to `platform-pre-commit-config.yaml` inside that repository's Git metadata directory, leaving the tracked config unchanged, then installs the pre-commit and commit-message hooks. Repeated runs update the generated config and reinstall the hooks safely. Repositories without their own config still receive the shared hooks.

## DuckDB source tables

Use the shared environment initialised by the platform setup script. From a dbt
project directory, generate empty source tables from its source YAML:

```powershell
generate-source-tables --source olids_pseudo --yaml .\models\src_olids.yml --schema OLIDS_PSEUDO --duckdb-file .\datamarts.duckdb
```

For scripts that select an interpreter explicitly, the equivalent is:

```powershell
& $venvPython -m dbt_platform_tooling.generate_source_tables --source olids_pseudo --yaml .\models\src_olids.yml --schema OLIDS_PSEUDO --duckdb-file .\datamarts.duckdb
```

Both `--source` and `--yaml` are required. Paths are relative to the caller's
working directory. `--database` and `--schema` override the source qualifiers;
`--table` preserves the Enrichment workflow's physical table name override.
Jinja is not evaluated: provide overrides for templated qualifiers or identifiers.
DuckDB execution ignores the source database qualifier and requires a concrete
schema. Use the same database file as the dbt target.

Columns come from each table's `columns` list and `data_type` values (defaulting
to `VARCHAR` when omitted). Snowflake numeric, timestamp and semi-structured
types are mapped for DuckDB. Tables without columns are skipped with a diagnostic;
their schemas are still created. DuckDB execution uses **CREATE OR REPLACE TABLE**,
so existing source table data is discarded. Use only a local fixture database.

Without `--duckdb-file`, SQL is printed rather than executed, with
`CREATE TABLE IF NOT EXISTS` and the original source types. `--output PATH`
writes the SQL to a file and can also accompany DuckDB execution.

DataMarts calls the shared module directly. The EMIS, TPP and Enrichment helper
paths remain as forwarding entry points for existing setup, documentation and
unit scripts, retaining their previous default YAML path. Those interpreters
must have this tooling package installed; source generation logic lives only here.
After updating the package, refresh its installation to register the new console
command (the platform setup does this automatically):

```powershell
.\.venv\Scripts\python.exe -m pip install --no-deps -e .\LondonDataServices.DataPlatformTooling
```

Run the generator tests from the tooling repository:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Sync source definitions

Build the relevant Enrichment Share models and their dependencies in the target database first. Then run `dbt docs generate` for that same project and target; it queries the database to create `target/manifest.json` and `target/catalog.json`. The catalog reflects the built relations, so rebuild after model changes. Catalog generation requires permission to inspect every model being synchronized.

For example, from the platform root, after building the Share models in the Enrichment DuckDB `dev` target:

```powershell
$project = '.\LondonDataServices.Transform.OLIDS_Enrichment\OLIDS_Enrichment'
$sourceYaml = '.\LondonDataServices.DataMarts\DATAMARTS\models\src_olids.yml'
.\.venv\Scripts\dbt.exe docs generate --project-dir $project --profiles-dir "$project\scripts" --target dev
.\.venv\Scripts\python.exe -m dbt_platform_tooling.sync_sources --yaml $sourceYaml --manifest "$project\target\manifest.json" --catalog "$project\target\catalog.json" --package OLIDS_Enrichment --database olids_duckdb_dev --source olids_pid=OLIDS_PID --source olids_pseudo=OLIDS_PSEUDO
```

Review the additions and removals, then repeat the sync command with `--write`. Use `--check` instead to exit nonzero when sources differ, without writing; this can be used as a CI drift check. The installed `sync-sources` console command is equivalent to the Python module. Supply the actual built database and schema names for other targets; these need not match the database name in the consumer's YAML. The command synchronizes only the explicitly mapped sources and treats each schema separately. It adds and removes model tables and columns, updates each `data_type` from the catalog, and fills missing column descriptions from the model documentation or database comment. Existing descriptions, tests, and other metadata for matched tables and columns are preserved. Other sources are untouched. Table or column metadata for removed items will be lost on write.

The command refuses to write if the catalog reports errors, is from a different dbt invocation than the manifest, omits a model, its columns or column types, or contains no models for a requested schema. An incomplete or stale build is not authoritative: rebuild all intended models before generating the artifacts. Catalog types describe the database and adapter used to generate them: the DuckDB example above produces DuckDB types, not verified Snowflake types. Use a catalog generated from the intended Snowflake deployment to capture its actual types. Do not use the generated catalog as evidence of which relations are exposed by a different deployment or share.

Source `data_type` and descriptions are metadata; dbt does not automatically type-check SQL references against them. The drift check compares definitions with the supplied artifacts, not query correctness. Validate model SQL against the deployment warehouse or a representative test database as well.

## Shared pre-commit hooks

The shared config combines two groups of checks:

- Infisical scans staged changes with `infisical scan git-changes --staged`. Findings block the commit; `--redact` prevents detected values from being printed. The Infisical CLI must be installed and available on `PATH` when committing.
- Hooks from `pre-commit-hooks` are pinned at `v6.0.0`. They check illegal Windows filenames, JSON/TOML/YAML syntax, merge-conflict markers, and private keys; fix end-of-file newlines and trailing whitespace; and block commits directly to `main` or `master`.

Every generated Infisical hook explicitly passes the packaged `src/dbt_platform_tooling/.infisical.toml` via `--config`. This extends Infisical's default rules with the shared high-entropy quoted-string rule. Rerun `attach-precommit-hooks` after updating the shared hook definitions or moving the tooling installation.

Manual scans must also select this config; a bare `infisical scan -v` does not automatically use the packaged file. For example, from the platform root, scan a dataset repository with the same rules:

```powershell
$scanConfig = (Resolve-Path '.\LondonDataServices.DataPlatformTooling\src\dbt_platform_tooling\.infisical.toml').Path
infisical scan --source '.\LondonDataServices.Transform.EMIS_OLIDS' --config $scanConfig --verbose --redact
```

The local branch hook is a developer safeguard. Use repository-host branch protection as the authoritative policy, since local Git hooks can be bypassed.

After setup, pre-commit runs automatically on commit. To run all configured checks manually from the platform root:

```powershell
$config = Join-Path (git rev-parse --absolute-git-dir) 'platform-pre-commit-config.yaml'
pre-commit run --all-files --config $config
```

## Updating shared versions

Update the central pins in `pyproject.toml`, rather than independently changing adapter versions in each dataset repository. Check dbt and adapter compatibility together when changing their release series. When changing the platform Python version, update both `PYTHON_VERSION` and `requires-python` in `pyproject.toml`; contributors also need that interpreter available locally. Update the shared pre-commit hook revision in `src/dbt_platform_tooling/pre-commit-config.yaml` when adopting a newer hook release.
