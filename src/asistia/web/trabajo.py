"""Captura declarada de trabajo compartido nivel/mes; ninguna tasa se infiere de clics."""

import uuid

from psycopg.types.json import Jsonb

from . import ErrorDeTrabajo
from .lecturas import uno


def tareas(conn, uid):
    return conn.execute(
        """SELECT t.*,
             COALESCE((SELECT floor(sum(extract(epoch FROM (i.fin-i.inicio))))::bigint
                       FROM web_tarea_intervalo i WHERE i.web_tarea_id=t.web_tarea_id AND i.fin IS NOT NULL),0) AS segundos_capturados,
             EXISTS(SELECT 1 FROM web_tarea_intervalo i WHERE i.web_tarea_id=t.web_tarea_id AND i.fin IS NULL) AS intervalo_abierto
           FROM web_tarea t WHERE usuario_id=%s ORDER BY creado_en DESC""",
        (uid,),
    ).fetchall()


def exportar_captura(conn, uid):
    """Corte privado por operador; componentes separados por grano, sin tasas inferidas."""
    # La autenticación abrió una transacción de lectura; abrir otra con un único corte.
    conn.rollback()
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    captura = {
        "version_captura": "P06.1",
        "version_metricas": "1.0",
        "corte": conn.execute("SELECT now() AS instante").fetchone()["instante"],
        "modo": "ASISTIDO",
        "grano_tiempo": "Trabajo compartido nivel/mes; no asignado a cada institución",
        "operador_id": uid,
        "resultados_metricas": "NO_MEDIDO",
        "tareas": tareas(conn, uid),
        "intervalos": conn.execute(
            "SELECT i.* FROM web_tarea_intervalo i JOIN web_tarea t USING(web_tarea_id) "
            "WHERE t.usuario_id=%s ORDER BY i.inicio",
            (uid,),
        ).fetchall(),
        "eventos_tarea": conn.execute(
            "SELECT e.* FROM web_tarea_evento e JOIN web_tarea t USING(web_tarea_id) "
            "WHERE t.usuario_id=%s ORDER BY e.creado_en",
            (uid,),
        ).fetchall(),
        "intervenciones": conn.execute(
            "SELECT e.web_revision_evento_id,e.operacion_id,e.web_tarea_id,e.reporte_asistencia_id,"
            "r.documento_recibido_id,o.sha256,r.version AS version_reporte,e.accion,e.creado_en,"
            "e.accion IN ('CORREGIR_MARCA','RESOLVER_IDENTIDAD') AS es_correccion_datos "
            "FROM web_revision_evento e JOIN reporte_asistencia r USING(reporte_asistencia_id) "
            "JOIN documento_recibido d USING(documento_recibido_id) JOIN objeto_archivo o USING(objeto_archivo_id) "
            "WHERE e.usuario_id=%s ORDER BY e.creado_en",
            (uid,),
        ).fetchall(),
        "recepciones": conn.execute(
            "SELECT r.web_recepcion_id,r.web_carga_id,r.recibido_real_en,r.periodo_esperado,r.cargado_en,"
            "o.sha256,c.tipo,c.fecha_corte,c.estado FROM web_recepcion r JOIN web_carga c USING(web_carga_id) "
            "JOIN objeto_archivo o USING(objeto_archivo_id) WHERE r.cargado_por=%s ORDER BY r.cargado_en",
            (uid,),
        ).fetchall(),
        "intentos": conn.execute(
            "SELECT web_carga_intento_id,web_carga_id,iniciado_en,terminado_en,estado FROM web_carga_intento "
            "WHERE iniciado_por=%s ORDER BY iniciado_en",
            (uid,),
        ).fetchall(),
        "limites": [
            "No contiene el manifiesto comparativo ni adjudica calidad M02/M03.",
            "PRACTICA no mide RRHH; OBSERVACION requiere verificar el protocolo externo.",
            "Intervalos declarados: una tarea abierta no demuestra trabajo humano activo.",
            "Confirmar revisión no es corregir datos; ausencia de correcciones no prueba exactitud M06.",
            "Intervenciones sin tarea se conservan sin atribución retroactiva.",
            "Archivo privado de captura, no informe público ni tasa M01/M06/M08.",
        ],
    }
    conn.rollback()
    return captura


def registrar_trabajo(conn, uid, periodo, nivel, form, op):
    accion = form.get("accion")
    datos = {
        k: form.get(k, "")
        for k in (
            "tarea",
            "salida",
            "cohorte",
            "tarea_referencia",
            "uso",
            "ayuda_tecnica",
            "lote_referencia",
            "version_referencia",
            "criterio_terminacion",
        )
    }
    datos.update(periodo=str(periodo), nivel=nivel, usuario_id=uid)
    anterior = conn.execute(
        "SELECT * FROM web_tarea_evento WHERE operacion_id=%s", (op,)
    ).fetchone()
    if anterior:
        if anterior["accion"] != accion or anterior["datos"] != datos:
            raise ErrorDeTrabajo(
                "Esta solicitud ya registró otro cambio de trabajo. Actualiza la página.",
                409,
            )
        return
    if accion == "INICIAR":
        if datos["uso"] not in {"PRACTICA", "OBSERVACION"}:
            raise ErrorDeTrabajo(
                "Indica si se trata de una práctica o de una observación programada."
            )
        for campo in (
            "cohorte",
            "tarea_referencia",
            "lote_referencia",
            "version_referencia",
            "criterio_terminacion",
        ):
            if not datos[campo].strip() or len(datos[campo]) > 500:
                raise ErrorDeTrabajo(
                    "Completa la referencia de la tarea, lote, versión y criterio de terminación (hasta 500 caracteres cada uno)."
                )
        if conn.execute(
            "SELECT 1 FROM web_tarea WHERE usuario_id=%s AND estado IN ('ACTIVA','PAUSADA')",
            (uid,),
        ).fetchone():
            raise ErrorDeTrabajo(
                "Ya tienes una tarea abierta. Retómala o termínala antes de iniciar otra.",
                409,
            )
        tid = op
        conn.execute(
            "INSERT INTO web_tarea(web_tarea_id,usuario_id,periodo,nivel,cohorte,tarea_referencia,uso,lote_referencia,version_referencia,criterio_terminacion) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                tid,
                uid,
                periodo,
                nivel,
                datos["cohorte"],
                datos["tarea_referencia"],
                datos["uso"],
                datos["lote_referencia"],
                datos["version_referencia"],
                datos["criterio_terminacion"],
            ),
        )
        conn.execute(
            "INSERT INTO web_tarea_intervalo(web_tarea_id) VALUES (%s)", (tid,)
        )
    else:
        try:
            tid = uuid.UUID(datos["tarea"])
        except ValueError:
            raise ErrorDeTrabajo("Selecciona una tarea válida.") from None
        tarea = uno(
            conn,
            "SELECT * FROM web_tarea WHERE web_tarea_id=%s AND usuario_id=%s FOR UPDATE",
            (tid, uid),
        )
        if tarea["estado"] not in {"ACTIVA", "PAUSADA"}:
            raise ErrorDeTrabajo(
                "La tarea ya terminó. Sus intervalos se conservan.", 409
            )
        if accion == "PAUSAR" and tarea["estado"] == "ACTIVA":
            conn.execute(
                "UPDATE web_tarea_intervalo SET fin=now() WHERE web_tarea_id=%s AND fin IS NULL",
                (tid,),
            )
            conn.execute(
                "UPDATE web_tarea SET estado='PAUSADA' WHERE web_tarea_id=%s", (tid,)
            )
        elif accion == "RETOMAR" and tarea["estado"] == "PAUSADA":
            conn.execute(
                "UPDATE web_tarea SET estado='ACTIVA' WHERE web_tarea_id=%s", (tid,)
            )
            conn.execute(
                "INSERT INTO web_tarea_intervalo(web_tarea_id) VALUES (%s)", (tid,)
            )
        elif accion in {"FINALIZAR", "ABANDONAR"}:
            sid = None
            if accion == "FINALIZAR":
                try:
                    sid = uuid.UUID(datos["salida"])
                except ValueError:
                    raise ErrorDeTrabajo(
                        "Selecciona la salida que preparaste para esta tarea."
                    ) from None
                s = uno(
                    conn,
                    "SELECT c.periodo,c.nivel_modalidad FROM web_salida w JOIN consolidado_dre c USING(consolidado_dre_id) WHERE web_salida_id=%s",
                    (sid,),
                )
                if (
                    s["periodo"] != tarea["periodo"]
                    or s["nivel_modalidad"] != tarea["nivel"]
                ):
                    raise ErrorDeTrabajo(
                        "La salida corresponde a otro mes o nivel. Selecciona la de esta tarea.",
                        409,
                    )
            conn.execute(
                "UPDATE web_tarea_intervalo SET fin=now() WHERE web_tarea_id=%s AND fin IS NULL",
                (tid,),
            )
            conn.execute(
                "UPDATE web_tarea SET estado=%s,terminado_en=now(),salida_id=%s,ayuda_tecnica=ayuda_tecnica OR %s WHERE web_tarea_id=%s",
                (
                    "FINALIZADA" if accion == "FINALIZAR" else "ABANDONADA",
                    sid,
                    datos["ayuda_tecnica"] == "si",
                    tid,
                ),
            )
        else:
            raise ErrorDeTrabajo(
                "La acción no corresponde al estado actual de la tarea. Actualiza la página.",
                409,
            )
    conn.execute(
        "INSERT INTO web_tarea_evento(operacion_id,web_tarea_id,accion,datos) VALUES (%s,%s,%s,%s)",
        (op, tid, accion, Jsonb(datos)),
    )
