import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mdns_helpers.config import load_config
from mdns_helpers.generator import generate_artifacts
from mdns_helpers.install import plan_install
from mdns_helpers.models import ValidationError
from mdns_helpers.uninstall import plan_uninstall


REPO_ROOT = Path(__file__).resolve().parent.parent


class MdnsHelpersTests(unittest.TestCase):
    def test_validate_sample_config(self):
        config = load_config(str(REPO_ROOT / "examples" / "sample-config.json"))
        self.assertEqual(config.domain, "home.arpa")
        self.assertEqual(len(config.sites), 2)

    def test_local_domain_rejected_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            site_root = temp_path / "site"
            site_root.mkdir()
            config_path = temp_path / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "domain": "local",
                        "host": {"ipv4": "192.168.1.2"},
                        "sites": [
                            {
                                "name": "movies",
                                "hostname": "movies.local",
                                "kind": "static_dir",
                                "source": str(site_root),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValidationError):
                load_config(str(config_path))

    def test_generate_ubuntu_artifacts(self):
        config = load_config(str(REPO_ROOT / "examples" / "sample-config.json"))
        artifacts = generate_artifacts(config, "ubuntu")
        root = config.output_dir / "ubuntu"
        self.assertIn(root / "dns" / "Corefile", artifacts.files)
        self.assertIn(root / "proxy" / "Caddyfile", artifacts.files)
        self.assertIn(root / "services" / "caddy.service", artifacts.files)
        self.assertIn(root / "mdns" / "movies.service", artifacts.files)

    def test_generate_macos_artifacts(self):
        config = load_config(str(REPO_ROOT / "examples" / "sample-config.json"))
        artifacts = generate_artifacts(config, "macos")
        root = config.output_dir / "macos"
        self.assertIn(root / "services" / "io.charley.caddy.plist", artifacts.files)
        self.assertIn(root / "mdns" / "movies.plist", artifacts.files)

    def test_disable_command_updates_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            site_root = temp_path / "movies"
            site_root.mkdir()
            config_path = temp_path / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "domain": "home.arpa",
                        "host": {"ipv4": "192.168.1.10"},
                        "sites": [
                            {
                                "name": "movies",
                                "kind": "static_dir",
                                "source": str(site_root),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mdns_helpers",
                    "-c",
                    str(config_path),
                    "disable",
                    "movies",
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            updated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertFalse(updated["sites"][0]["enabled"])

    def test_uninstall_plan_for_ubuntu(self):
        config = load_config(str(REPO_ROOT / "examples" / "sample-config.json"))
        plan = plan_uninstall(config, "ubuntu")
        deployed = {str(item.path) for item in plan.deployed_paths}
        self.assertIn("/etc/coredns/Corefile", deployed)
        self.assertIn("/etc/systemd/system/caddy.service", deployed)
        self.assertIn("/etc/avahi/services/movies.service", deployed)
        self.assertEqual(plan.pre_commands[0].argv[:3], ["systemctl", "disable", "--now"])
        self.assertEqual(plan.post_commands[0].argv, ["systemctl", "daemon-reload"])

    def test_uninstall_plan_for_macos_generated_only(self):
        config = load_config(str(REPO_ROOT / "examples" / "sample-config.json"))
        plan = plan_uninstall(config, "macos", remove_generated=True, remove_deployed=False)
        self.assertEqual(len(plan.generated_paths), 1)
        self.assertEqual(plan.generated_paths[0].path, REPO_ROOT / "generated" / "macos")
        self.assertEqual(plan.deployed_paths, [])
        self.assertEqual(plan.pre_commands, [])
        self.assertEqual(plan.post_commands, [])

    def test_install_plan_for_ubuntu(self):
        config = load_config(str(REPO_ROOT / "examples" / "sample-config.json"))
        plan = plan_install(config, "ubuntu")
        destinations = {str(item.destination) for item in plan.deployed_copies}
        self.assertIn("/etc/coredns/Corefile", destinations)
        self.assertIn("/etc/systemd/system/caddy.service", destinations)
        self.assertIn("/etc/avahi/services/movies.service", destinations)
        self.assertEqual(plan.pre_commands, [])
        self.assertEqual(plan.post_commands[0].argv, ["systemctl", "daemon-reload"])
        self.assertEqual(
            plan.post_commands[1].argv,
            ["systemctl", "enable", "--now", "coredns.service"],
        )

    def test_install_plan_for_macos(self):
        config = load_config(str(REPO_ROOT / "examples" / "sample-config.json"))
        plan = plan_install(config, "macos")
        destinations = {str(item.destination) for item in plan.deployed_copies}
        self.assertIn("/Library/LaunchDaemons/io.charley.coredns.plist", destinations)
        self.assertIn("/Library/LaunchDaemons/io.charley.mdns.movies.plist", destinations)
        self.assertEqual(
            plan.pre_commands[0].argv,
            ["launchctl", "unload", "-w", "/Library/LaunchDaemons/io.charley.coredns.plist"],
        )
        self.assertEqual(
            plan.post_commands[0].argv,
            ["launchctl", "load", "-w", "/Library/LaunchDaemons/io.charley.coredns.plist"],
        )


if __name__ == "__main__":
    unittest.main()
