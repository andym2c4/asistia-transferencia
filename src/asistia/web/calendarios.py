"""Aprobación de un calendario (feedback RRHH 2026-09-12): pasar una versión RECIBIDA/BORRADOR/
EN_REVISION a VIGENTE. Sin motivo obligatorio, decisión explícita del usuario y distinta del resto
de decisiones de revisión, que sí lo exigen. El núcleo ya degrada automáticamente cualquier otra
versión VIGENTE de la misma institución a HISTORICA (trigger fn_calendarizacion_version_auto_demote,
migración 0001); esta función no repite esa lógica.

Un calendario vacío en enero/febrero no bloquea la aprobación: las clases regularmente empiezan en
marzo, así que días sin actividad determinada en esos dos meses no son una observación pendiente de
resolver (decisión del usuario, 2026-09-12). Fuera de esos meses, sí bloquean.
"""

from __future__ import annotations

from . import ErrorDeTrabajo
from .lecturas import uno

ESTADOS_APROBABLES = ("RECIBIDA", "BORRADOR", "EN_REVISION")


def dias_bloqueantes(conn, cvid):
    return conn.execute(
        "SELECT count(*) AS n FROM dia_calendarizacion WHERE calendarizacion_version_id=%s "
        "AND estado_captura NOT IN ('REGISTRADO','NO_APLICA') AND extract(month FROM fecha) NOT IN (1,2)",
        (cvid,),
    ).fetchone()["n"]


def marcar_vigente(conn, cvid, uid):
    cv = uno(
        conn,
        "SELECT estado,procedencia_extraccion FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
        (cvid,),
    )
    if cv["procedencia_extraccion"].get("tipo") == "DERIVADO_2025":
        raise ErrorDeTrabajo(
            "Este calendario está basado en 2025. Carga una fuente de 2026 para aprobar el calendario del año.",
            409,
        )
    if cv["estado"] not in ESTADOS_APROBABLES:
        raise ErrorDeTrabajo(
            f"Este calendario está en estado {cv['estado']}, no se puede aprobar desde aquí.",
            409,
        )
    n = dias_bloqueantes(conn, cvid)
    if n:
        raise ErrorDeTrabajo(
            f"Hay {n} día(s) sin actividad determinada fuera de enero/febrero. Reclasifica el "
            "código o corrige la fuente antes de aprobar.",
            409,
        )
    conn.execute(
        "UPDATE calendarizacion_version SET estado='VIGENTE',aprobado_por=%s,aprobado_en=now() "
        "WHERE calendarizacion_version_id=%s",
        (uid, cvid),
    )
