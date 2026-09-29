"""Actualizar solo código en EC2 con reversión; nunca restaura una base o data/.

Como root: update.py ARCHIVE EXPECTED_SHA256. Las migraciones nuevas se ejecutan
mediante un cambio explícito separado y respaldado; este comando las rechaza.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path("/data/projects/asistia")
PRIVATE = Path("/var/lib/asistia")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main(archive_name, expected):
    if not (PRIVATE / "initialized").exists():
        raise RuntimeError("Instalación inicial pendiente")
    archive_path = Path(archive_name)
    with archive_path.open("rb") as f:
        assert hashlib.file_digest(f, "sha256").hexdigest() == expected
    stage = Path(tempfile.mkdtemp(prefix="release-", dir=PRIVATE))
    with tarfile.open(archive_path) as archive:
        for item in archive.getmembers():
            if item.name == ".env" or item.name.startswith("data/"):
                raise RuntimeError("Un release no puede incluir datos ni secretos")
        archive.extractall(stage, filter="data")
    deployed = {p.name: p.read_bytes() for p in (ROOT / "migrations").glob("*.sql")}
    incoming = {p.name: p.read_bytes() for p in (stage / "migrations").glob("*.sql")}
    if incoming != deployed:
        raise RuntimeError(
            "Cambio de esquema: preparar y verificar migración antes de publicar código"
        )
    rollback = PRIVATE / "rollback-code"
    rollback.mkdir(exist_ok=True)
    names = ("src", "scripts", "pyproject.toml", "uv.lock")
    for name in names:
        dest = rollback / name
        if dest.is_dir():
            shutil.rmtree(dest)
        elif dest.exists():
            dest.unlink()
        if (ROOT / name).is_dir():
            shutil.copytree(ROOT / name, dest)
        else:
            shutil.copy2(ROOT / name, dest)
    run("systemctl", "stop", "asistia")

    def replace(source):
        for name in names:
            dest = ROOT / name
            if dest.is_dir():
                shutil.rmtree(dest)
                shutil.copytree(source / name, dest)
            else:
                shutil.copy2(source / name, dest)
            run("chown", "-R", "asistia:asistia", str(dest))
        run(
            "runuser",
            "-u",
            "asistia",
            "--",
            "/opt/asistia-tools/bin/uv",
            "sync",
            "--frozen",
            "--no-dev",
            cwd=ROOT,
        )
        run("systemctl", "start", "asistia")
        # Imports científicos en una CPU Standard ocupada pueden tardar más de
        # diez segundos. Esperar disponibilidad real sin reiniciar en un bucle.
        run(
            "curl",
            "--fail",
            "--silent",
            "--retry",
            "60",
            "--retry-connrefused",
            "--retry-delay",
            "2",
            "--max-time",
            "5",
            "http://127.0.0.1:8000/salud",
            stdout=subprocess.DEVNULL,
        )

    try:
        replace(stage)
        with urllib.request.urlopen(
            "http://127.0.0.1:8000/ingresar", timeout=10
        ) as response:
            assert response.status == 200
    except BaseException:
        run("systemctl", "stop", "asistia")
        replace(rollback)
        raise
    config_path = PRIVATE / "deployment.json"
    config = json.loads(config_path.read_text())
    config["release_sha256"] = expected
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    shutil.rmtree(stage)
    print(
        json.dumps({"release_sha256": expected, "health": "ok", "data_replaced": False})
    )


if __name__ == "__main__":
    os.umask(0o077)
    main(*sys.argv[1:])
