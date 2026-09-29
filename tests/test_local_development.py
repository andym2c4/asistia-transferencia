"""Guardia de destino local; sin conectar a ninguna base real."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "asistia_local", Path(__file__).resolve().parents[1] / "scripts/local.py"
)
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)


class LocalDestinationTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.marker = Path(folder.name) / "local.json"
        self.marker.write_text(
            json.dumps({"state": "VERIFIED_REAL_COPY", "source_backup": "test"})
        )
        patcher = patch.object(local, "MARKER", self.marker)
        patcher.start()
        self.addCleanup(patcher.stop)

    def check_url(self, url):
        with patch.object(
            local, "entorno_local", return_value={"ASISTIA_DATABASE_URL": url}
        ):
            return local.validated_url()

    def test_exact_local_destination(self):
        parsed, _ = self.check_url(
            "postgresql://asistia_app:local@127.0.0.1:5544/asistia"
        )
        self.assertEqual(parsed.hostname, "127.0.0.1")

    def test_reject_remote_test_and_connection_overrides(self):
        for url in (
            "postgresql://asistia_app:local@127.0.0.1:5546/asistia",
            "postgresql://asistia_app:local@127.0.0.1:5545/asistia_test",
            "postgresql://asistia_app:local@example.invalid:5544/asistia",
            "postgresql://asistia_app:local@127.0.0.1:5544/asistia?host=example.invalid",
            "postgresql://asistia_owner:local@127.0.0.1:5544/asistia",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.check_url(url)

    def test_unverified_copy_is_rejected(self):
        self.marker.write_text('{"state":"RESTORING"}')
        with self.assertRaises(ValueError):
            self.check_url("postgresql://asistia_app:local@127.0.0.1:5544/asistia")


if __name__ == "__main__":
    unittest.main()
