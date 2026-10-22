from copy import deepcopy
import platform
import shutil
import subprocess
from pathlib import Path

import typer
import yaml


install_infisical_app = typer.Typer()
attach_precommit_hooks_app = typer.Typer()


def _merge_precommit_config(repository_path: Path, shared_config_path: Path) -> Path:
    repository_config_path = repository_path / ".pre-commit-config.yaml"
    if repository_config_path.is_file():
        repository_config = yaml.safe_load(repository_config_path.read_text(encoding="utf-8")) or {}
    else:
        repository_config = {}

    if not isinstance(repository_config, dict):
        raise ValueError(f"Invalid pre-commit config in {repository_path}")

    repository_hooks = repository_config.setdefault("repos", [])
    if not isinstance(repository_hooks, list):
        raise ValueError(f"Invalid repos section in {repository_config_path}")

    shared_config = yaml.safe_load(shared_config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(shared_config, dict) or not isinstance(shared_config.get("repos"), list):
        raise ValueError(f"Invalid shared pre-commit config: {shared_config_path}")

    scan_config_path = shared_config_path.with_name(".infisical.toml").resolve(strict=True)
    for shared_repo in shared_config["repos"]:
        if shared_repo.get("repo") == "local":
            for shared_hook in shared_repo.get("hooks", []):
                if shared_hook.get("id") == "infisical-scan":
                    shared_hook["args"] = [
                        *shared_hook.get("args", []),
                        "--config",
                        str(scan_config_path),
                    ]
        matching_repo = next(
            (
                repo
                for repo in repository_hooks
                if isinstance(repo, dict)
                and repo.get("repo") == shared_repo.get("repo")
                and repo.get("rev") == shared_repo.get("rev")
            ),
            None,
        )
        if matching_repo is None:
            repository_hooks.append(deepcopy(shared_repo))
            continue

        hooks = matching_repo.setdefault("hooks", [])
        if not isinstance(hooks, list):
            raise ValueError(f"Invalid hooks section in {repository_config_path}")

        for shared_hook in shared_repo.get("hooks", []):
            matching_hook_index = next(
                (
                    index
                    for index, hook in enumerate(hooks)
                    if isinstance(hook, dict) and hook.get("id") == shared_hook.get("id")
                ),
                None,
            )
            if matching_hook_index is None:
                hooks.append(deepcopy(shared_hook))
            else:
                hooks[matching_hook_index] = deepcopy(shared_hook)

    git_directory = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=repository_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    effective_config_path = Path(git_directory) / "platform-pre-commit-config.yaml"
    serialized_config = yaml.safe_dump(repository_config, sort_keys=False)
    if not effective_config_path.is_file() or effective_config_path.read_text(encoding="utf-8") != serialized_config:
        effective_config_path.write_text(serialized_config, encoding="utf-8")
    return effective_config_path


@install_infisical_app.command()
def install_infisical() -> None:
    try:
        if shutil.which("infisical"):
            typer.echo("Infisical is already installed.")
            return

        operating_system = platform.system()
        if operating_system == "Windows":
            subprocess.run(
                [
                    "winget",
                    "install",
                    "--id",
                    "infisical.infisical",
                    "--exact",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ],
                check=True,
            )
        elif operating_system == "Linux":
            subprocess.run(
                [
                    "bash",
                    "-c",
                    "curl -1sLf 'https://artifacts-cli.infisical.com/setup.deb.sh' "
                    "| sudo -E bash && sudo apt-get install -y infisical",
                ],
                check=True,
            )
        else:
            typer.echo(f"Warning: Infisical installation is not supported on {operating_system}.")
            return
    except Exception as error:
        typer.echo(f"Warning: Could not install Infisical: {error}")
        return

    typer.echo("Infisical installation completed.")


@attach_precommit_hooks_app.command()
def attach_precommit_hooks(
    root: Path | None = typer.Option(None, "--root", help="Platform repository root."),
) -> None:
    repository_root = (root or Path.cwd()).resolve()
    repository_paths = [repository_root]
    shared_config_path = Path(__file__).with_name("pre-commit-config.yaml")

    submodules = subprocess.run(
        ["git", "submodule", "foreach", "--quiet", "echo $sm_path"],
        cwd=repository_root,
        capture_output=True,
        check=True,
        text=True,
    )
    repository_paths.extend(
        repository_root / path
        for path in submodules.stdout.splitlines()
        if path.strip()
    )

    for repository_path in repository_paths:
        effective_config_path = _merge_precommit_config(repository_path, shared_config_path)
        typer.echo(f"Installing pre-commit hooks in {repository_path}")
        subprocess.run(
            ["pre-commit", "install", "--install-hooks", "--config", str(effective_config_path)],
            cwd=repository_path,
            check=True,
        )
        subprocess.run(
            ["pre-commit", "install", "--hook-type", "commit-msg", "--config", str(effective_config_path)],
            cwd=repository_path,
            check=True,
        )


if __name__ == "__main__":
    install_infisical_app()
