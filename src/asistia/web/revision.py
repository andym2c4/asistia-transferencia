"""Decisiones explícitas con historial y control de edición obsoleta."""

from __future__ import annotations

import json

from psycopg.types.json import Jsonb

from asistia.consolidado.revision import resolver_alerta

from . import ErrorDeTrabajo
from .lecturas import huella_reporte, uno


def json_datos(datos):
    return json.loads(json.dumps(datos, default=str))


def verificar_edicion(conn, rid, huella):
    r = uno(
        conn,
        "SELECT * FROM reporte_asistencia WHERE reporte_asistencia_id=%s FOR UPDATE",
        (rid,),
    )
    posterior = conn.execute(
        "SELECT 1 FROM reporte_asistencia WHERE reporte_asistencia_serie_id=%s AND version > %s",
        (r["reporte_asistencia_serie_id"], r["version"]),
    ).fetchone()
    if posterior or huella_reporte(conn, rid) != huella:
        raise ErrorDeTrabajo(
            "Hay cambios posteriores a lo que estabas revisando. Recarga el reporte, compara los datos y registra tu decisión sobre la versión actual.",
            409,
        )
    return r


def ya_registrada(conn, operacion, rid, accion, motivo, objetivo=None):
    evento = conn.execute(
        "SELECT * FROM web_revision_evento WHERE operacion_id=%s", (operacion,)
    ).fetchone()
    if evento:
        if (
            str(evento["reporte_asistencia_id"]),
            evento["accion"],
            evento["motivo"],
            evento["posterior"].get("objetivo"),
        ) != (str(rid), accion, motivo, objetivo):
            raise ErrorDeTrabajo(
                "Ese envío ya se registró con otra decisión. Abre el reporte de nuevo.",
                409,
            )
        return True
    return False


def registrar(
    conn,
    operacion,
    rid,
    accion,
    motivo,
    anterior,
    posterior,
    uid,
    alerta=None,
    persona=None,
):
    tarea = conn.execute(
        "SELECT t.web_tarea_id FROM web_tarea t "
        "JOIN reporte_asistencia r ON r.periodo=t.periodo "
        "JOIN reporte_asistencia_serie s USING(reporte_asistencia_serie_id) "
        "WHERE r.reporte_asistencia_id=%s AND t.usuario_id=%s AND t.estado='ACTIVA' "
        "AND fn_nivel_canonico(s.nivel_modalidad)=t.nivel",
        (rid, uid),
    ).fetchone()
    conn.execute(
        "INSERT INTO web_revision_evento(operacion_id,reporte_asistencia_id,validacion_reporte_id,trabajador_en_reporte_id,"
        "accion,motivo,anterior,posterior,usuario_id,web_tarea_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            operacion,
            rid,
            alerta,
            persona,
            accion,
            motivo,
            Jsonb(json_datos(anterior)),
            Jsonb(json_datos(posterior)),
            uid,
            tarea["web_tarea_id"] if tarea else None,
        ),
    )


def decidir_alerta(conn, rid, aid, accion, motivo, huella, operacion, uid):
    if accion not in {"CONFIRMAR_ALERTA", "DESCARTAR_ALERTA", "DEJAR_PENDIENTE"}:
        raise ErrorDeTrabajo("Selecciona una decisión válida.")
    if ya_registrada(conn, operacion, rid, accion, motivo, str(aid)):
        return
    verificar_edicion(conn, rid, huella)
    a = uno(
        conn,
        "SELECT * FROM validacion_reporte WHERE validacion_reporte_id=%s AND reporte_asistencia_id=%s FOR UPDATE",
        (aid, rid),
    )
    from asistia.calidad_leyendas import REGLA_ALERTA

    if a["codigo_regla"] == REGLA_ALERTA and accion != "DEJAR_PENDIENTE":
        raise ErrorDeTrabajo(
            "Resuelve esta observación revisando los códigos en Leyenda y remuneración. La observación se cierra al terminar ese cotejo.",
            409,
        )
    if a["estado"] != "PENDIENTE":
        raise ErrorDeTrabajo(
            "Esta observación ya tiene una decisión. Recarga el reporte para consultarla.",
            409,
        )
    if accion == "CONFIRMAR_ALERTA":
        resolver_alerta(conn, str(aid), uid, motivo, confirmar=False)
    elif accion == "DESCARTAR_ALERTA":
        conn.execute(
            "UPDATE validacion_reporte SET estado='DESCARTADA',resuelta_por=%s,resuelta_en=now(),motivo_resolucion=%s WHERE validacion_reporte_id=%s",
            (uid, motivo, aid),
        )
    nuevo = uno(
        conn, "SELECT * FROM validacion_reporte WHERE validacion_reporte_id=%s", (aid,)
    )
    registrar(
        conn,
        operacion,
        rid,
        accion,
        motivo,
        a,
        {**nuevo, "objetivo": str(aid)},
        uid,
        alerta=aid,
    )


def confirmar_reporte(conn, rid, motivo, huella, operacion, uid):
    if ya_registrada(conn, operacion, rid, "REVISAR_REPORTE", motivo):
        return
    anterior = verificar_edicion(conn, rid, huella)
    from asistia.calidad_leyendas import pendiente
    from asistia.leyendas import codigos_usados_asistencia

    usados = codigos_usados_asistencia(
        conn.execute(
            "SELECT a.* FROM asistencia_dia a JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id) WHERE t.reporte_asistencia_id=%s",
            (rid,),
        ).fetchall()
    )

    if any(
        pendiente(c)
        for codigo, c in anterior["clasificacion_codigos"].items()
        if codigo in usados
    ):
        raise ErrorDeTrabajo(
            "La leyenda tiene una posible desalineación. Revisa los códigos afectados junto al original antes de confirmar el reporte.",
            409,
        )
    if anterior["estado"] == "VALIDADO":
        raise ErrorDeTrabajo(
            "Este reporte ya está revisado. Consulta su historial.", 409
        )
    criticos = conn.execute(
        "SELECT count(*) AS n FROM validacion_reporte WHERE reporte_asistencia_id=%s AND severidad='ERROR' AND estado='PENDIENTE' AND codigo_regla<>'LEYENDA_POSIBLE_DESFASE'",
        (rid,),
    ).fetchone()["n"]
    omitidos = conn.execute(
        "SELECT count(*) AS n FROM trabajador_en_reporte WHERE reporte_asistencia_id=%s AND (trabajador_id IS NULL OR rol_laboral_id IS NULL)",
        (rid,),
    ).fetchone()["n"]
    if criticos or omitidos:
        raise ErrorDeTrabajo(
            f"Falta resolver {criticos} observaciones críticas y {omitidos} identidades o roles. El reporte continúa pendiente.",
            409,
        )
    if not conn.execute(
        "SELECT 1 FROM trabajador_en_reporte WHERE reporte_asistencia_id=%s", (rid,)
    ).fetchone():
        raise ErrorDeTrabajo(
            "Este reporte no contiene personas identificadas. Revisa la carga antes de declararlo revisado.",
            409,
        )
    # La revisión explícita descarta el aviso agregado sin incidencia en las
    # marcas usadas. Conservar ambos estados evita ocultar un ERROR pendiente
    # que reaparecería después en el consolidado.
    alertas_leyenda = conn.execute(
        "SELECT * FROM validacion_reporte WHERE reporte_asistencia_id=%s AND codigo_regla='LEYENDA_POSIBLE_DESFASE' AND estado='PENDIENTE' FOR UPDATE",
        (rid,),
    ).fetchall()
    descartadas = conn.execute(
        "UPDATE validacion_reporte SET estado='DESCARTADA',resuelta_por=%s,resuelta_en=now(),motivo_resolucion=%s "
        "WHERE reporte_asistencia_id=%s AND codigo_regla='LEYENDA_POSIBLE_DESFASE' AND estado='PENDIENTE' RETURNING *",
        (
            uid,
            "Sin desalineaciones pendientes en los códigos usados de esta versión. "
            "Las leyendas sin uso se conservan para consulta. " + motivo,
            rid,
        ),
    ).fetchall()
    conn.execute(
        "UPDATE reporte_asistencia SET estado='HISTORICA' WHERE reporte_asistencia_serie_id=%s AND estado='VALIDADO' AND reporte_asistencia_id<>%s",
        (anterior["reporte_asistencia_serie_id"], rid),
    )
    conn.execute(
        "UPDATE reporte_asistencia SET estado='VALIDADO',validado_por=%s,validado_en=now() WHERE reporte_asistencia_id=%s",
        (uid, rid),
    )
    posterior = uno(
        conn, "SELECT * FROM reporte_asistencia WHERE reporte_asistencia_id=%s", (rid,)
    )
    if alertas_leyenda:
        anterior["alertas_leyenda"] = alertas_leyenda
        posterior["alertas_leyenda"] = descartadas
    registrar(conn, operacion, rid, "REVISAR_REPORTE", motivo, anterior, posterior, uid)


def corregir_marca(conn, rid, did, codigo, captura, motivo, huella, operacion, uid):
    objetivo = {"dia": str(did), "codigo": codigo, "captura": captura}
    if ya_registrada(conn, operacion, rid, "CORREGIR_MARCA", motivo, objetivo):
        return
    reporte = verificar_edicion(conn, rid, huella)
    anterior = uno(
        conn,
        "SELECT a.* FROM asistencia_dia a JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id) "
        "WHERE a.asistencia_dia_id=%s AND t.reporte_asistencia_id=%s FOR UPDATE OF a",
        (did, rid),
    )
    estado_id = None
    categoria_local = reporte["clasificacion_codigos"].get(codigo)
    from asistia.calidad_leyendas import pendiente

    if captura == "REGISTRADO" and pendiente(categoria_local or {}):
        raise ErrorDeTrabajo(
            "Primero revisa el significado de este código en la leyenda; su alineación está pendiente.",
            409,
        )
    if captura == "REGISTRADO":
        estado_id = uno(
            conn,
            "SELECT estado_asistencia_id FROM catalogo_estado_asistencia WHERE codigo=%s AND activo",
            (
                (categoria_local.get("estado_asistencia_codigo") or "OTRO_REPORTADO")
                if categoria_local
                else codigo,
            ),
        )["estado_asistencia_id"]
    elif captura not in {"VACIO", "ILEGIBLE", "NO_APLICA", "PENDIENTE"}:
        raise ErrorDeTrabajo("Selecciona una marca o un estado desconocido válido.")
    from asistia.leyendas import interpretar_descripcion

    cat = (
        conn.execute(
            "SELECT nombre FROM catalogo_estado_asistencia WHERE estado_asistencia_id=%s",
            (estado_id,),
        ).fetchone()
        if estado_id
        else None
    )
    evidencia = (
        {
            "clasificacion_aceptada": {
                **(
                    categoria_local
                    or interpretar_descripcion(cat["nombre"], "asistencia")
                ),
                "fuente": "CORRECCION_WEB",
                "autor": uid,
                "motivo": motivo,
            }
        }
        if cat
        else {"dato_no_determinado": True, "autor": uid, "motivo": motivo}
    )
    conn.execute(
        "UPDATE asistencia_dia SET codigo_interpretado=%s,evidencia_interpretacion=%s WHERE asistencia_dia_id=%s",
        (codigo if cat else None, Jsonb(evidencia), did),
    )
    # La marca recibida y su localizador permanecen. La corrección aceptada y el antes/después
    # quedan separados. Los archivos web ya preparados conservan sus bytes y manifiesto.
    conn.execute(
        "UPDATE asistencia_dia SET estado_asistencia_id=%s,estado_captura=%s,hecho_asistencia_dia_id=NULL,"
        "observacion=%s,validado=true,version_extraccion='RRHH_WEB_1' WHERE asistencia_dia_id=%s",
        (estado_id, captura, motivo, did),
    )
    conn.execute(
        "UPDATE reporte_asistencia SET estado='EN_VALIDACION',validado_por=NULL,validado_en=NULL WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    posterior = uno(
        conn, "SELECT * FROM asistencia_dia WHERE asistencia_dia_id=%s", (did,)
    )
    registrar(
        conn,
        operacion,
        rid,
        "CORREGIR_MARCA",
        motivo,
        anterior,
        {**posterior, "objetivo": objetivo},
        uid,
        persona=anterior["trabajador_en_reporte_id"],
    )


def resolver_identidad(conn, rid, tid, vid, motivo, huella, operacion, uid):
    objetivo = {"persona": str(tid), "vinculo": vid}
    if ya_registrada(conn, operacion, rid, "RESOLVER_IDENTIDAD", motivo, objetivo):
        return
    reporte = verificar_edicion(conn, rid, huella)
    anterior = uno(
        conn,
        "SELECT * FROM trabajador_en_reporte WHERE trabajador_en_reporte_id=%s AND reporte_asistencia_id=%s FOR UPDATE",
        (tid, rid),
    )
    vinculo = uno(
        conn,
        "SELECT * FROM vinculo_trabajador_ie WHERE vinculo_trabajador_ie_id=%s AND institucion_educativa_id=%s AND trabajador_id IS NOT NULL",
        (vid, reporte["institucion_educativa_id"]),
    )
    conn.execute(
        "UPDATE trabajador_en_reporte SET trabajador_id=%s,rol_laboral_id=%s,vinculo_trabajador_ie_id=%s,"
        "estado_match='RESUELTO',metodo_match='MANUAL',resolucion_manual_por=%s,resolucion_manual_en=now(),"
        "resolucion_manual_motivo=%s WHERE trabajador_en_reporte_id=%s",
        (vinculo["trabajador_id"], vinculo["rol_laboral_id"], vid, uid, motivo, tid),
    )
    conn.execute(
        "UPDATE asistencia_dia SET hecho_asistencia_dia_id=NULL WHERE trabajador_en_reporte_id=%s",
        (tid,),
    )
    conn.execute(
        "UPDATE reporte_asistencia SET estado='EN_VALIDACION',validado_por=NULL,validado_en=NULL WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    posterior = uno(
        conn,
        "SELECT * FROM trabajador_en_reporte WHERE trabajador_en_reporte_id=%s",
        (tid,),
    )
    registrar(
        conn,
        operacion,
        rid,
        "RESOLVER_IDENTIDAD",
        motivo,
        anterior,
        {**posterior, "objetivo": objetivo},
        uid,
        persona=tid,
    )
