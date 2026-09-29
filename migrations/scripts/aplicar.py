"""Runner minimo de migraciones: aplica los .sql de migrations/ en orden numerico,
registrando cuales ya corrieron en una tabla _migraciones_aplicadas."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent


def dsn() -> str:
    return os.environ.get(
        "ASISTIA_DATABASE_URL",
        "postgresql://asistia:asistia_dev@localhost:5544/asistia",
    )


def migraciones_disponibles() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def aplicar() -> None:
    archivos = migraciones_disponibles()
    if not archivos:
        print("No hay migraciones en", MIGRATIONS_DIR)
        return

    with psycopg.connect(dsn(), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS _migraciones_aplicadas (
                    nombre text PRIMARY KEY,
                    aplicada_en timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            conn.commit()

        for archivo in archivos:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM _migraciones_aplicadas WHERE nombre = %s", (archivo.name,)
                )
                if cur.fetchone():
                    print(f"  ya aplicada: {archivo.name}")
                    continue

            sql = archivo.read_text(encoding="utf-8")
            print(f"  aplicando: {archivo.name}")
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO _migraciones_aplicadas (nombre) VALUES (%s)", (archivo.name,)
                )
            conn.commit()

    print("Migraciones al dia.")


if __name__ == "__main__":
    sys.exit(aplicar())
