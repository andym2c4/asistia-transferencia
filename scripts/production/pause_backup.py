"""Preparar una pausa autorizada: parar web, respaldar y conservar el corte en S3.

Ejecutar como root por SSM. No detiene EC2: el operador verifica este resultado
antes de pedir StopInstances. Ante un fallo deja la máquina encendida para reparar.
No cambia la habilitación de los servicios para el próximo arranque.
"""

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

PRIVATE = Path("/var/lib/asistia")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--retain-existing",
        action="store_true",
        help="Reanudar solo la retención tras un fallo, con la web ya detenida",
    )
    args = parser.parse_args()
    os.umask(0o077)
    started = datetime.now(UTC)
    config = json.loads((PRIVATE / "deployment.json").read_text())
    if args.retain_existing:
        state = subprocess.run(
            ["systemctl", "is-active", "asistia"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        assert state == "inactive", "La web debe haber permanecido detenida"
    run("systemctl", "stop", "asistia-backup.timer", "asistia")
    for _ in range(120):
        state = subprocess.run(
            ["systemctl", "is-active", "asistia-backup.service"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if state not in {"active", "activating", "deactivating"}:
            break
        time.sleep(5)
    else:
        raise RuntimeError("El respaldo anterior sigue activo; no detener EC2")
    if not args.retain_existing:
        run("systemctl", "start", "asistia-backup.service")
    folder = PRIVATE / "backup"
    backup = json.loads((folder / "latest.json").read_text())
    stamp = backup["created_at"]
    if not args.retain_existing:
        assert stamp >= started.strftime("%Y%m%dT%H%M%SZ"), (
            "Respaldo anterior a la pausa"
        )
    assert backup["success"]
    prefix = f"backups/paused/{stamp}"
    base = f"s3://{config['bucket']}/"
    checked = PRIVATE / "pause-verification"
    checked.mkdir(exist_ok=True)
    hashes = {}
    for field, name in (
        ("database_key", "database.dump"),
        ("database_reference_key", "database.json"),
        ("file_manifest_key", "files.json"),
    ):
        key = f"{prefix}/{name}"
        run(
            "aws",
            "s3",
            "cp",
            base + backup[field],
            base + key,
            "--copy-props",
            "none",
            "--only-show-errors",
        )
        run("aws", "s3", "cp", base + key, str(checked / name), "--only-show-errors")
        hashes[name] = sha256(checked / name)
        assert hashes[name] == sha256(folder / name), "Copia retenida distinta"
        backup[field] = key
    assert hashes["database.dump"] == backup["database_sha256"]
    run(
        "pg_restore",
        "--list",
        str(checked / "database.dump"),
        stdout=subprocess.DEVNULL,
    )
    # Las versiones bajo backups/files/ tampoco tienen caducidad en la plantilla.
    # Su manifiesto conserva el VersionId exacto de cada archivo y de .env.
    backup.update(
        paused_at=datetime.now(UTC).isoformat(),
        retention="manual; no automatic expiration under current bucket lifecycle",
        retained_manifest_key=f"{prefix}/latest.json",
        retained_artifact_hashes=hashes,
        web_stopped=True,
    )
    manifest = PRIVATE / "pause.json"
    manifest.write_text(json.dumps(backup, indent=2) + "\n")
    for key in (backup["retained_manifest_key"], "backups/paused/latest.json"):
        run("aws", "s3", "cp", str(manifest), base + key, "--only-show-errors")
    print(json.dumps(backup), flush=True)


if __name__ == "__main__":
    main()
