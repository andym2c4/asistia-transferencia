"""Fixtures de P02: base de prueba aislada y efimera (docker-compose `db_test`, puerto 5545),
separada del volumen de `db` que conserva el avance manual del piloto (ver
docs/runbooks/PUNTO_DE_EJECUCION.md). Cada test recibe un esquema vacio salvo catalogos, que la
migracion 0001 ya siembra y estos tests nunca modifican.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DSN = os.environ.get(
    "ASISTIA_TEST_DATABASE_URL",
    "postgresql://asistia:asistia_test@localhost:5545/asistia_test",
)


@pytest.fixture(scope="session", autouse=True)
def _esquema_migrado() -> None:
    # Esta suite trunca tablas. Rechazar la BD operativa incluso si se configuró por error.
    info = psycopg.conninfo.conninfo_to_dict(TEST_DSN)
    if not info.get("dbname", "").startswith("asistia_test"):
        raise RuntimeError(
            "Las pruebas solo pueden usar una base cuyo nombre empiece por asistia_test"
        )
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "migrations" / "scripts" / "aplicar.py")],
        check=True,
        env={**os.environ, "ASISTIA_DATABASE_URL": TEST_DSN},
    )


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    with psycopg.connect(TEST_DSN, row_factory=dict_row, autocommit=False) as base:
        with base.cursor() as cur:
            cur.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename NOT LIKE 'catalogo\\_%' ESCAPE '\\'
                  AND tablename <> '_migraciones_aplicadas'
                """
            )
            tablas = [fila["tablename"] for fila in cur.fetchall()]
        if tablas:
            with base.cursor() as cur:
                cur.execute(f"TRUNCATE {', '.join(tablas)} RESTART IDENTITY CASCADE")
        base.commit()
        yield base
