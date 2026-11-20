#!/usr/bin/env python3
"""Generate CREATE TABLE statements from a dbt source YAML file.

Example:
    generate-source-tables --source olids_pseudo --yaml models/src_olids.yml
    python -m dbt_platform_tooling.generate_source_tables --help
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import duckdb
import yaml


def quote_ident(name: str) -> str:
    """Quote identifiers so names remain valid SQL across warehouses."""
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def to_duckdb_data_type(data_type: str) -> str:
    normalized = data_type.strip()
    if not normalized:
        return "VARCHAR"

    upper = normalized.upper()
    base = upper.split("(", 1)[0].strip()

    mapping = {
        "NUMBER": "DECIMAL",
        "NUMERIC": "DECIMAL",
        "TIMESTAMP_TZ": "TIMESTAMPTZ",
        "TIMESTAMP_LTZ": "TIMESTAMPTZ",
        "TIMESTAMP_NTZ": "TIMESTAMP",
        # Preserve semi-structured source payloads as text in DuckDB unit fixtures.
        "VARIANT": "VARCHAR",
        "OBJECT": "VARCHAR",
        "ARRAY": "VARCHAR",
    }
    mapped = mapping.get(base)
    if mapped is None:
        return normalized

    if base in {"NUMBER", "NUMERIC"}:
        precision_scale = re.search(r"\(\s*\d+\s*(,\s*\d+\s*)?\)", normalized)
        if precision_scale:
            return f"{mapped}{precision_scale.group(0)}"

    return mapped


def build_column_ddl(column: dict[str, Any], *, duckdb_mode: bool = False) -> str:
    col_name = str(column.get("name", "")).strip()
    if not col_name:
        raise ValueError("Encountered a column without a name.")

    data_type = str(column.get("data_type", "VARCHAR")).strip() or "VARCHAR"
    if duckdb_mode:
        data_type = to_duckdb_data_type(data_type)
    return f"    {quote_ident(col_name)} {data_type}"


def build_table_ddl(
    table: dict[str, Any],
    database: str | None,
    schema: str | None,
    *,
    duckdb_mode: bool = False,
    table_name_override: str | None = None,
) -> str:
    table_name = table_name_override or str(table.get("name", "")).strip()
    if not table_name:
        raise ValueError("Encountered a table without a name.")

    qualified_parts = []
    if database:
        qualified_parts.append(quote_ident(database))
    if schema:
        qualified_parts.append(quote_ident(schema))
    qualified_parts.append(quote_ident(table_name))
    qualified_name = ".".join(qualified_parts)

    columns = table.get("columns") or []
    if not columns:
        return (
            f"-- Skipped {qualified_name}: no columns defined in YAML.\n"
            f"-- Add `columns` under table `{table_name}` to generate CREATE TABLE."
        )

    column_lines = [build_column_ddl(col, duckdb_mode=duckdb_mode) for col in columns]
    columns_sql = ",\n".join(column_lines)
    create_clause = "CREATE OR REPLACE TABLE" if duckdb_mode else "CREATE TABLE IF NOT EXISTS"
    return f"{create_clause} {qualified_name} (\n{columns_sql}\n);"


def build_schema_ddl(schema: str | None) -> str | None:
    if not schema:
        return None
    return f"CREATE SCHEMA IF NOT EXISTS {quote_ident(schema)};"


def load_source(yaml_path: Path, source_name: str) -> dict[str, Any]:
    with yaml_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    if not isinstance(config, dict):
        raise ValueError(f"Unexpected YAML structure in {yaml_path}.")

    sources = config.get("sources") or []
    for source in sources:
        if str(source.get("name", "")).strip() == source_name:
            return source

    available = ", ".join(str(s.get("name", "")).strip() for s in sources if isinstance(s, dict))
    raise ValueError(
        f"Source '{source_name}' not found in {yaml_path}. "
        f"Available sources: {available or 'none'}"
    )


def resolve_value(raw_value: Any, override: str | None) -> str | None:
    if override is not None:
        return override
    if raw_value is None:
        return None

    text = str(raw_value).strip()
    # Keep dbt Jinja expressions out of the generated DDL by default.
    if "{{" in text or "}}" in text:
        return None
    return text or None


def execute_duckdb(statements: list[str], duckdb_file: Path) -> None:
    duckdb_file.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(duckdb_file))
    try:
        for statement in statements:
            if statement.lstrip().startswith("--"):
                print(statement, file=sys.stderr)
                continue
            connection.execute(statement)
    finally:
        connection.close()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate CREATE TABLE SQL from a dbt source YAML by source name."
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Source name from YAML (for example: olids_pseudo).",
    )
    parser.add_argument(
        "--yaml",
        required=True,
        help="Path to the dbt source YAML file.",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="Override database name used in generated SQL.",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help="Override schema name used in generated SQL.",
    )
    parser.add_argument(
        "--table",
        default=None,
        help=(
            "Override the physical table name used in generated SQL. Use this when a "
            "table's identifier resolves differently per target via Jinja; "
            "this command does not evaluate Jinja."
        ),
    )
    parser.add_argument(
        "--duckdb-file",
        default=None,
        help="Execute statements against this DuckDB database file.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write SQL output to this file instead of stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    yaml_path = Path(args.yaml)

    if not yaml_path.exists():
        print(f"YAML file not found: {yaml_path}", file=sys.stderr)
        return 1

    try:
        source = load_source(yaml_path, args.source)
        database = resolve_value(source.get("database"), args.database)
        schema = resolve_value(source.get("schema"), args.schema)

        if args.duckdb_file:
            # DuckDB file execution does not use the source database name.
            database = None
            if not schema:
                raise ValueError(
                    "DuckDB execution requires a concrete schema name. "
                    "Pass --schema to override any dbt Jinja expression."
                )

        tables = source.get("tables") or []
        if not tables:
            raise ValueError(f"Source '{args.source}' contains no tables.")

        statements: list[str] = []
        schema_statement = build_schema_ddl(schema)
        if schema_statement:
            statements.append(schema_statement)

        statements.extend(
            build_table_ddl(
                table,
                database,
                schema,
                duckdb_mode=bool(args.duckdb_file),
                table_name_override=args.table,
            )
            for table in tables
        )
        sql = "\n\n".join(statements) + "\n"

        if args.duckdb_file:
            duckdb_path = Path(args.duckdb_file)
            execute_duckdb(statements, duckdb_path)
            print(f"Executed DDL against DuckDB file: {duckdb_path}")

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(sql, encoding="utf-8")
            print(f"Wrote SQL to {output_path}")
        elif not args.duckdb_file:
            print(sql)

        return 0
    except (OSError, ValueError, RuntimeError, yaml.YAMLError, duckdb.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
