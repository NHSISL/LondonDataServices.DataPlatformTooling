import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from dbt_platform_tooling.sync_sources import main, sync_source


class SyncSourceTests(unittest.TestCase):
    def test_tables_and_columns_are_reconciled_without_losing_metadata(self):
        source = {
            "name": "olids_pid",
            "tables": [
                {
                    "name": "PATIENT",
                    "description": "Patient details",
                    "columns": [
                        {"name": "ID", "description": "Patient identifier", "data_tests": ["not_null"]},
                        {"name": "OBSOLETE"},
                    ],
                },
                {"name": "PSEUDO_ONLY"},
            ],
        }

        sync_source(source, {
            "PATIENT": [
                {"name": "ID", "data_type": "VARCHAR", "description": "Upstream identifier"},
                {"name": "NHS_NUMBER", "data_type": "VARCHAR", "description": "NHS number"},
            ],
            "PID_ONLY": [{"name": "ID", "data_type": "INTEGER"}],
        })

        self.assertEqual(source["tables"], [
            {
                "name": "PATIENT",
                "description": "Patient details",
                "columns": [
                    {"name": "ID", "description": "Patient identifier", "data_tests": ["not_null"], "data_type": "VARCHAR"},
                    {"name": "NHS_NUMBER", "data_type": "VARCHAR", "description": "NHS number"},
                ],
            },
            {"name": "PID_ONLY", "columns": [{"name": "ID", "data_type": "INTEGER"}]},
        ])


class SyncCommandTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        root = Path(temporary_directory.name)
        self.source_path = root / "sources.yml"
        self.manifest_path = root / "manifest.json"
        self.catalog_path = root / "catalog.json"
        self.source_path.write_text(
            "version: 2\n# Keep this comment\nsources:\n"
            "  - name: olids_pid\n    schema: OLIDS_PID\n    tables:\n"
            "      - name: SHARED\n        description: Keep me\n        columns:\n"
            "          - name: ID\n            description: Key\n            data_type: VARCHAR\n"
            "          - name: OLD\n"
            "      - name: PATIENT_DATE_FLAGS\n"
            "  - name: olids_pseudo\n    schema: OLIDS_PSEUDO\n    tables:\n"
            "      - name: SHARED\n        columns:\n          - name: ID\n"
            "  - name: unrelated\n    tables:\n      - name: KEEP\n",
            encoding="utf-8",
        )
        self.manifest = {"metadata": {"invocation_id": "same"}, "nodes": {}}
        self.catalog = {"metadata": {"invocation_id": "same"}, "nodes": {}}
        self.add_model("pid_shared", "OLIDS_PID", "SHARED", ["ID", "NHS_NUMBER"])
        self.add_model("pid_only", "OLIDS_PID", "PATIENT_AGE_FLAGS", ["ID"])
        self.add_model("pseudo_shared", "OLIDS_PSEUDO", "SHARED", ["ID"])
        self.add_model("pseudo_only", "OLIDS_PSEUDO", "PATIENT_DATE_FLAGS", ["ID"])
        self.write_artifacts()

    def add_model(self, model, schema, alias, columns):
        unique_id = f"model.enrichment.{model}"
        self.manifest["nodes"][unique_id] = {
            "resource_type": "model", "package_name": "enrichment", "database": "BUILT",
            "schema": schema, "alias": alias, "config": {"materialized": "view"},
            "columns": {"NHS_NUMBER": {"description": "Upstream NHS number"}},
        }
        self.catalog["nodes"][unique_id] = {
            "metadata": {"database": "BUILT", "schema": schema, "name": alias},
            "columns": {
                name: {
                    "name": name, "index": index,
                    "type": "NUMBER(18,0)" if schema == "OLIDS_PID" and name == "ID" else "VARCHAR",
                    "comment": "Catalog identifier" if name == "ID" else None,
                }
                for index, name in enumerate(columns, 1)
            },
        }

    def write_artifacts(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.catalog_path.write_text(json.dumps(self.catalog), encoding="utf-8")

    def run_sync(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main([
                "--yaml", str(self.source_path), "--manifest", str(self.manifest_path),
                "--catalog", str(self.catalog_path), "--package", "enrichment",
                "--database", "BUILT", "--source", "olids_pid=OLIDS_PID",
                "--source", "olids_pseudo=OLIDS_PSEUDO", *args,
            ])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_preview_write_and_repeat_are_schema_specific(self):
        original = self.source_path.read_text(encoding="utf-8")
        result, output, _ = self.run_sync()
        self.assertEqual(result, 0)
        self.assertIn("- table PATIENT_DATE_FLAGS", output)
        self.assertIn("+ table PATIENT_AGE_FLAGS", output)
        self.assertIn("+ table PATIENT_DATE_FLAGS", output)
        self.assertIn("~ type SHARED.ID: VARCHAR -> NUMBER(18,0)", output)
        self.assertIn("+ description SHARED.ID", output)
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), original)
        result, _, error = self.run_sync("--check")
        self.assertEqual(result, 1)
        self.assertIn("out of date", error)
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), original)

        self.assertEqual(self.run_sync("--write")[0], 0)
        updated = self.source_path.read_text(encoding="utf-8")
        self.assertIn("# Keep this comment", updated)
        sources = {source["name"]: source for source in yaml.safe_load(updated)["sources"]}
        pid = {table["name"]: table for table in sources["olids_pid"]["tables"]}
        pseudo = {table["name"]: table for table in sources["olids_pseudo"]["tables"]}
        self.assertEqual(set(pid), {"SHARED", "PATIENT_AGE_FLAGS"})
        self.assertEqual(set(pseudo), {"SHARED", "PATIENT_DATE_FLAGS"})
        self.assertEqual(pid["SHARED"]["description"], "Keep me")
        self.assertEqual(pid["SHARED"]["columns"], [
            {"name": "ID", "description": "Key", "data_type": "NUMBER(18,0)"},
            {"name": "NHS_NUMBER", "data_type": "VARCHAR", "description": "Upstream NHS number"},
        ])
        self.assertEqual(pseudo["SHARED"]["columns"], [
            {"name": "ID", "data_type": "VARCHAR", "description": "Catalog identifier"},
        ])
        self.assertEqual(sources["unrelated"]["tables"], [{"name": "KEEP"}])
        result, output, _ = self.run_sync("--write")
        self.assertEqual(result, 0)
        self.assertIn("0 changes", output)
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), updated)
        self.assertEqual(self.run_sync("--check")[0], 0)

    def test_type_only_drift_is_detected_and_updated(self):
        self.assertEqual(self.run_sync("--write")[0], 0)
        self.catalog["nodes"]["model.enrichment.pid_shared"]["columns"]["ID"]["type"] = "NUMBER(20,0)"
        self.write_artifacts()
        result, output, _ = self.run_sync("--check")
        self.assertEqual(result, 1)
        self.assertIn("~ type SHARED.ID: NUMBER(18,0) -> NUMBER(20,0)", output)
        self.assertEqual(self.run_sync("--write")[0], 0)
        sources = yaml.safe_load(self.source_path.read_text(encoding="utf-8"))["sources"]
        table = next(table for table in sources[0]["tables"] if table["name"] == "SHARED")
        self.assertEqual(table["columns"][0]["data_type"], "NUMBER(20,0)")
        self.assertEqual(self.run_sync("--check")[0], 0)

    def test_incomplete_or_inconsistent_artifacts_never_write(self):
        original = self.source_path.read_text(encoding="utf-8")
        scenarios = [
            ("missing", "missing from the catalog"),
            ("mismatch", "same dbt docs generate invocation"),
            ("errors", "Catalog contains errors"),
            ("empty_columns", "missing or invalid catalog columns"),
            ("missing_type", "missing or invalid catalog columns or types"),
        ]
        for scenario, message in scenarios:
            with self.subTest(scenario=scenario):
                self.reset_artifacts()
                if scenario == "missing":
                    del self.catalog["nodes"]["model.enrichment.pseudo_only"]
                elif scenario == "mismatch":
                    self.catalog["metadata"]["invocation_id"] = "different"
                elif scenario == "errors":
                    self.catalog["errors"] = ["catalog query failed"]
                elif scenario == "empty_columns":
                    self.catalog["nodes"]["model.enrichment.pid_only"]["columns"] = {}
                else:
                    del self.catalog["nodes"]["model.enrichment.pid_only"]["columns"]["ID"]["type"]
                self.write_artifacts()
                result, _, error = self.run_sync("--write")
                self.assertEqual(result, 1)
                self.assertIn(message, error)
                self.assertEqual(self.source_path.read_text(encoding="utf-8"), original)

    def reset_artifacts(self):
        self.catalog = {"metadata": {"invocation_id": "same"}, "nodes": {}}
        self.manifest["nodes"] = {}
        self.add_model("pid_shared", "OLIDS_PID", "SHARED", ["ID", "NHS_NUMBER"])
        self.add_model("pid_only", "OLIDS_PID", "PATIENT_AGE_FLAGS", ["ID"])
        self.add_model("pseudo_shared", "OLIDS_PSEUDO", "SHARED", ["ID"])
        self.add_model("pseudo_only", "OLIDS_PSEUDO", "PATIENT_DATE_FLAGS", ["ID"])


class DbtCatalogIntegrationTests(unittest.TestCase):
    def test_real_dbt_catalog_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            models = root / "models"
            models.mkdir()
            (root / "dbt_project.yml").write_text(
                "name: enrichment\nversion: '1.0'\nconfig-version: 2\nprofile: enrichment\n",
                encoding="utf-8",
            )
            (root / "profiles.yml").write_text(
                "enrichment:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
                "      path: warehouse.duckdb\n      schema: main\n      threads: 1\n",
                encoding="utf-8",
            )
            (models / "pid.sql").write_text(
                "{{ config(schema='OLIDS_PID', alias='PATIENT_AGE_FLAGS') }}\nselect 1 as ID\n",
                encoding="utf-8",
            )
            (models / "pseudo.sql").write_text(
                "{{ config(schema='OLIDS_PSEUDO', alias='PATIENT_DATE_FLAGS') }}\nselect 1 as ID\n",
                encoding="utf-8",
            )
            executable = Path(sys.executable).parent / ("dbt.exe" if os.name == "nt" else "dbt")
            for command in (["run"], ["docs", "generate"]):
                result = subprocess.run(
                    [str(executable), *command, "--project-dir", str(root), "--profiles-dir", str(root)],
                    cwd=root, capture_output=True, text=True, encoding="utf-8",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            source_path = root / "source.yml"
            source_path.write_text(
                "version: 2\nsources:\n  - name: olids_pid\n    tables: []\n"
                "  - name: olids_pseudo\n    tables: []\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = main([
                    "--yaml", str(source_path), "--manifest", str(root / "target" / "manifest.json"),
                    "--catalog", str(root / "target" / "catalog.json"), "--package", "enrichment",
                    "--database", "warehouse", "--source", "olids_pid=main_OLIDS_PID",
                    "--source", "olids_pseudo=main_OLIDS_PSEUDO", "--write",
                ])
            self.assertEqual(result, 0)
            sources = yaml.safe_load(source_path.read_text(encoding="utf-8"))["sources"]
            self.assertEqual(sources[0]["tables"], [
                {"name": "PATIENT_AGE_FLAGS", "columns": [{"name": "ID", "data_type": "INTEGER"}]},
            ])
            self.assertEqual(sources[1]["tables"], [
                {"name": "PATIENT_DATE_FLAGS", "columns": [{"name": "ID", "data_type": "INTEGER"}]},
            ])


if __name__ == "__main__":
    unittest.main()
