import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb
import yaml

from dbt_platform_tooling.generate_source_tables import (
    build_table_ddl,
    main,
    quote_ident,
    to_duckdb_data_type,
)


class SourceTableTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.yaml_path = self.root / "source.yml"
        self.database_path = self.root / "nested" / "fixtures.duckdb"
        self.source = {
            "name": "example",
            "database": "{{ var('database') }}",
            "schema": "CONTROL",
            "tables": [
                {
                    "name": "EVENT",
                    "columns": [
                        {"name": "ID", "data_type": "NUMBER(18,0)"},
                        {"name": "CREATED_AT", "data_type": "TIMESTAMP_NTZ"},
                        {"name": "PAYLOAD", "data_type": "VARIANT"},
                        {"name": "DESCRIPTION"},
                    ],
                },
                {"name": "NO_COLUMNS"},
            ],
        }
        self.write_source()

    def write_source(self):
        self.yaml_path.write_text(
            yaml.safe_dump({"version": 2, "sources": [self.source]}),
            encoding="utf-8",
        )

    def run_generator(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main([
                "--source", "example", "--yaml", str(self.yaml_path), *args,
            ])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_type_mapping(self):
        for original, expected in {
            "NUMBER(18, 2)": "DECIMAL(18, 2)",
            "NUMERIC": "DECIMAL",
            "TIMESTAMP_NTZ(9)": "TIMESTAMP",
            "TIMESTAMP_LTZ": "TIMESTAMPTZ",
            "TIMESTAMP_TZ": "TIMESTAMPTZ",
            "VARIANT": "VARCHAR",
            "OBJECT": "VARCHAR",
            "ARRAY": "VARCHAR",
            "BOOLEAN": "BOOLEAN",
            "": "VARCHAR",
        }.items():
            with self.subTest(original=original):
                self.assertEqual(to_duckdb_data_type(original), expected)

    def test_identifier_quoting(self):
        self.assertEqual(quote_ident('a"b'), '"a""b"')

    def test_sql_output_preserves_source_types_and_qualifiers(self):
        result, stdout, stderr = self.run_generator("--database", "WAREHOUSE")
        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn('CREATE TABLE IF NOT EXISTS "WAREHOUSE"."CONTROL"."EVENT"', stdout)
        self.assertIn('"ID" NUMBER(18,0)', stdout)
        self.assertIn('"PAYLOAD" VARIANT', stdout)
        self.assertIn('-- Skipped "WAREHOUSE"."CONTROL"."NO_COLUMNS"', stdout)

    def test_duckdb_creates_typed_empty_tables_and_reports_skips(self):
        result, _, stderr = self.run_generator(
            "--duckdb-file", str(self.database_path), "--database", "IGNORED",
        )
        self.assertEqual(result, 0)
        self.assertIn('-- Skipped "CONTROL"."NO_COLUMNS"', stderr)
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            columns = connection.execute('DESCRIBE "CONTROL"."EVENT"').fetchall()
            self.assertEqual(
                [(row[0], row[1]) for row in columns],
                [
                    ("ID", "DECIMAL(18,0)"),
                    ("CREATED_AT", "TIMESTAMP"),
                    ("PAYLOAD", "VARCHAR"),
                    ("DESCRIPTION", "VARCHAR"),
                ],
            )
            self.assertEqual(
                connection.execute('SELECT count(*) FROM "CONTROL"."EVENT"').fetchone(),
                (0,),
            )

    def test_rerun_replaces_fixture_tables(self):
        args = ("--duckdb-file", str(self.database_path))
        self.assertEqual(self.run_generator(*args)[0], 0)
        with duckdb.connect(str(self.database_path)) as connection:
            connection.execute('INSERT INTO "CONTROL"."EVENT" (ID) VALUES (1)')
        self.assertEqual(self.run_generator(*args)[0], 0)
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            self.assertEqual(
                connection.execute('SELECT count(*) FROM "CONTROL"."EVENT"').fetchone(),
                (0,),
            )

    def test_schema_and_physical_table_override(self):
        self.source["schema"] = "{{ target.schema }}"
        self.source["tables"] = [self.source["tables"][0]]
        self.write_source()
        result, _, _ = self.run_generator(
            "--duckdb-file", str(self.database_path),
            "--schema", "LOCAL", "--table", 'physical"table',
        )
        self.assertEqual(result, 0)
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT table_schema, table_name FROM information_schema.tables"
                ).fetchall(),
                [("LOCAL", 'physical"table')],
            )

    def test_output_file(self):
        output = self.root / "sql" / "source.sql"
        result, stdout, _ = self.run_generator("--output", str(output))
        self.assertEqual(result, 0)
        self.assertIn("Wrote SQL", stdout)
        self.assertIn('CREATE TABLE IF NOT EXISTS "CONTROL"."EVENT"', output.read_text())

    def test_missing_source_and_yaml_fail(self):
        result, _, stderr = self.run_generator("--source", "missing")
        self.assertEqual(result, 1)
        self.assertIn("Available sources: example", stderr)
        self.yaml_path.unlink()
        result, _, stderr = self.run_generator()
        self.assertEqual(result, 1)
        self.assertIn("YAML file not found", stderr)

    def test_templated_schema_requires_override(self):
        self.source["schema"] = "{{ target.schema }}"
        self.write_source()
        result, _, stderr = self.run_generator("--duckdb-file", str(self.database_path))
        self.assertEqual(result, 1)
        self.assertIn("requires a concrete schema", stderr)
        self.assertFalse(self.database_path.exists())

    def test_invalid_yaml_fails(self):
        self.yaml_path.write_text("sources: [", encoding="utf-8")
        self.assertEqual(self.run_generator()[0], 1)

    def test_invalid_duckdb_type_fails(self):
        self.source["tables"][0]["columns"][0]["data_type"] = "NOT_A_TYPE"
        self.write_source()
        result, _, stderr = self.run_generator("--duckdb-file", str(self.database_path))
        self.assertEqual(result, 1)
        self.assertIn("NOT_A_TYPE", stderr)

    def test_console_entry_point_reads_process_arguments(self):
        with patch("sys.argv", [
            "generate-source-tables", "--source", "example",
            "--yaml", str(self.yaml_path),
        ]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)

    def test_yaml_path_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["--source", "example"])
        self.assertEqual(error.exception.code, 2)

    def test_invalid_column_name_fails(self):
        with self.assertRaisesRegex(ValueError, "column without a name"):
            build_table_ddl(
                {"name": "BAD", "columns": [{"data_type": "VARCHAR"}]},
                None,
                "CONTROL",
            )


if __name__ == "__main__":
    unittest.main()
