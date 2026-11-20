"""Synchronize dbt source definitions with built model metadata."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


def sync_source(source: dict[str, Any], models: dict[str, list[dict[str, str]]]) -> None:
    existing = {table["name"].upper(): table for table in (source.get("tables") or [])}
    tables = []
    for name, model_columns in sorted(models.items()):
        table = existing.get(name.upper(), {"name": name})
        columns = {column["name"].upper(): column for column in (table.get("columns") or [])}
        updated_columns = []
        for model_column in model_columns:
            column_name = model_column["name"]
            column = columns.get(column_name.upper(), {"name": column_name})
            column["data_type"] = model_column["data_type"]
            if not column.get("description") and model_column.get("description"):
                column["description"] = model_column["description"]
            updated_columns.append(column)
        table["columns"] = updated_columns
        tables.append(table)
    source["tables"] = tables


def catalog_models(
    manifest: dict[str, Any],
    catalog: dict[str, Any],
    package: str,
    database: str,
    schema: str,
) -> dict[str, list[dict[str, str]]]:
    if catalog.get("errors"):
        raise ValueError(f"Catalog contains errors: {catalog['errors']}")
    manifest_id = manifest.get("metadata", {}).get("invocation_id")
    catalog_id = catalog.get("metadata", {}).get("invocation_id")
    if not manifest_id or manifest_id != catalog_id:
        raise ValueError("Manifest and catalog must be from the same dbt docs generate invocation.")

    models: dict[str, list[dict[str, str]]] = {}
    catalog_nodes = catalog.get("nodes", {})
    for unique_id, node in manifest.get("nodes", {}).items():
        if (
            node.get("resource_type") != "model"
            or node.get("package_name") != package
            or str(node.get("database", "")).upper() != database.upper()
            or str(node.get("schema", "")).upper() != schema.upper()
            or node.get("config", {}).get("materialized") == "ephemeral"
        ):
            continue
        entry = catalog_nodes.get(unique_id)
        if not entry:
            raise ValueError(f"Model {unique_id} is missing from the catalog; build it and regenerate docs.")
        metadata = entry.get("metadata", {})
        name = metadata.get("name")
        if (
            not isinstance(name, str)
            or not name
            or str(metadata.get("database", "")).upper() != database.upper()
            or str(metadata.get("schema", "")).upper() != schema.upper()
            or name.upper() != str(node.get("alias", "")).upper()
        ):
            raise ValueError(f"Catalog relation for {unique_id} does not match the manifest.")
        catalog_columns = entry.get("columns") or {}
        column_entries = list(catalog_columns.values())
        if not column_entries or any(
            not isinstance(column.get("name"), str)
            or not column["name"]
            or not isinstance(column.get("index"), int)
            or not isinstance(column.get("type"), str)
            or not column["type"].strip()
            for column in column_entries
        ):
            raise ValueError(f"Model {unique_id} has missing or invalid catalog columns or types.")
        column_entries.sort(key=lambda column: column["index"])
        names = [column["name"] for column in column_entries]
        if len({name.upper() for name in names}) != len(names) or len(
            {column["index"] for column in column_entries}
        ) != len(names):
            raise ValueError(f"Model {unique_id} has duplicate catalog columns or indexes.")
        if name.upper() in {model_name.upper() for model_name in models}:
            raise ValueError(f"Multiple models produce {database}.{schema}.{name}.")
        documented_columns = {
            column_name.upper(): column
            for column_name, column in (node.get("columns") or {}).items()
        }
        models[name] = []
        for column in column_entries:
            column_name = column["name"]
            description = documented_columns.get(column_name.upper(), {}).get("description") or column.get("comment")
            result = {"name": column_name, "data_type": column["type"].strip()}
            if isinstance(description, str) and description.strip():
                result["description"] = description
            models[name].append(result)
    if not models:
        raise ValueError(f"No models found for package {package} in {database}.{schema}.")
    return models


def changes_for_source(source: dict[str, Any], models: dict[str, list[dict[str, str]]]) -> list[str]:
    tables = source.get("tables") or []
    existing = {str(table["name"]).upper(): table for table in tables}
    if len(existing) != len(tables):
        raise ValueError(f"Source {source['name']} has duplicate table names.")
    desired = {name.upper(): columns for name, columns in models.items()}
    changes = []
    for name in sorted(existing.keys() - desired.keys()):
        changes.append(f"  - table {name}")
    for name in sorted(desired.keys() - existing.keys()):
        changes.append(f"  + table {name}")
    for name in sorted(existing.keys() & desired.keys()):
        identifier = existing[name].get("identifier")
        if identifier is not None and str(identifier).upper() != name:
            raise ValueError(f"Source {source['name']}.{name} has a different physical identifier.")
        columns = existing[name].get("columns") or []
        old_names = [str(column["name"]).upper() for column in columns]
        new_names = [column["name"].upper() for column in desired[name]]
        if len(set(old_names)) != len(old_names):
            raise ValueError(f"Source {source['name']}.{name} has duplicate columns.")
        for column in sorted(set(old_names) - set(new_names)):
            changes.append(f"  - column {name}.{column}")
        for column in sorted(set(new_names) - set(old_names)):
            changes.append(f"  + column {name}.{column}")
        old_columns = {str(column["name"]).upper(): column for column in columns}
        for model_column in desired[name]:
            column_name = model_column["name"].upper()
            if column_name not in old_columns:
                continue
            old_column = old_columns[column_name]
            if old_column.get("data_type") != model_column["data_type"]:
                changes.append(f"  ~ type {name}.{column_name}: {old_column.get('data_type', '<missing>')} -> {model_column['data_type']}")
            if not old_column.get("description") and model_column.get("description"):
                changes.append(f"  + description {name}.{column_name}")
        if set(old_names) == set(new_names) and old_names != new_names:
            changes.append(f"  ~ column order {name}")
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yaml", required=True, help="Source YAML to synchronize.")
    parser.add_argument("--manifest", required=True, help="dbt docs generate manifest.json.")
    parser.add_argument("--catalog", required=True, help="dbt docs generate catalog.json.")
    parser.add_argument("--package", required=True, help="dbt project/package containing the models.")
    parser.add_argument("--database", required=True, help="Database containing the built models.")
    parser.add_argument(
        "--source", action="append", required=True, metavar="NAME=SCHEMA",
        help="Map a source name to a catalog schema; repeat for each source.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Apply changes; otherwise only report them.")
    mode.add_argument("--check", action="store_true", help="Exit with an error if changes are needed.")
    args = parser.parse_args(argv)
    try:
        mappings = {}
        for mapping in args.source:
            name, separator, schema = mapping.partition("=")
            if not separator or not name.strip() or not schema.strip() or name.strip() in mappings:
                raise ValueError(f"Invalid or duplicate source mapping: {mapping}")
            mappings[name.strip()] = schema.strip()

        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)
        yaml.width = 4096
        yaml_path = Path(args.yaml)
        yaml.line_break = "\r\n" if b"\r\n" in yaml_path.read_bytes() else "\n"
        with yaml_path.open("r", encoding="utf-8") as handle:
            document = yaml.load(handle)
        if not isinstance(document, dict) or not isinstance(document.get("sources"), list):
            raise ValueError("Source YAML must contain a sources list.")
        sources = document.get("sources", [])
        by_name = {source["name"]: source for source in sources}
        if len(by_name) != len(sources):
            raise ValueError("Source YAML contains duplicate source names.")

        pending = []
        for source_name, schema in mappings.items():
            if source_name not in by_name:
                raise ValueError(f"Source {source_name} not found in {yaml_path}.")
            models = catalog_models(manifest, catalog, args.package, args.database, schema)
            source = by_name[source_name]
            changes = changes_for_source(source, models)
            pending.append((source, models, changes))

        for source, models, changes in pending:
            print(f"{source['name']}: {len(models)} models, {len(changes)} changes")
            for change in changes:
                print(change)
        if args.write and any(changes for _, _, changes in pending):
            for source, models, _ in pending:
                sync_source(source, models)
            with yaml_path.open("w", encoding="utf-8", newline="") as handle:
                yaml.dump(document, handle)
            print(f"Updated {yaml_path}")
        elif not args.write and not args.check:
            print("Preview only; pass --write to apply changes.")
        if args.check and any(changes for _, _, changes in pending):
            print("Source definitions are out of date.", file=sys.stderr)
            return 1
        return 0
    except (OSError, ValueError, KeyError, TypeError, YAMLError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
