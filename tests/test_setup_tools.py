import unittest
from unittest.mock import call, patch

from dbt_platform_tooling import setup_tools


class UpdateInfisicalTests(unittest.TestCase):
    @patch("dbt_platform_tooling.setup_tools.subprocess.run")
    @patch("dbt_platform_tooling.setup_tools.platform.system", return_value="Windows")
    @patch("dbt_platform_tooling.setup_tools.shutil.which", return_value="infisical")
    def test_updates_on_windows(self, _which, _system, run):
        setup_tools.update_infisical()

        run.assert_called_once_with(
            [
                "winget",
                "upgrade",
                "--id",
                "infisical.infisical",
                "--exact",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            check=True,
        )

    @patch("dbt_platform_tooling.setup_tools.subprocess.run")
    @patch("dbt_platform_tooling.setup_tools.platform.system", return_value="Linux")
    @patch("dbt_platform_tooling.setup_tools.shutil.which", return_value="/usr/bin/infisical")
    def test_updates_on_linux(self, _which, _system, run):
        setup_tools.update_infisical()

        self.assertEqual(
            run.call_args_list,
            [
                call(["sudo", "apt-get", "update"], check=True),
                call(
                    ["sudo", "apt-get", "install", "--only-upgrade", "-y", "infisical"],
                    check=True,
                ),
            ],
        )

    @patch("dbt_platform_tooling.setup_tools.subprocess.run")
    @patch("dbt_platform_tooling.setup_tools.shutil.which", return_value=None)
    def test_skips_when_infisical_is_not_installed(self, _which, run):
        setup_tools.update_infisical()

        run.assert_not_called()

    @patch("dbt_platform_tooling.setup_tools.subprocess.run")
    @patch("dbt_platform_tooling.setup_tools.platform.system", return_value="Darwin")
    @patch("dbt_platform_tooling.setup_tools.shutil.which", return_value="/usr/local/bin/infisical")
    def test_warns_on_unsupported_operating_system(self, _which, _system, run):
        setup_tools.update_infisical()

        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
