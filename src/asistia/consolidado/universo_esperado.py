"""Universo esperado del mes: integra calendario aplicable + vigencia de vinculos
(fn_vinculo_presencia_esperada, migrations/0003) para saber, por vinculo y periodo, cuantos dias
lectivos/de gestion se esperaba presencia, y para exponer -- sin agregarlas silenciosamente al
consolidado -- las personas esperadas que no tienen reporte.

Alcance (P04, docs/exec-plans/active/2026-09-11-consolidacion-verificable.md): construye la
poblacion esperada desde vinculos vigentes y el calendario aplicable de cada institucion. No
resuelve el universo completo de M04 (eso exige un manifiesto de unidades esperadas validado
contra el padron/obligacion de reporte, que no existe todavia -- ver docs/METRICAS.md M04) ni
decide `corresponde_descuento` (decision humana, columna con CHECK que lo exige).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import psycopg
from psycopg.types.json import Jsonb


@dataclass
class DiasEsperados:
    lectivos: int | None
    gestion: int | None
    fuente_calculo: dict


@dataclass
class VinculoSinReporte:
    vinculo_trabajador_ie_id: int
    trabajador_id: int
    institucion_educativa_id: int
    dias_lectivos_esperados: int | None
    dias_gestion_esperados: int | None


@dataclass
class ResultadoPoblacionEsperada:
    instituciones_evaluadas: int = 0
    instituciones_sin_calendario: list[int] = field(default_factory=list)
    vinculos_esperados: int = 0
    vinculos_con_reporte: int = 0
    vinculos_sin_reporte: list[VinculoSinReporte] = field(default_factory=list)
    # vinculos sin reporte pero con 0 dias lectivos/gestion esperados este periodo (cubiertos por
    # reemplazo o encargatura en otra plaza todo el mes, o sin calendario): no son un hueco real,
    # pero deben quedar contados para que vinculos_esperados reconcilie exacto contra sus partes.
    vinculos_sin_reporte_ni_dias_esperados: int = 0
    error: str | None = None

    def reconciliado(self) -> bool:
        return self.vinculos_esperados == (
            self.vinculos_con_reporte
            + len(self.vinculos_sin_reporte)
            + self.vinculos_sin_reporte_ni_dias_esperados
        )


def _rango_periodo(periodo: date) -> tuple[date, date]:
    inicio = date(periodo.year, periodo.month, 1)
    fin = date(periodo.year + (periodo.month == 12), periodo.month % 12 + 1, 1)
    return inicio, fin


def calendario_aplicable(
    cur: psycopg.Cursor, institucion_educativa_id: int, anio: int
) -> tuple[str, str] | tuple[None, None]:
    """(calendarizacion_version_id, estado) del calendario a usar para esta institucion/anio:
    prefiere VIGENTE; a falta de una version aprobada, usa la mas reciente (BORRADOR incluido,
    con su estado visible en el resultado, nunca silenciado como si fuera VIGENTE). (None, None)
    si la institucion no tiene ningun calendario importado ese anio -- "calendario desconocido"."""
    cur.execute(
        """
        SELECT cv.calendarizacion_version_id, cv.estado
        FROM calendarizacion_version cv
        JOIN calendarizacion_local cl ON cl.calendarizacion_local_id = cv.calendarizacion_local_id
        WHERE cl.institucion_educativa_id = %s AND cl.anio = %s
          AND cv.estado NOT IN ('RECHAZADA','HISTORICA')
        ORDER BY (cv.procedencia_extraccion->>'tipo' = 'DERIVADO_2025') ASC NULLS FIRST,
                 (cv.estado = 'VIGENTE') DESC, cv.version DESC
        LIMIT 1
        """,
        (institucion_educativa_id, anio),
    )
    fila = cur.fetchone()
    if fila is None:
        return None, None
    return fila["calendarizacion_version_id"], fila["estado"]


def dias_esperados_de_vinculo(
    cur: psycopg.Cursor, vinculo_trabajador_ie_id: int, periodo: date
) -> DiasEsperados:
    """Dias lectivos/de gestion del `periodo` en los que se espera presencia de este vinculo,
    aplicando fn_vinculo_presencia_esperada dia por dia (cubre reemplazo a mitad de mes,
    encargatura en otra plaza, DESTACADO por defecto y vinculos concurrentes -- todo ya resuelto
    por esa funcion, ver docs/decisions/2026-09-10-nomina-esperada-encargaturas-reemplazos.md).
    NULL en ambos campos si la institucion no tiene calendario importado ese anio (calendario
    desconocido): no se inventa un supuesto de dias habiles."""
    cur.execute(
        "SELECT institucion_educativa_id FROM vinculo_trabajador_ie WHERE vinculo_trabajador_ie_id = %s",
        (vinculo_trabajador_ie_id,),
    )
    fila = cur.fetchone()
    if fila is None:
        return DiasEsperados(None, None, {"motivo": "vinculo_no_encontrado"})

    calendarizacion_version_id, estado = calendario_aplicable(
        cur, fila["institucion_educativa_id"], periodo.year
    )
    if calendarizacion_version_id is None:
        return DiasEsperados(
            None, None, {"motivo": "calendario_desconocido", "anio": periodo.year}
        )

    inicio, fin = _rango_periodo(periodo)
    cur.execute(
        """
        WITH fechas AS (
            SELECT %(inicio)s::date + n AS fecha
            FROM generate_series(0, %(fin)s::date - %(inicio)s::date - 1) AS n
        ), programacion AS (
            SELECT f.fecha,ct.codigo_interno,
                   fn_vinculo_presencia_esperada(%(vid)s, f.fecha) AS vinculo_esperado
            FROM fechas f
            LEFT JOIN dia_calendarizacion dc ON dc.fecha=f.fecha
                AND dc.calendarizacion_version_id=%(cvid)s
            LEFT JOIN catalogo_tipo_dia ct ON ct.tipo_dia_id=dc.tipo_dia_id
        )
        SELECT
            count(*) FILTER (
                WHERE codigo_interno = 'LECTIVO' AND vinculo_esperado
            ) AS lectivos,
            count(*) FILTER (
                WHERE codigo_interno = 'GESTION' AND vinculo_esperado
            ) AS gestion,
            array_agg(fecha ORDER BY fecha) FILTER (
                WHERE codigo_interno IS NULL AND vinculo_esperado
            ) AS sin_resolver
        FROM programacion
        """,
        {
            "vid": vinculo_trabajador_ie_id,
            "cvid": calendarizacion_version_id,
            "inicio": inicio,
            "fin": fin,
        },
    )
    conteo = cur.fetchone()
    fuente = {
        "calendarizacion_version_id": str(calendarizacion_version_id),
        "calendarizacion_version_estado": estado,
        "regla_dias_esperados": "P06.2_calendario_completo_por_vigencia",
    }
    cur.execute(
        "SELECT procedencia_extraccion FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
        (calendarizacion_version_id,),
    )
    procedencia = cur.fetchone()["procedencia_extraccion"]
    fuente["procedencia_calendario"] = procedencia
    if procedencia.get("tipo") == "DERIVADO_2025":
        return DiasEsperados(
            None,
            None,
            {
                **fuente,
                "motivo": "calendario_estimado_desde_2025",
                "lectivos_estimados_2025": conteo["lectivos"],
                "gestion_estimada_2025": conteo["gestion"],
                "fechas_sin_resolver": [str(f) for f in (conteo["sin_resolver"] or [])],
            },
        )
    if conteo["sin_resolver"]:
        return DiasEsperados(
            None,
            None,
            {
                **fuente,
                "motivo": "calendario_incompleto",
                "fechas_calendarizacion_sin_resolver": [
                    str(f) for f in conteo["sin_resolver"]
                ],
                "lectivos_identificados_parcial": conteo["lectivos"],
                "gestion_identificados_parcial": conteo["gestion"],
            },
        )
    return DiasEsperados(
        lectivos=conteo["lectivos"],
        gestion=conteo["gestion"],
        fuente_calculo=fuente,
    )


def aplicar_dias_esperados_a_detalle(
    conn: psycopg.Connection,
    consolidado_dre_id: str,
    periodo: date,
    *,
    confirmar: bool = True,
) -> None:
    """Actualiza dias_lectivos_esperados/dias_gestion_esperados/fuente_calculo de cada
    consolidado_dre_detalle de este consolidado, usando su vinculo_trabajador_ie_id (cuando fue
    resuelto). No toca corresponde_descuento -- esa columna exige decidido_por/en/motivo (decision
    humana), nunca se completa por este calculo."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT consolidado_dre_detalle_id, vinculo_trabajador_ie_id
            FROM consolidado_dre_detalle
            WHERE consolidado_dre_id = %s AND vinculo_trabajador_ie_id IS NOT NULL
            """,
            (consolidado_dre_id,),
        )
        detalles = cur.fetchall()

        for detalle in detalles:
            dias = dias_esperados_de_vinculo(
                cur, detalle["vinculo_trabajador_ie_id"], periodo
            )
            # `||` fusiona con lo que _congelar_clasificacion_faltas (generar.py, P05) ya haya
            # escrito en esta misma columna -- nunca lo pisa.
            cur.execute(
                """
                UPDATE consolidado_dre_detalle
                SET dias_lectivos_esperados = %s, dias_gestion_esperados = %s,
                    fuente_calculo = fuente_calculo || %s
                WHERE consolidado_dre_detalle_id = %s
                """,
                (
                    dias.lectivos,
                    dias.gestion,
                    Jsonb(dias.fuente_calculo),
                    detalle["consolidado_dre_detalle_id"],
                ),
            )
    if confirmar:
        conn.commit()


def calcular_poblacion_esperada(
    conn: psycopg.Connection, periodo: date, nivel_modalidad: str
) -> ResultadoPoblacionEsperada:
    """Poblacion esperada del (periodo, nivel_modalidad): vinculos vigentes de instituciones de ese
    nivel, con dias esperados por calendario aplicable, separando quienes tienen reporte de quienes
    no (hueco, expuesto explicitamente -- nunca agregado como si fuera un trabajador reportado).
    No filtra por `anexo`: la institucion se identifica por institucion_educativa_id, valido para
    anexo='0' o no."""
    resultado = ResultadoPoblacionEsperada()
    periodo = date(periodo.year, periodo.month, 1)
    inicio, fin = _rango_periodo(periodo)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT institucion_educativa_id FROM institucion_educativa WHERE "
            "fn_nivel_canonico(nivel_modalidad) = fn_nivel_canonico(%s) OR nivel_modalidad = %s",
            (nivel_modalidad, nivel_modalidad),
        )
        instituciones = [f["institucion_educativa_id"] for f in cur.fetchall()]
        resultado.instituciones_evaluadas = len(instituciones)
        if not instituciones:
            resultado.error = (
                f"no hay instituciones con nivel_modalidad={nivel_modalidad!r}"
            )
            return resultado

        for institucion_educativa_id in instituciones:
            calendarizacion_version_id, _ = calendario_aplicable(
                cur, institucion_educativa_id, periodo.year
            )
            if calendarizacion_version_id is None:
                resultado.instituciones_sin_calendario.append(institucion_educativa_id)

            cur.execute(
                """
                SELECT vinculo_trabajador_ie_id, trabajador_id
                FROM vinculo_trabajador_ie
                WHERE institucion_educativa_id = %s AND trabajador_id IS NOT NULL
                  AND (fecha_inicio IS NULL OR fecha_inicio < %s)
                  AND (fecha_fin IS NULL OR fecha_fin >= %s)
                """,
                (institucion_educativa_id, fin, inicio),
            )
            vinculos = cur.fetchall()

            for v in vinculos:
                vinculo_id = v["vinculo_trabajador_ie_id"]
                resultado.vinculos_esperados += 1

                # Directo contra trabajador_en_reporte/reporte_asistencia (no contra
                # consolidado_dre_detalle): asi la poblacion esperada se puede calcular aunque
                # todavia no se haya generado el consolidado de este periodo, y no confunde un
                # reporte de otro mes con el de este.
                cur.execute(
                    """
                    SELECT 1 FROM trabajador_en_reporte ter
                    JOIN reporte_asistencia ra ON ra.reporte_asistencia_id = ter.reporte_asistencia_id
                    WHERE ter.vinculo_trabajador_ie_id = %s AND ra.periodo = %s
                      AND ra.estado NOT IN ('RECHAZADO','HISTORICA')
                    """,
                    (vinculo_id, periodo),
                )
                tiene_reporte = cur.fetchone() is not None

                if tiene_reporte:
                    resultado.vinculos_con_reporte += 1
                    continue

                dias = dias_esperados_de_vinculo(cur, vinculo_id, periodo)
                if dias.lectivos == 0 and dias.gestion == 0:
                    # ninguna presencia esperada este periodo para este vinculo (cubierto por
                    # reemplazo o encargatura en otra plaza todo el mes) -- no es un hueco real,
                    # pero se cuenta para que vinculos_esperados reconcilie contra sus partes.
                    resultado.vinculos_sin_reporte_ni_dias_esperados += 1
                    continue
                resultado.vinculos_sin_reporte.append(
                    VinculoSinReporte(
                        vinculo_trabajador_ie_id=vinculo_id,
                        trabajador_id=v["trabajador_id"],
                        institucion_educativa_id=institucion_educativa_id,
                        dias_lectivos_esperados=dias.lectivos,
                        dias_gestion_esperados=dias.gestion,
                    )
                )

    return resultado
