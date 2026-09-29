"""Revision local del consolidado (P05): hace revisable cada correccion/vinculo automatico con su
fuente, autor y motivo, y define cuando una salida puede declararse revisada sin asociarlo al
estado ENVIADO heredado -- ese estado sigue significando solo el tramite manual a la DRE
(docs/casos/TACTAMAL_JULIO_2026.md §11.4), no una revision de RRHH.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import psycopg


@dataclass
class AlertaConFuente:
    validacion_reporte_id: str
    codigo_regla: str
    severidad: str
    mensaje: str
    estado: str
    origen_vinculo: str | None
    motivo_vinculo: str | None
    confirmado_por: int | None
    confirmado_en: datetime | None
    resuelta_por: int | None
    resuelta_en: datetime | None
    motivo_resolucion: str | None


@dataclass
class ResultadoRevision:
    consolidado_dre_id: str
    revisable: bool
    pendientes_criticos: list[AlertaConFuente] = field(default_factory=list)


def listar_alertas_con_fuente(
    cur: psycopg.Cursor, consolidado_dre_id: str
) -> list[AlertaConFuente]:
    """Alertas (validacion_reporte) ligadas a los reportes efectivamente incluidos en este
    consolidado, con la fuente/autor/motivo del vinculo automatico que las origino cuando existe
    (Casos 1-3 de fn_resolver_vinculo_por_reporte, docs/decisions/2026-09-10-vigencia-vinculo-
    trabajador-institucion.md §13). El join por trabajador_en_reporte_id es exacto: esa funcion crea
    la validacion_reporte y la vinculo_trabajador_ie_confirmacion con el mismo id de origen."""
    cur.execute(
        """
        SELECT DISTINCT v.validacion_reporte_id, v.codigo_regla, v.severidad, v.mensaje, v.estado,
               c.origen, c.motivo, c.confirmado_por, c.confirmado_en,
               v.resuelta_por, v.resuelta_en, v.motivo_resolucion
        FROM validacion_reporte v
        JOIN consolidado_reporte_fuente f ON f.reporte_asistencia_id = v.reporte_asistencia_id
        LEFT JOIN vinculo_trabajador_ie_confirmacion c
            ON c.trabajador_en_reporte_id = v.trabajador_en_reporte_id
           AND c.origen = 'AUTOMATICO_IMPORTACION'
        WHERE f.consolidado_dre_id = %s
        ORDER BY v.validacion_reporte_id
        """,
        (consolidado_dre_id,),
    )
    return [
        AlertaConFuente(
            validacion_reporte_id=r["validacion_reporte_id"],
            codigo_regla=r["codigo_regla"],
            severidad=r["severidad"],
            mensaje=r["mensaje"],
            estado=r["estado"],
            origen_vinculo=r["origen"],
            motivo_vinculo=r["motivo"],
            confirmado_por=r["confirmado_por"],
            confirmado_en=r["confirmado_en"],
            resuelta_por=r["resuelta_por"],
            resuelta_en=r["resuelta_en"],
            motivo_resolucion=r["motivo_resolucion"],
        )
        for r in cur.fetchall()
    ]


def evaluar_revision(
    conn: psycopg.Connection, consolidado_dre_id: str
) -> ResultadoRevision:
    """Revision local: un borrador puede tener pendientes, pero no puede declararse "revisado" con
    discrepancias criticas (severidad='ERROR') aun PENDIENTE -- mismo umbral que ya usa el trigger
    heredado fn_reporte_asistencia_check_validacion para bloquear VALIDADO (ADVERTENCIA no bloquea
    a proposito, ver decision de vigencia §13.1). No escribe nada ni introduce un estado nuevo en
    consolidado_dre: evita atar la revision local al ENVIADO heredado, que sigue significando solo
    el tramite manual a la DRE."""
    with conn.cursor() as cur:
        alertas = listar_alertas_con_fuente(cur, consolidado_dre_id)
    criticos = [
        a for a in alertas if a.estado == "PENDIENTE" and a.severidad == "ERROR"
    ]
    return ResultadoRevision(
        consolidado_dre_id=consolidado_dre_id,
        revisable=not criticos,
        pendientes_criticos=criticos,
    )


def resolver_alerta(
    conn: psycopg.Connection,
    validacion_reporte_id: str,
    resuelta_por: int,
    motivo_resolucion: str | None = None,
    *,
    confirmar: bool = True,
) -> None:
    """Marca una alerta como revisada (RESUELTA), con el motivo de RRHH cuando lo da (opcional:
    aceptar una correccion automatica tal cual, sin mas accion, es una resolucion valida sin motivo
    adicional -- escenario 13.f de la decision de vigencia). No decide si la correccion automatica
    que la origino debe deshacerse -- eso es una vinculo_trabajador_ie_confirmacion nueva
    (CONFIRMA_CIERRE, append-only), fuera de esta funcion; ver escenario 13.e de esa decision."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE validacion_reporte
            SET estado = 'RESUELTA', resuelta_por = %s, resuelta_en = now(), motivo_resolucion = %s
            WHERE validacion_reporte_id = %s AND estado = 'PENDIENTE'
            """,
            (resuelta_por, motivo_resolucion, validacion_reporte_id),
        )
        if cur.rowcount == 0:
            raise ValueError(
                f"validacion_reporte {validacion_reporte_id} no existe o ya no esta PENDIENTE"
            )
    if confirmar:
        conn.commit()
