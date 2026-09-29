"""Dump consistente + archivos privados en S3. No propaga borrados de originales.

Retención de dumps definida en CloudFormation; versiones de objetos conservadas.
Se ejecuta como root por systemd, sin credenciales AWS estáticas.
"""

import hashlib
import importlib.util
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path("/var/lib/asistia")


def main():
    os.umask(0o077)
    config = json.loads((PRIVATE / "deployment.json").read_text())
    secret = json.loads((PRIVATE / "database.json").read_text())
    folder = PRIVATE / "backup"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dump = folder / "database.dump"
    prefix = f"s3://{config['bucket']}/backups"
    with psycopg.connect(
        f"postgresql://asistia_owner:{secret['owner']}@127.0.0.1:5432/asistia",
        row_factory=dict_row,
    ) as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SELECT pg_advisory_lock(26091206)")
        snapshot = conn.execute("SELECT pg_export_snapshot() AS id").fetchone()["id"]
        spec = importlib.util.spec_from_file_location(
            "recuperacion", ROOT / "scripts/recuperacion.py"
        )
        recovery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recovery)
        reference = folder / "database.json"
        reference.write_text(
            json.dumps(recovery.tablas(conn), default=str, indent=2) + "\n"
        )
        with dump.open("wb") as out:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "asistia-postgres",
                    "pg_dump",
                    "-U",
                    "asistia_owner",
                    "-d",
                    "asistia",
                    "-Fc",
                    "--no-owner",
                    "--no-privileges",
                    f"--snapshot={snapshot}",
                ],
                stdout=out,
                check=True,
            )
        with dump.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        # Dump anterior a la copia de archivos: todos los originales referenciados
        # deben estar presentes. El lock excluye escrituras de la aplicación.
        subprocess.run(
            [
                "aws",
                "s3",
                "sync",
                str(ROOT / "data"),
                f"{prefix}/files/data/",
                "--only-show-errors",
                "--exclude",
                "production/*",
                "--exclude",
                "backups/*",
                "--exclude",
                "recuperacion/*",
                "--exclude",
                "github_publicacion/*",
                "--exclude",
                "logs/*",
                "--exclude",
                "*chrome*/*",
                "--exclude",
                "*browser-*/*",
                "--exclude",
                "*/__pycache__/*",
                "--no-follow-symlinks",
            ],
            check=True,
        )
        subprocess.run(
            [
                "aws",
                "s3",
                "cp",
                str(ROOT / ".env"),
                f"{prefix}/files/.env",
                "--only-show-errors",
            ],
            check=True,
        )
        file_records = []
        paths = [ROOT / ".env"]
        for base, dirs, names in os.walk(ROOT / "data", followlinks=False):
            parent = Path(base)
            dirs[:] = [
                d
                for d in dirs
                if d not in {"__pycache__", "chrome"}
                and not d.startswith(("chrome-", "browser-"))
                and not (
                    parent == ROOT / "data"
                    and d
                    in {
                        "production",
                        "backups",
                        "recuperacion",
                        "github_publicacion",
                        "logs",
                    }
                )
            ]
            paths.extend(
                parent / name
                for name in names
                if (parent / name).is_file() and not (parent / name).is_symlink()
            )
        versions = json.loads(
            subprocess.check_output(
                [
                    "aws",
                    "s3api",
                    "list-object-versions",
                    "--bucket",
                    config["bucket"],
                    "--prefix",
                    "backups/files/",
                    "--output",
                    "json",
                ]
            )
        )
        latest_versions = {
            item["Key"]: item["VersionId"]
            for item in versions.get("Versions", [])
            if item["IsLatest"]
        }
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            with path.open("rb") as source:
                sha = hashlib.file_digest(source, "sha256").hexdigest()
            key = f"backups/files/{relative}"
            file_records.append(
                {
                    "path": relative,
                    "sha256": sha,
                    "version_id": latest_versions[key],
                    "key": key,
                }
            )
        files_manifest = folder / "files.json"
        files_manifest.write_text(json.dumps(file_records, ensure_ascii=False) + "\n")
        for path in (reference, files_manifest):
            subprocess.run(
                [
                    "aws",
                    "s3",
                    "cp",
                    str(path),
                    f"{prefix}/db/{stamp}/{path.name}",
                    "--only-show-errors",
                ],
                check=True,
            )
        # El manifiesto de éxito se publica únicamente después de todos los objetos.
        subprocess.run(
            [
                "aws",
                "s3",
                "cp",
                str(dump),
                f"{prefix}/db/{stamp}/database.dump",
                "--only-show-errors",
            ],
            check=True,
        )
        result = {
            "created_at": stamp,
            "database_sha256": digest,
            "database_bytes": dump.stat().st_size,
            "database_key": f"backups/db/{stamp}/database.dump",
            "file_prefix": "backups/files/",
            "file_manifest_key": f"backups/db/{stamp}/files.json",
            "database_reference_key": f"backups/db/{stamp}/database.json",
            "files": len(file_records),
            "release_sha256": config["release_sha256"],
            "success": True,
        }
        manifest = folder / "latest.json"
        manifest.write_text(json.dumps(result, indent=2) + "\n")
        subprocess.run(
            [
                "aws",
                "s3",
                "cp",
                str(manifest),
                f"{prefix}/latest.json",
                "--only-show-errors",
            ],
            check=True,
        )
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
