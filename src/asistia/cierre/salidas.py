"""Consolidados técnicos por nivel, con decisiones y alternativas conservadas."""

import uuid
from collections import defaultdict
from datetime import date

from asistia.niveles import NIVELES
from asistia.web.lecturas import reportes_mes
from asistia.web.salidas import preparar

from .ingesta import SALIDA, escribir_json

REGLAS = [
    "BORRADOR de cierre: la revisión técnica no equivale a aprobación de RRHH ni a envío a la DRE.",
    "Una fila representa institución + persona + rol + vínculo + mes. Personas distintas no se suman entre niveles.",
    "Se utilizan las últimas versiones de cada serie. Para filas superpuestas: priorizar revisión humana, más días explícitos, menos ilegibles y extracción nativa; desempate estable por identificador.",
    "Las contradicciones entre fuentes se conservan en Selección de fuentes. La elección para este borrador es un supuesto técnico que requiere cotejo.",
    "Vacío, ilegible y desconocido no son presencia, falta ni cero. Las faltas se calculan cruzando la remuneración y el tipo de día de ambas fuentes; sin regla suficiente el total queda Pendiente.",
    "NEXUS es complementario; los reportes DRE solo aportan padrón nominal. No reconstruir fechas de faltas desde conteos.",
    "Un calendario derivado de 2025 conserva ese aviso y no determina días esperados oficiales de 2026.",
    "Incluye instituciones sin reporte en la hoja de revisión; no se les crean filas de asistencia ficticias.",
]


def elegir_filas(conn, reportes):
    if not reportes:
        return [], []
    grupos = defaultdict(list)
    por_reporte = {str(r["reporte_asistencia_id"]): r for r in reportes}
    filas = conn.execute(
        """SELECT t.*,r.institucion_educativa_id FROM trabajador_en_reporte t
      JOIN reporte_asistencia r USING(reporte_asistencia_id)
      WHERE r.reporte_asistencia_id=ANY(%s) AND t.trabajador_id IS NOT NULL AND t.rol_laboral_id IS NOT NULL""",
        ([r["reporte_asistencia_id"] for r in reportes],),
    ).fetchall()
    for fila in filas:
        dias = conn.execute(
            """SELECT fecha,estado_captura,estado_asistencia_id FROM asistencia_dia
          WHERE trabajador_en_reporte_id=%s ORDER BY fecha""",
            (fila["trabajador_en_reporte_id"],),
        ).fetchall()
        fila["dias"] = {
            str(d["fecha"]): (d["estado_captura"], d["estado_asistencia_id"])
            for d in dias
        }
        fila["explicitos"] = sum(
            d["estado_captura"] in ("REGISTRADO", "DERIVADO") for d in dias
        )
        fila["ilegibles"] = sum(
            d["estado_captura"] in ("ILEGIBLE", "PENDIENTE") for d in dias
        )
        grupos[
            (
                fila["institucion_educativa_id"],
                fila["trabajador_id"],
                fila["rol_laboral_id"],
                fila["vinculo_trabajador_ie_id"],
            )
        ].append(fila)
    elegidas, decisiones = [], []
    for clave, opciones in sorted(grupos.items(), key=lambda p: str(p[0])):

        def prioridad(f):
            r = por_reporte[str(f["reporte_asistencia_id"])]
            return (
                r["estado"] == "VALIDADO",
                f["explicitos"],
                -f["ilegibles"],
                r["procedencia_extraccion"].get("metodo") != "GEMINI",
                str(f["trabajador_en_reporte_id"]),
            )

        opciones.sort(key=prioridad, reverse=True)
        elegida = opciones[0]
        elegidas.append(str(elegida["trabajador_en_reporte_id"]))
        if len(opciones) == 1:
            continue
        fechas = set().union(*(f["dias"] for f in opciones))
        conflictos = sorted(
            d
            for d in fechas
            if len(
                {
                    f["dias"][d][1]
                    for f in opciones
                    if d in f["dias"] and f["dias"][d][0] in ("REGISTRADO", "DERIVADO")
                }
            )
            > 1
        )
        equivalentes = all(f["dias"] == elegida["dias"] for f in opciones[1:])
        decisiones.append(
            {
                "institucion_id": clave[0],
                "trabajador_id": clave[1],
                "elegida": str(elegida["trabajador_en_reporte_id"]),
                "alternativas": [
                    str(f["trabajador_en_reporte_id"]) for f in opciones[1:]
                ],
                "fechas_conflicto": conflictos,
                "motivo": "Fuentes diarias equivalentes; contar una sola vez."
                if equivalentes
                else REGLAS[2],
                "supuesto": not equivalentes,
                "autor": "AGENTE_TECNICO",
            }
        )
    return elegidas, decisiones


def alcance_nivel(conn, periodo, nivel):
    reportes = reportes_mes(conn, periodo, nivel)
    elegidas, decisiones = elegir_filas(conn, reportes)
    instituciones = conn.execute(
        """SELECT ie.cod_mod,ie.anexo,ie.nombre_ie,
      COALESCE(r.resultado,'PENDIENTE') resultado,COALESCE(r.detalle,'{}') detalle
      FROM institucion_educativa ie LEFT JOIN LATERAL (
        SELECT resultado,detalle FROM cierre_revision_institucion WHERE institucion_educativa_id=ie.institucion_educativa_id
        AND periodo=%s ORDER BY revisado_en DESC LIMIT 1) r ON true
      WHERE fn_nivel_canonico(ie.nivel_modalidad)=%s ORDER BY ie.cod_mod,ie.anexo""",
        (periodo, nivel),
    ).fetchall()
    return {
        "reglas": REGLAS,
        "filas_elegidas": elegidas,
        "decisiones": decisiones,
        "instituciones": instituciones,
    }


def generar_salidas(conn, periodo=date(2026, 7, 1)):
    """Se ejecuta bajo contexto Flask para reutilizar almacenamiento y manifiesto de la web."""
    uid = conn.execute(
        """SELECT usuario_id FROM usuario WHERE email='agente-tecnico@asistia.local' """
    ).fetchone()
    if uid:
        uid = uid["usuario_id"]
    else:
        uid = conn.execute("""INSERT INTO usuario(nombre,email,rol,activo)
          VALUES('Revisión técnica de cierre','agente-tecnico@asistia.local','SERVICIO',false) RETURNING usuario_id""").fetchone()[
            "usuario_id"
        ]
    conn.commit()
    resultados = []
    for nivel in NIVELES:
        alcance = alcance_nivel(conn, periodo, nivel)
        if not reportes_mes(conn, periodo, nivel):
            resultados.append(
                {
                    "nivel": nivel,
                    "estado": "SIN_REPORTE_DIARIO",
                    "instituciones": len(alcance["instituciones"]),
                }
            )
            continue
        operacion = uuid.uuid4()
        preparar(conn, periodo, nivel, False, operacion, uid, cierre_tecnico=alcance)
        conn.commit()
        salida = conn.execute(
            """SELECT w.web_salida_id,w.manifiesto->>'filas_detalle' AS filas,o.ruta_objeto,o.sha256
          FROM web_salida w JOIN objeto_archivo o USING(objeto_archivo_id) WHERE web_salida_id=%s""",
            (operacion,),
        ).fetchone()
        resultados.append({"nivel": nivel, "estado": "BORRADOR_TECNICO", **salida})
    escribir_json(SALIDA / f"salidas_{periodo:%Y-%m}.json", resultados)
    return resultados
