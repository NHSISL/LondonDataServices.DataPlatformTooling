# LondonDataServices.DataPlatformTooling

This is a Python package shared by the platform repository and its dataset repositories. It centralizes dbt and adapter versions, setup commands, and pre-commit hooks so those tools and policies do not drift between repositories. It is not a dbt package and is not installed with `dbt deps`.

## Package contents

- `pyproject.toml` defines the package metadata, Python requirement, runtime dependencies, optional development dependencies, and console scripts.
- `PYTHON_VERSION` is the major/minor Python version used for the platform's shared virtual environment.
- `src/dbt_platform_tooling/setup_tools.py` implements the `install-infisical` and `attach-precommit-hooks` commands.
- `src/dbt_platform_tooling/pre-commit-config.yaml` contains the shared hook definitions. It is included as package data so the installed commands can find it.

The package currently requires Python 3.13 or newer. Its runtime dependencies pin `dbt-core`, `dbt-duckdb`, and `dbt-snowflake` to the compatible `1.11.*` release series, alongside their shared CLI and pre-commit tooling. Optional `dev` dependencies provide SQLFluff and dbt coverage tools.

## Bootstrap the platform

From the platform repository root, run the root setup script in Windows PowerShell:

```powershell
.\setup.ps1
```

The script initializes the Git submodules, reads the Python version from this package's `PYTHON_VERSION`, and creates or reuses the shared `.venv`. If the venv's Python major/minor version differs from the pin, it recreates the venv. It then installs this package in editable mode and runs both console commands.

You can provide a different venv directory. Relative paths are resolved from the platform repository root:

```powershell
.\setup.ps1 -VenvDir C:\venvs\london-data-platform
```

The required Python version must be available through the Windows `py` launcher (currently `py -3.13`). Setup also requires Git. `install-infisical` uses Winget on Windows and the Infisical Debian installation script on Linux. It is safe to rerun; an existing CLI is left in place, and installation failures are reported as warnings rather than aborting setup.

## Console commands

The scripts are registered in `pyproject.toml` and become available after installing the package:

- `install-infisical` installs the Infisical CLI when it is missing. Windows uses the verified Winget package ID `infisical.infisical`; Linux uses the upstream Debian setup script. Unsupported operating systems and install failures produce warnings.
- `attach-precommit-hooks [--root PATH]` configures the platform root and each direct Git submodule. `--root` defaults to the current working directory, so run it from the platform repository root unless supplying a path.

The hook installer merges the shared hook definitions with each repository's existing `.pre-commit-config.yaml`. It writes the effective config to `platform-pre-commit-config.yaml` inside that repository's Git metadata directory, leaving the tracked config unchanged, then installs the pre-commit and commit-message hooks. Repeated runs update the generated config and reinstall the hooks safely. Repositories without their own config still receive the shared hooks.

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
