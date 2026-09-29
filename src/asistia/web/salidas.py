"""Preparar una versión visible solo cuando cálculo, fichero y manifiesto están completos."""

from __future__ import annotations

import uuid
from pathlib import Path

from flask import current_app
from psycopg.types.json import Jsonb

from asistia.coherencia import revisar_reportes
from asistia.consolidado import generar as modulo_generar
from asistia.consolidado import universo_esperado as modulo_universo
from asistia.consolidado.generar import (
    _CLASIFICACION_FALTA,
    exportar_consolidado_xlsx,
    generar_consolidado,
)
from asistia.consolidado.revision import evaluar_revision
from asistia.db import sha256_de

from . import ErrorDeTrabajo
from .archivos import ruta_verificada
from .lecturas import (
    calendarios_actuales,
    huella_contexto_calculo,
    huella_reporte,
    reportes_mes,
    uno,
)
from .revision import json_datos


def preparar(conn, periodo, nivel, revisado, operacion, uid, *, cierre_tecnico=None):
    existente = conn.execute(
        "SELECT w.*,c.periodo,c.nivel_modalidad FROM web_salida w JOIN consolidado_dre c USING(consolidado_dre_id) WHERE web_salida_id=%s",
        (operacion,),
    ).fetchone()
    estado = "REVISADO" if revisado else "BORRADOR"
    if existente:
        if (
            existente["periodo"],
            existente["nivel_modalidad"],
            existente["estado_revision"],
        ) != (periodo, nivel, estado):
            raise ErrorDeTrabajo(
                "Esa solicitud ya preparó otra salida. Abre de nuevo el formulario para una nueva versión.",
                409,
            )
        return operacion
    reportes = reportes_mes(conn, periodo, nivel)
    if cierre_tecnico is not None and revisado:
        raise ErrorDeTrabajo(
            "La selección técnica produce un borrador; requiere revisión de RRHH.", 409
        )
    if not reportes:
        raise ErrorDeTrabajo(
            "Aún no hay reportes para este mes y nivel. Carga los documentos para preparar una salida.",
            409,
        )
    if revisado and any(
        r["estado"] != "VALIDADO" or r["criticos"] or r["sin_identidad"]
        for r in reportes
    ):
        raise ErrorDeTrabajo(
            "La versión revisada requiere cotejar cada reporte incluido y resolver sus identidades y observaciones críticas. Puedes generar un borrador con los pendientes visibles.",
            409,
        )
    coherencias = revisar_reportes(conn, reportes)
    if revisado and any(not c["listo"] for c in coherencias.values()):
        raise ErrorDeTrabajo(
            "Falta revisar la coherencia entre reportes y calendarios: hay casos pendientes o cruces sin información suficiente. Abre Coherencia en los reportes; puedes conservar un borrador con sus observaciones.",
            409,
        )
    # Evita sumar dos series que difieran solo en la grafía del nivel para una misma IE/turno.
    claves = [(r["institucion_educativa_id"], r["turno"]) for r in reportes]
    if len(set(claves)) != len(claves) and cierre_tecnico is None:
        raise ErrorDeTrabajo(
            "Hay series duplicadas para una institución y turno con distintas formas del nivel. Es necesario reconciliarlas antes de consolidar este ámbito.",
            409,
        )
    superpuestos = conn.execute(
        "SELECT 1 FROM trabajador_en_reporte t JOIN reporte_asistencia r USING(reporte_asistencia_id) "
        "WHERE r.reporte_asistencia_id=ANY(%s) AND t.trabajador_id IS NOT NULL "
        "GROUP BY r.institucion_educativa_id,t.trabajador_id,t.rol_laboral_id,t.vinculo_trabajador_ie_id "
        "HAVING count(*) > 1 LIMIT 1",
        ([r["reporte_asistencia_id"] for r in reportes],),
    ).fetchone()
    if superpuestos and cierre_tecnico is None:
        raise ErrorDeTrabajo(
            "Hay filas del mismo vínculo en más de un reporte o turno del mes. Revisa el solapamiento antes de consolidar; no se eligió una fila automáticamente.",
            409,
        )
    filas_elegidas = None
    if cierre_tecnico is not None:
        filas_elegidas = set(cierre_tecnico["filas_elegidas"])
        elegidas = conn.execute(
            """SELECT t.*,r.institucion_educativa_id FROM trabajador_en_reporte t
          JOIN reporte_asistencia r USING(reporte_asistencia_id)
          WHERE t.trabajador_en_reporte_id::text=ANY(%s) AND r.reporte_asistencia_id=ANY(%s)""",
            (list(filas_elegidas), [r["reporte_asistencia_id"] for r in reportes]),
        ).fetchall()
        granos = {
            (
                r["institucion_educativa_id"],
                r["trabajador_id"],
                r["rol_laboral_id"],
                r["vinculo_trabajador_ie_id"],
            )
            for r in elegidas
        }
        if len(elegidas) != len(filas_elegidas) or len(granos) != len(elegidas):
            raise ErrorDeTrabajo(
                "La selección técnica contiene filas ajenas al ámbito o vínculos duplicados.",
                409,
            )
    fuentes = []
    for r in reportes:
        original = uno(
            conn,
            "SELECT o.* FROM documento_recibido d JOIN objeto_archivo o USING(objeto_archivo_id) WHERE documento_recibido_id=%s",
            (r["documento_recibido_id"],),
        )
        copia = conn.execute(
            "SELECT ruta_copia FROM web_carga WHERE objeto_archivo_id=%s LIMIT 1",
            (original["objeto_archivo_id"],),
        ).fetchone()
        ruta_verificada(
            copia["ruta_copia"] if copia else original["ruta_objeto"],
            original["sha256"],
        )
        decisiones = conn.execute(
            "SELECT e.*,u.nombre AS autor FROM web_revision_evento e JOIN usuario u USING(usuario_id) WHERE reporte_asistencia_id=%s ORDER BY creado_en",
            (r["reporte_asistencia_id"],),
        ).fetchall()
        fuentes.append(
            {
                "id": str(r["reporte_asistencia_id"]),
                "version": r["version"],
                "nombre_ie": r["nombre_ie"],
                "sha256": original["sha256"],
                "huella": huella_reporte(conn, r["reporte_asistencia_id"]),
                "decisiones": decisiones,
            }
        )
    calendarios = calendarios_actuales(conn, reportes, periodo.year)
    for calendario in calendarios:
        if calendario["objeto_archivo_id"]:
            original = uno(
                conn,
                "SELECT * FROM objeto_archivo WHERE objeto_archivo_id=%s",
                (calendario["objeto_archivo_id"],),
            )
            copia = conn.execute(
                "SELECT ruta_copia FROM web_carga WHERE objeto_archivo_id=%s LIMIT 1",
                (calendario["objeto_archivo_id"],),
            ).fetchone()
            ruta_verificada(
                copia["ruta_copia"] if copia else original["ruta_objeto"],
                original["sha256"],
            )
    resultado = generar_consolidado(
        conn, periodo, nivel, confirmar=False, filas_elegidas=filas_elegidas
    )
    if resultado.error:
        raise ErrorDeTrabajo(
            "No se pudo preparar el consolidado para el ámbito seleccionado.", 409
        )
    cid = resultado.consolidado_dre_id
    if revisado and not evaluar_revision(conn, cid).revisable:
        raise ErrorDeTrabajo(
            "Hay observaciones críticas pendientes; la salida sigue siendo un borrador.",
            409,
        )
    conn.execute(
        "UPDATE consolidado_dre SET generado_por=%s WHERE consolidado_dre_id=%s",
        (uid, cid),
    )
    filas = conn.execute(
        """SELECT d.*,ie.nombre_ie,ie.cod_mod,ie.anexo,t.apellido_paterno,t.apellido_materno,t.nombres,
                  t.dni,c.nombre AS rol
           FROM consolidado_dre_detalle d JOIN institucion_educativa ie USING(institucion_educativa_id)
           JOIN trabajador t USING(trabajador_id) JOIN catalogo_rol_laboral c USING(rol_laboral_id)
           WHERE consolidado_dre_id=%s ORDER BY ie.nombre_ie,t.apellido_paterno,t.apellido_materno,t.nombres""",
        (cid,),
    ).fetchall()
    manifiesto = json_datos(
        {
            "version_contrato": "P06.3",
            "cierre_tecnico": cierre_tecnico,
            "cobertura_estado": "NO_CALCULABLE",
            "cobertura_motivo": "Falta el manifiesto de unidades esperadas validado por RRHH.",
            "periodo": periodo,
            "nivel": nivel,
            "estado": estado,
            "reportes": fuentes,
            "calendarios": calendarios,
            "coherencia": coherencias,
            "huella_contexto_calculo": huella_contexto_calculo(conn, reportes),
            "reglas": {
                "clasificacion_reportada_legacy": dict(_CLASIFICACION_FALTA),
                "cruce_remuneracion": "CRUCE_REMUNERACION_1",
                "ajustes_asistencia": "AJUSTE_ASISTENCIA_RRHH_1",
                "ajustes_asistencia_sha256": sha256_de(
                    Path(modulo_generar.__file__).parent.parent
                    / "ajustes_asistencia.py"
                ),
                "remuneracion_sha256": sha256_de(
                    Path(modulo_generar.__file__).with_name("remuneracion.py")
                ),
                "leyendas_sha256": sha256_de(
                    Path(modulo_generar.__file__).parent.parent / "leyendas.py"
                ),
                "generar_sha256": sha256_de(Path(modulo_generar.__file__)),
                "dias_esperados_sha256": sha256_de(Path(modulo_universo.__file__)),
                "presencia_esperada_sql": conn.execute(
                    "SELECT pg_get_functiondef('fn_vinculo_presencia_esperada(bigint,date)'::regprocedure) AS definicion"
                ).fetchone()["definicion"],
                "contrato_metricas": "1.0",
            },
            "filas": filas,
            "filas_detalle": len(filas),
            "personas_distintas": len({r["trabajador_id"] for r in filas}),
            "filas_omitidas_sin_identidad": resultado.personas_omitidas_sin_identidad,
            "alertas_pendientes": resultado.validaciones_pendientes,
        }
    )
    destino = current_app.config["STORAGE_ROOT"] / "salidas" / f"{uuid.uuid4()}.xlsx"
    exportar_consolidado_xlsx(
        conn,
        cid,
        destino,
        confirmar=False,
        estado_revision="REVISADO LOCALMENTE" if revisado else "BORRADOR",
        anexos_cierre=cierre_tecnico,
        anexo_coherencia=coherencias,
    )
    destino.chmod(0o400)
    objeto = uno(
        conn,
        "SELECT archivo_exportado_id FROM consolidado_dre WHERE consolidado_dre_id=%s",
        (cid,),
    )
    conn.execute(
        "INSERT INTO web_salida(web_salida_id,consolidado_dre_id,objeto_archivo_id,estado_revision,manifiesto,creado_por) VALUES (%s,%s,%s,%s,%s,%s)",
        (
            operacion,
            cid,
            objeto["archivo_exportado_id"],
            estado,
            Jsonb(manifiesto),
            uid,
        ),
    )
    return operacion
