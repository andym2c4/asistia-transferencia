"""Acceso a la copia real local seleccionada explícitamente, sin contactar AWS.

check valida destino antes de arrancar la web; sql usa app o --owner para DDL.
La copia real nunca es una base asistia_test ni se usa para pytest.
"""

import argparse
import json
import os
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

from asistia.configuracion import entorno_local, leer_env

ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / "data/production/local-development.json"


def validated_url(owner=False):
    config = json.loads(MARKER.read_text())
    if config.get("state") != "VERIFIED_REAL_COPY":
        raise ValueError("La copia real local no está verificada; no iniciar la web.")
    value = (
        leer_env("data/production/local-owner.env")["ASISTIA_DATABASE_URL"]
        if owner
        else entorno_local()["ASISTIA_DATABASE_URL"]
    )
    parsed = urlparse(value)
    expected_user = "asistia_owner" if owner else "asistia_app"
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 5544
        or parsed.path != "/asistia"
        or parsed.username != expected_user
        or parsed.query
        or parsed.fragment
        or not parsed.password
    ):
        raise ValueError("La configuración no apunta a la copia real local esperada.")
    return parsed, config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "sql"])
    parser.add_argument(
        "--owner", action="store_true", help="Rol dueño para migraciones/DDL locales"
    )
    args = parser.parse_args()
    parsed, config = validated_url(args.owner)
    if args.action == "check":
        print(
            "Copia real local · 127.0.0.1:5544/asistia · "
            f"origen {config['source_backup']} · EC2 permanece en pausa."
        )
        return
    if not shutil.which("psql"):
        raise SystemExit("Falta el cliente psql.")
    env = {
        **os.environ,
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port),
        "PGDATABASE": "asistia",
        "PGUSER": unquote(parsed.username),
        "PGPASSWORD": unquote(parsed.password),
        "PGCONNECT_TIMEOUT": "5",
    }
    print(
        "DATOS REALES LOCALES · "
        + ("DUEÑO / DDL" if args.owner else "ESCRITURA OPERATIVA"),
        flush=True,
    )
    os.execvpe("psql", ["psql", "-X", "-v", "ON_ERROR_STOP=1"], env)


if __name__ == "__main__":
    main()
