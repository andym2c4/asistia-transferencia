"""Conexion a Postgres y helpers de procedencia (hash de archivo, documento_recibido)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from asistia.configuracion import entorno_local


def dsn() -> str:
    return entorno_local().get(
        "ASISTIA_DATABASE_URL",
        "postgresql://asistia:asistia_dev@localhost:5544/asistia",
    )


@contextmanager
def conectar() -> Iterator[psycopg.Connection]:
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        yield conn


def sha256_de(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def registrar_documento(
    cur: psycopg.Cursor,
    ruta: Path,
    mime_type: str,
    conservar_en: Path | None = None,
    recibido_en: datetime | None = None,
) -> str:
    """Registra (o reutiliza) el objeto_archivo por hash y crea un documento_recibido.

    Idempotente por contenido (mismo criterio que ADR-021 de v1): un archivo byte-identico ya
    presente en objeto_archivo se reutiliza; nunca se reprocesa el hash.

    `conservar_en` (DT06): raiz de un almacen por contenido al que copiar el original antes de
    registrarlo, de modo que `ruta_objeto` apunte a una copia propia y no a la ubicacion prestada
    desde la que se importo. Sin este parametro se conserva el comportamiento anterior -- se guarda
    la ruta de origen tal cual -- que es suficiente cuando el archivo vive en un directorio estable,
    pero deja de serlo en cuanto se importa desde un temporal: en el piloto, un consolidado quedo
    apuntando a un /tmp de otra sesion y su original ya no se puede abrir. `asistia originales
    verificar` detecta esos casos.

    `recibido_en` (DT06): instante real de recepcion del documento. Si no se indica, la columna
    aplica su `now()` por defecto, que es **fecha de carga, no fecha de recepcion en mesa de
    partes** (docs/CLAUDE.md). Pasarlo explicitamente es la unica forma de que esas dos fechas no se
    confundan cuando se importa un documento recibido dias antes.
    """
    if conservar_en is not None:
        from asistia.originales import conservar_original

        ruta = conservar_original(ruta, conservar_en)
    sha256 = sha256_de(ruta)
    cur.execute(
        "SELECT objeto_archivo_id FROM objeto_archivo WHERE sha256 = %s", (sha256,)
    )
    fila = cur.fetchone()
    if fila:
        objeto_archivo_id = fila["objeto_archivo_id"]
    else:
        cur.execute(
            """
            INSERT INTO objeto_archivo (sha256, ruta_objeto, mime_type, tamano_bytes)
            VALUES (%s, %s, %s, %s)
            RETURNING objeto_archivo_id
            """,
            (sha256, str(ruta), mime_type, ruta.stat().st_size),
        )
        objeto_archivo_id = cur.fetchone()["objeto_archivo_id"]

    if recibido_en is None:
        cur.execute(
            """
            INSERT INTO documento_recibido (objeto_archivo_id, nombre_original)
            VALUES (%s, %s)
            RETURNING documento_recibido_id
            """,
            (objeto_archivo_id, ruta.name),
        )
    else:
        cur.execute(
            """
            INSERT INTO documento_recibido (objeto_archivo_id, nombre_original, recibido_en)
            VALUES (%s, %s, %s)
            RETURNING documento_recibido_id
            """,
            (objeto_archivo_id, ruta.name, recibido_en),
        )
    return cur.fetchone()["documento_recibido_id"]
