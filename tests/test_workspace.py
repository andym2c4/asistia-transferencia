"""Pruebas del lanzador sin PostgreSQL: python3 -m unittest tests.test_workspace."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts/production").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/workspace.sh", self.root / "scripts/workspace.sh")
        shutil.copy2(
            ROOT / "scripts/production/dev.py", self.root / "scripts/production/dev.py"
        )
        (self.root / "data/production").mkdir(parents=True)
        (self.root / "bin").mkdir()
        self.calls = self.root / "calls"
        self.env = {
            **os.environ,
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "WORKSPACE_TEST_CALLS": str(self.calls),
            "TMUX_PANE": "",
        }
        for command in ("docker", "aws", "tmux"):
            self.stub(
                command, 'printf "%s %s\\n" "${0##*/}" "$*" >> "$WORKSPACE_TEST_CALLS"'
            )
        self.stub("ss", "exit 0")

    def stub(self, name, body):
        path = self.root / "bin" / name
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)

    def production(self):
        (self.root / "data/production/active.json").write_text(
            '{"url":"https://example.invalid", "instance_id":"fixture",'
            '"region":"fixture", "activated_at":"fixture",'
            '"last_known_instance_state":"stopped"}'
        )

    def local_environment(self):
        path = self.root / ".venv/bin/asistia"
        path.parent.mkdir(parents=True)
        path.write_text("#!/usr/bin/env bash\nexit 99\n")
        path.chmod(0o755)

    def real_local_environment(self):
        self.production()
        self.local_environment()
        (self.root / "data/production/local-development.json").write_text("{}")
        path = self.root / ".venv/bin/python"
        path.write_text(
            '#!/usr/bin/env bash\nprintf "check local\\n" >> "$WORKSPACE_TEST_CALLS"\n'
        )
        path.chmod(0o755)

    def test_real_local_prepare_selects_separate_persistent_service(self):
        self.real_local_environment()
        result = self.run_action("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check local", self.recorded())
        self.assertIn(
            "docker compose -f docker-compose.local.yml up -d --no-recreate --wait --wait-timeout 30 db_local",
            self.recorded(),
        )
        self.assertNotIn("aws", self.recorded())

    def test_real_local_invalid_configuration_prevents_start(self):
        self.real_local_environment()
        (self.root / ".venv/bin/python").write_text("#!/usr/bin/env bash\nexit 1\n")
        result = self.run_action("prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("docker", self.recorded())

    def test_real_local_stop_preserves_historical_and_test_databases(self):
        self.real_local_environment()
        result = self.run_action("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "docker compose -f docker-compose.local.yml stop db_local", self.recorded()
        )
        self.assertNotIn("db_test", self.recorded())
        self.assertNotIn("aws", self.recorded())

    def run_action(self, action):
        return subprocess.run(
            ["bash", str(self.root / "scripts/workspace.sh"), action],
            cwd="/",
            env=self.env,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

    def recorded(self):
        return self.calls.read_text() if self.calls.exists() else ""

    def test_production_prepare_opens_without_local_dependencies(self):
        self.production()
        self.stub("ss", "echo occupied")
        result = self.run_action("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), "")

    def test_production_panels_remain_usable_offline(self):
        self.production()
        for action in ("web", "logs", "console"):
            with self.subTest(action=action):
                result = self.run_action(action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("EC2", result.stdout)
        self.assertEqual(self.recorded(), "")

    def test_production_stop_only_closes_its_session(self):
        self.production()
        self.stub("ss", "echo occupied")
        result = self.run_action("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tmux kill-session -t =asistia", self.recorded())
        self.assertNotIn("docker", self.recorded())
        self.assertNotIn("aws", self.recorded())

    def test_local_prepare_preserves_database_start(self):
        self.local_environment()
        result = self.run_action("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "docker compose up -d --no-recreate --wait --wait-timeout 30 db",
            self.recorded(),
        )

    def test_local_prepare_rejects_occupied_port(self):
        self.local_environment()
        self.stub("ss", "echo occupied")
        result = self.run_action("prepare")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Puerto 8000 ocupado", result.stderr)
        self.assertEqual(self.recorded(), "")

    def test_local_prepare_requires_environment(self):
        result = self.run_action("prepare")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Falta .venv", result.stderr)
        self.assertEqual(self.recorded(), "")

    def test_local_web_requires_workspace(self):
        result = self.run_action("web")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ws asistia", result.stderr)
        self.assertEqual(self.recorded(), "")

    def test_local_web_enables_full_export_only_in_loopback_process(self):
        self.real_local_environment()
        self.env["TMUX_PANE"] = "%ficticio"
        self.env["ASISTIA_WEB_DEVELOPMENT_EXPORTS"] = "0"
        self.env["ASISTIA_WEB_DEVELOPMENT_TOOLS"] = "0"
        self.stub("tmux", 'if [[ "$1" == display-message ]]; then echo asistia; fi')
        (self.root / ".venv/bin/asistia").write_text(
            '#!/usr/bin/env bash\nprintf "%s|%s|%s\\n" "$ASISTIA_WEB_DEVELOPMENT_EXPORTS" "$ASISTIA_WEB_DEVELOPMENT_TOOLS" "$*"\n'
        )
        result = self.run_action("web")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1|1|web servir --host 127.0.0.1 --puerto 8000", result.stdout)
        self.assertIn("check local", self.recorded())

    def test_local_logs_and_stop_preserve_behavior(self):
        result = self.run_action("logs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("docker compose logs --tail=100 --follow db", self.recorded())
        result = self.run_action("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("docker compose stop db db_test", self.recorded())
        self.assertIn("tmux kill-session -t =asistia", self.recorded())


if __name__ == "__main__":
    unittest.main()
