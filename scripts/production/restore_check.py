"""Restaurar un backup S3 en contenedor/archivos aislados y cotejar su contenido.

No sustituye la base operativa. Elimina solo el contenedor y directorio temporales
creados aquí después de una comprobación exitosa; conserva evidencia agregada.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from install import PG_IMAGE
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path("/var/lib/asistia")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resume", type=Path, help="Reusar un corte descargado que quedó pendiente"
    )
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads((PRIVATE / "deployment.json").read_text())
    if args.resume:
        target = args.resume.resolve()
        if (
            target.parent != PRIVATE
            or not target.name.startswith("restore-")
            or not target.is_dir()
        ):
            raise ValueError(
                "La reanudación requiere un directorio restore-* dentro del área privada"
            )
    else:
        target = PRIVATE / f"restore-{secrets.token_hex(5)}"
        target.mkdir()
    prefix = f"s3://{config['bucket']}/"

    def download(key, path):
        run("aws", "s3", "cp", prefix + key, str(path), "--only-show-errors")

    if not args.resume:
        download("backups/latest.json", target / "latest.json")
    backup = json.loads((target / "latest.json").read_text())
    for key, name in (
        ("database_key", "database.dump"),
        ("database_reference_key", "database.json"),
        ("file_manifest_key", "files.json"),
    ):
        if not (target / name).is_file():
            download(backup[key], target / name)
    assert digest(target / "database.dump") == backup["database_sha256"]
    files = json.loads((target / "files.json").read_text())
    copied = target / "files"
    copied.mkdir(exist_ok=True)
    if not args.resume:
        run(
            "aws",
            "s3",
            "sync",
            prefix + backup["file_prefix"],
            str(copied),
            "--only-show-errors",
        )
    for item in files:
        path = copied / item["path"]
        if not path.is_file() or digest(path) != item["sha256"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            run(
                "aws",
                "s3api",
                "get-object",
                "--bucket",
                config["bucket"],
                "--key",
                item["key"],
                "--version-id",
                item["version_id"],
                str(path),
                stdout=subprocess.DEVNULL,
            )
        assert digest(path) == item["sha256"], "Archivo de backup distinto"
    password = secrets.token_urlsafe(32)
    (target / "postgres.env").write_text(
        f"POSTGRES_USER=restore_admin\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB=asistia_restore_check\n"
    )
    container = f"asistia-restore-{secrets.token_hex(5)}"
    run(
        "docker",
        "run",
        "-d",
        "--name",
        container,
        "--publish",
        "127.0.0.1:5547:5432",
        "--env-file",
        str(target / "postgres.env"),
        "--tmpfs",
        "/var/lib/postgresql/data",
        PG_IMAGE,
        stdout=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "restore_admin",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("La base aislada no inició")
        with (target / "database.dump").open("rb") as source:
            run(
                "docker",
                "exec",
                "-i",
                container,
                "pg_restore",
                "-U",
                "restore_admin",
                "-d",
                "asistia_restore_check",
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                stdin=source,
            )
        spec = importlib.util.spec_from_file_location(
            "recuperacion", ROOT / "scripts/recuperacion.py"
        )
        recovery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recovery)
        with psycopg.connect(
            f"postgresql://restore_admin:{password}@127.0.0.1:5547/asistia_restore_check",
            row_factory=dict_row,
        ) as conn:
            assert (
                conn.execute("SELECT current_database() AS name").fetchone()["name"]
                == "asistia_restore_check"
            )
            restored = recovery.tablas(conn)
            expected = json.loads((target / "database.json").read_text())
            assert restored == expected, "Base restaurada distinta del corte del backup"
        result = {
            "verified_at": datetime.now(UTC).isoformat(),
            "backup": backup["created_at"],
            "database_tables": len(expected["tablas"]),
            "database_sequences": len(expected["secuencias"]),
            "database_identical": True,
            "files_verified": len(files),
            "files_from_s3": True,
            "production_replaced": False,
        }
        (PRIVATE / "restore-verification.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        print(json.dumps(result), flush=True)
    finally:
        run("docker", "rm", "-f", container, stdout=subprocess.DEVNULL)
    shutil.rmtree(target)


if __name__ == "__main__":
    main()
