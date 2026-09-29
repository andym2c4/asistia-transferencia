"""Captura inicial privada; código separado de base/archivos/configuración.

No arranca la web, no modifica tablas y rechaza una captura con la web local viva.
Los manifiestos privados contienen nombres de archivos: nunca se versionan.
"""

import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from asistia.db import dsn

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {"backups", "recuperacion", "production", "github_publicacion", "logs"}


def main():
    os.umask(0o077)
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 8000)) == 0:
            raise RuntimeError(
                "Detener la web local antes de capturar y cortar escrituras."
            )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = ROOT / "data/production" / f"capture-{stamp}"
    target.mkdir(parents=True)
    database_command = ["docker", "compose"]
    database_service = "db"
    database_owner = "asistia"
    if (ROOT / "data/production/local-development.json").exists():
        subprocess.run(
            [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/local.py"), "check"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        database_command += ["-f", str(ROOT / "docker-compose.local.yml")]
        database_service = "db_local"
        database_owner = "asistia_owner"
    spec = importlib.util.spec_from_file_location(
        "recuperacion", ROOT / "scripts/recuperacion.py"
    )
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if not conn.execute(
            "SELECT pg_try_advisory_lock(26091206) AS locked"
        ).fetchone()["locked"]:
            raise RuntimeError("Hay una escritura operativa en curso.")
        snapshot = conn.execute("SELECT pg_export_snapshot() AS id").fetchone()["id"]
        baseline = recovery.tablas(conn)
        (target / "database.json").write_text(
            json.dumps(baseline, default=str, indent=2) + "\n"
        )
        with (target / "database.dump").open("wb") as dump:
            subprocess.run(
                database_command
                + [
                    "exec",
                    "-T",
                    database_service,
                    "pg_dump",
                    "-U",
                    database_owner,
                    "-d",
                    "asistia",
                    "-Fc",
                    "--no-owner",
                    f"--snapshot={snapshot}",
                ],
                cwd=ROOT,
                stdout=dump,
                check=True,
            )
        files = [ROOT / ".env"]
        for base, dirs, names in os.walk(ROOT / "data", followlinks=False):
            current = Path(base)
            dirs[:] = sorted(
                d
                for d in dirs
                if d
                not in {
                    ".venv",
                    "__pycache__",
                    "node_modules",
                    ".git",
                    ".pytest_cache",
                    "chrome",
                }
                and not d.startswith(("chrome-", "browser-"))
                and not (current == ROOT / "data" and d in EXCLUDED)
            )
            for name in sorted(names):
                path = current / name
                if path.is_symlink():
                    raise RuntimeError("Resolver enlaces de datos antes de empaquetar.")
                if path.is_file():
                    files.append(path)
        manifest = []
        with tarfile.open(target / "data.tar.gz", "w:gz", compresslevel=1) as archive:
            for p in files:
                if not p.exists():
                    continue
                relative = p.relative_to(ROOT).as_posix()
                with p.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                manifest.append(
                    {"path": relative, "size": p.stat().st_size, "sha256": digest}
                )
                archive.add(p, arcname=relative, recursive=False)
        (target / "files.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    code = {ROOT / p for p in tracked if p and not p.startswith("data/")}
    code.update((ROOT / "scripts/production").glob("*"))
    with tarfile.open(target / "code.tar.gz", "w:gz", compresslevel=1) as archive:
        for p in sorted(code):
            if p.is_file() and not p.is_symlink():
                archive.add(p, arcname=p.relative_to(ROOT).as_posix(), recursive=False)
    artifacts = {}
    for p in sorted(target.iterdir()):
        with p.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        artifacts[p.name] = {"sha256": digest, "bytes": p.stat().st_size}
    meta = {
        "captured_at": stamp,
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
        .decode()
        .strip(),
        "root": str(ROOT),
        "files": len(manifest),
        "bytes": sum(x["size"] for x in manifest),
        "artifacts": artifacts,
        "note": "Working tree content, including local changes; never upload as public artifact",
    }
    (target / "capture.json").write_text(json.dumps(meta, indent=2) + "\n")
    (ROOT / "data/production/current-capture").write_text(str(target) + "\n")
    print(
        json.dumps(
            {
                "capture": str(target),
                "files": meta["files"],
                "bytes": meta["bytes"],
                "archive_bytes": artifacts["data.tar.gz"]["bytes"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
