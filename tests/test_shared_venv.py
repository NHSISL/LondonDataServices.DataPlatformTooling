import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


TOOLING_ROOT = Path(__file__).resolve().parents[1]
HELPER = TOOLING_ROOT / "scripts" / "use-platform-venv.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(sys.platform == "win32" and POWERSHELL, "Requires Windows PowerShell")
class SharedVenvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp_dir.cleanup)
        cls.venv = Path(cls.temp_dir.name) / "test venv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(cls.venv)],
            check=True,
            capture_output=True,
            text=True,
        )
        (cls.venv / "Scripts" / "dbt.exe").touch()
        package = cls.venv / "Lib" / "site-packages" / "dbt_platform_tooling"
        package.mkdir()
        (package / "__init__.py").touch()

    def run_helper(self, venv, helper=HELPER):
        env = os.environ.copy()
        env["TEST_HELPER"] = str(helper)
        env["TEST_VENV"] = str(venv)
        return subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference = 'Stop'; "
                ". $env:TEST_HELPER -VenvDir $env:TEST_VENV; "
                ". $venvActivate; Write-Output \"SELECTED=$env:VIRTUAL_ENV\"",
            ],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_custom_venv_is_selected_and_activatable(self):
        result = self.run_helper(self.venv)
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = next(line.removeprefix("SELECTED=") for line in result.stdout.splitlines()
                        if line.startswith("SELECTED="))
        self.assertTrue(os.path.samefile(selected, self.venv))

    def test_relative_venv_is_resolved_from_platform_root(self):
        relative = os.path.relpath(self.venv, TOOLING_ROOT.parent)
        result = self.run_helper(relative)
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = next(line.removeprefix("SELECTED=") for line in result.stdout.splitlines()
                        if line.startswith("SELECTED="))
        self.assertTrue(os.path.samefile(selected, self.venv))

    def test_missing_venv_fails_with_setup_guidance(self):
        result = self.run_helper(Path(self.temp_dir.name) / "missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Shared virtual environment is not ready", result.stderr)

    def test_wrong_python_version_fails(self):
        tooling = Path(self.temp_dir.name) / "other-tooling"
        (tooling / "scripts").mkdir(parents=True)
        helper = tooling / "scripts" / HELPER.name
        shutil.copyfile(HELPER, helper)
        (tooling / "PYTHON_VERSION").write_text("0.0\n", encoding="utf-8")
        result = self.run_helper(self.venv, helper)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must use Python 0.0", result.stderr)


if __name__ == "__main__":
    unittest.main()
