"""Conexiones por petición y exclusión de escrituras del recorrido web."""

from contextlib import contextmanager

import psycopg
from flask import current_app, g
from psycopg.rows import dict_row

from . import ErrorDeTrabajo

WRITE_LOCK = 26091206


def base():
    if "db" not in g:
        g.db = psycopg.connect(
            current_app.config["DATABASE_URL"], row_factory=dict_row, connect_timeout=5
        )
        g.db.execute("SET TIME ZONE 'America/Lima'")
        g.db.commit()
    return g.db


@contextmanager
def escritura():
    """Lock de sesión: se mantiene aunque un importador confirme cada fila.

    El lote de cierre utiliza la misma exclusión. Una importación parcial conserva su
    progreso; las otras operaciones confirman un único bloque completo.
    """
    conn = base()
    conn.rollback()
    tomado = conn.execute(
        "SELECT pg_try_advisory_lock(%s) AS ok", (WRITE_LOCK,)
    ).fetchone()["ok"]
    if not tomado:
        conn.rollback()
        raise ErrorDeTrabajo(
            "Hay otra operación en curso. Espera su resultado y vuelve a intentar.", 409
        )
    try:
        conn.execute(
            "SELECT set_config('app.usuario_id', %s, false)",
            (str(g.usuario["usuario_id"]),),
        )
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        if not conn.closed:
            conn.rollback()
            conn.execute("SELECT pg_advisory_unlock(%s)", (WRITE_LOCK,))
            conn.commit()
