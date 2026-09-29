"""Recuperar un corte de pausa S3 para la copia real local, sin encender EC2.

Solo prepara y verifica entradas. No modifica la base ni activa archivos/.env.
Los archivos locales idénticos se reutilizan; las diferencias se descargan por
VersionId al área privada. Repetir conserva el mismo corte y revalida los hashes.
"""

import argparse
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_path(value):
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not (value == ".env" or value.startswith("data/"))
    ):
        raise ValueError("Ruta fuera del alcance de los archivos de producción")
    path = ROOT / relative
    if not path.resolve().is_relative_to(ROOT):
        raise ValueError("Ruta que escapa del proyecto")
    return path


def fetch(folder, bucket):
    if (ROOT / "data/production/local-development.json").exists():
        raise RuntimeError(
            "Copia real ya activa: no volver a importar sobre su trabajo."
        )
    os.umask(0o077)
    folder = folder.resolve()
    if folder.parent != ROOT / "data/production" or not folder.name.startswith(
        "local-copy-"
    ):
        raise ValueError("Usar data/production/local-copy-ID")
    folder.mkdir(exist_ok=True)

    def aws(*args):
        return subprocess.check_output(
            [
                "aws",
                "--profile",
                "personal",
                "--region",
                "us-east-1",
                *args,
                "--output",
                "json",
            ],
            text=True,
        )

    def download(key, path, version=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".partial")
        command = ["s3api", "get-object", "--bucket", bucket, "--key", key]
        if version:
            command += ["--version-id", version]
        aws(*command, str(temporary))
        temporary.replace(path)

    expected = json.loads((folder / "expected-source.json").read_text())
    download(expected["retained_manifest_key"], folder / "source.json")
    backup = json.loads((folder / "source.json").read_text())
    if backup != expected or not backup["success"] or not backup["web_stopped"]:
        raise RuntimeError("El corte remoto no coincide con la pausa seleccionada.")
    for field, name in (
        ("database_key", "database.dump"),
        ("database_reference_key", "database.json"),
        ("file_manifest_key", "files.json"),
    ):
        target = folder / name
        expected_hash = backup["retained_artifact_hashes"][name]
        if not target.is_file() or digest(target) != expected_hash:
            download(backup[field], target)
        if digest(target) != expected_hash:
            raise RuntimeError(f"Hash incorrecto: {name}")

    files = json.loads((folder / "files.json").read_text())
    if len(files) != backup["files"] or len({item["path"] for item in files}) != len(
        files
    ):
        raise RuntimeError("Manifiesto de archivos incompleto o con rutas repetidas")

    def prepare(item):
        path = source_path(item["path"])
        if path.is_file() and digest(path) == item["sha256"]:
            return "local_identical"
        target = folder / "files" / item["path"]
        if not target.is_file() or digest(target) != item["sha256"]:
            download(item["key"], target, item["version_id"])
        if digest(target) != item["sha256"]:
            raise RuntimeError("Archivo descargado distinto del manifiesto")
        return "downloaded_verified"

    with ThreadPoolExecutor(max_workers=6) as pool:
        states = list(pool.map(prepare, files))
    result = {
        "source_backup": backup["created_at"],
        "database_sha256": backup["database_sha256"],
        "release_sha256": backup["release_sha256"],
        "files_verified": len(files),
        "local_identical": states.count("local_identical"),
        "downloaded_verified": states.count("downloaded_verified"),
        "activated": False,
    }
    (folder / "fetch-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--bucket", required=True)
    args = parser.parse_args()
    fetch(args.folder, args.bucket)
