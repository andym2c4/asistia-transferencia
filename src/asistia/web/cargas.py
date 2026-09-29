"""Recepciones idempotentes y ejecución con progreso durable por archivo."""

from __future__ import annotations

import json
from dataclasses import asdict

from psycopg.types.json import Jsonb

from asistia.importar.asistencia import importar_asistencia
from asistia.importar.calendario import importar_calendario
from asistia.importar.nexus import importar_nexus

from . import ErrorDeTrabajo
from .archivos import comprobar_formato, conservar, ruta_verificada
from .lecturas import uno


def recibir(conn, archivo, tipo, corte, recibido, periodo, usuario_id, operacion):
    path, sha, size = conservar(archivo)
    anterior = conn.execute(
        "SELECT r.*,c.tipo,c.fecha_corte,o.sha256 FROM web_recepcion r JOIN web_carga c USING(web_carga_id) "
        "JOIN objeto_archivo o USING(objeto_archivo_id) WHERE web_recepcion_id=%s",
        (operacion,),
    ).fetchone()
    if anterior:
        if (
            anterior["sha256"],
            anterior["tipo"],
            anterior["fecha_corte"],
            anterior["recibido_real_en"],
            anterior["periodo_esperado"],
        ) != (sha, tipo, corte, recibido, periodo):
            raise ErrorDeTrabajo(
                "Este envío ya se registró con otros datos. Abre un formulario nuevo para otra recepción.",
                409,
            )
        return anterior["web_carga_id"], True
    obj = conn.execute(
        "SELECT objeto_archivo_id FROM objeto_archivo WHERE sha256=%s",
        (sha,),
    ).fetchone()
    if not obj:
        mime = {
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xls": "application/vnd.ms-excel",
            ".pdf": "application/pdf",
        }.get(path.suffix.lower(), "application/octet-stream")
        obj = conn.execute(
            "INSERT INTO objeto_archivo(sha256,ruta_objeto,mime_type,tamano_bytes) VALUES (%s,%s,%s,%s) RETURNING objeto_archivo_id",
            (sha, str(path), mime, size),
        ).fetchone()
    carga = conn.execute(
        "SELECT web_carga_id FROM web_carga WHERE objeto_archivo_id=%s AND tipo=%s AND fecha_corte IS NOT DISTINCT FROM %s",
        (obj["objeto_archivo_id"], tipo, corte),
    ).fetchone()
    repetida = carga is not None
    if carga is None:
        carga = conn.execute(
            "INSERT INTO web_carga(objeto_archivo_id,ruta_copia,tipo,fecha_corte) VALUES (%s,%s,%s,%s) RETURNING web_carga_id",
            (obj["objeto_archivo_id"], str(path), tipo, corte),
        ).fetchone()
    conn.execute(
        "INSERT INTO web_recepcion(web_recepcion_id,web_carga_id,nombre_original,recibido_real_en,periodo_esperado,cargado_por) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (
            operacion,
            carga["web_carga_id"],
            (archivo.filename or "documento")[:250],
            recibido,
            periodo,
            usuario_id,
        ),
    )
    conn.commit()  # El original y su recepción sobreviven a un fallo del importador.
    return carga["web_carga_id"], repetida


def mensaje_resultado(tipo, datos):
    if datos.get("error"):
        texto = datos["error"].lower()
        if "instituci" in texto:
            return "No se pudo identificar la institución. Revisa el corte NEXUS y el nombre/nivel del encabezado."
        if "período" in texto or "periodo" in texto or "anio" in texto:
            return (
                "No se pudo leer el período o año. Revisa el encabezado del original."
            )
        if "anexo" in texto or "dni" in texto or "encabezado" in texto:
            return "No se encontró la estructura esperada. Revisa la hoja ANEXO 3 y las columnas de identificación."
        return "El archivo no pudo procesarse completamente. Revisa su original y el formato admitido."
    if tipo == "nexus":
        return f"{datos['total_filas']} filas leídas; {datos['resueltas']} vínculos identificados; {len(datos['errores'])} filas con error."
    if tipo == "asistencia":
        return f"{len(datos['reportes_asistencia_id'])} reportes identificados; {datos['total_trabajadores']} filas de personas; {datos['pendientes']} por identificar."
    return f"{datos['dias_registrados']} días con código; {datos['dias_vacios']} vacíos; {datos['dias_codigo_desconocido']} códigos desconocidos."


def procesar(conn, cid, usuario_id, operacion):
    carga = uno(
        conn,
        "SELECT c.*,o.sha256 FROM web_carga c JOIN objeto_archivo o USING(objeto_archivo_id) WHERE web_carga_id=%s FOR UPDATE OF c",
        (cid,),
    )
    repetido = conn.execute(
        "SELECT web_carga_id FROM web_carga_intento WHERE web_carga_intento_id=%s",
        (operacion,),
    ).fetchone()
    if repetido:
        if str(repetido["web_carga_id"]) != str(cid):
            raise ErrorDeTrabajo("Ese intento corresponde a otro archivo.", 409)
        return
    if carga["estado"] == "PROCESADO":
        return
    if conn.execute(
        "SELECT 1 FROM web_revision_evento e JOIN reporte_asistencia r USING(reporte_asistencia_id) "
        "JOIN documento_recibido d USING(documento_recibido_id) WHERE d.objeto_archivo_id=%s LIMIT 1",
        (carga["objeto_archivo_id"],),
    ).fetchone():
        raise ErrorDeTrabajo(
            "Este contenido ya tiene decisiones de revisión. Se conservan sin reemplazarlas. "
            "Para incorporar una rectificación, carga un archivo corregido como nueva fuente.",
            409,
        )
    ruta = ruta_verificada(carga["ruta_copia"], carga["sha256"])
    if (
        carga["tipo"] != "nexus"
        and not conn.execute("SELECT 1 FROM institucion_educativa LIMIT 1").fetchone()
    ):
        raise ErrorDeTrabajo(
            "El original quedó recibido. Carga primero NEXUS para identificar las instituciones; luego pulsa Reintentar.",
            409,
        )
    # Tener el lock de escritura demuestra que no queda un importador web activo.
    conn.execute(
        "UPDATE web_carga_intento SET estado='INTERRUMPIDO',terminado_en=now() WHERE web_carga_id=%s AND terminado_en IS NULL",
        (cid,),
    )
    conn.execute(
        "INSERT INTO web_carga_intento(web_carga_intento_id,web_carga_id,iniciado_por) VALUES (%s,%s,%s)",
        (operacion, cid, usuario_id),
    )
    conn.execute(
        "UPDATE web_carga SET estado='PROCESANDO',actualizado_en=now() WHERE web_carga_id=%s",
        (cid,),
    )
    conn.commit()
    datos = {}
    mensaje = comprobar_formato(ruta, carga["tipo"])
    estado = "NO_SOPORTADO" if mensaje else "PROCESADO"
    if not mensaje:
        try:
            if carga["tipo"] == "nexus":
                resultado = importar_nexus(conn, ruta, carga["fecha_corte"])
            elif carga["tipo"] == "calendario":
                resultado = importar_calendario(conn, ruta)
            else:
                from asistia.cierre.ingesta import hojas_diarias

                if len(hojas_diarias(ruta)) > 1:
                    raise ValueError(
                        "El libro contiene varias tablas diarias; requiere lectura de todas sus hojas."
                    )
                resultado = importar_asistencia(conn, ruta)
            datos = json.loads(json.dumps(asdict(resultado), default=str))
            if datos.get("error"):
                conn.rollback()
                estado = "ERROR"
            elif (
                datos.get("errores")
                or datos.get("errores_fila")
                or datos.get("meses_sin_alinear")
            ):
                estado = "PARCIAL"
            mensaje = mensaje_resultado(carga["tipo"], datos)
        except Exception as exc:  # noqa: BLE001 -- registrar intento recuperable en la frontera del importador
            conn.rollback()
            estado = "ERROR"
            # El detalle técnico se limita a la clase; nunca renderizar traceback ni SQL a RRHH.
            datos = {"tipo_error": type(exc).__name__}
            mensaje = "El procesamiento se interrumpió. El original y los resultados ya confirmados se conservan. Revisa el archivo y reintenta."
    if (
        carga["tipo"] != "nexus"
        and estado in {"ERROR", "PARCIAL"}
        and not comprobar_formato(ruta, carga["tipo"])
    ):
        did = preparar_ocr(conn, carga, ruta)
        datos["cierre_documento_id"] = str(did)
        estado = "PARCIAL"
        mensaje = "El original está conservado. Continúa con la lectura asistida para extraer sus tablas y revisar los resultados."
    conn.execute(
        "UPDATE web_carga SET estado=%s,resultado=%s,mensaje=%s,actualizado_en=now() WHERE web_carga_id=%s",
        (estado, Jsonb(datos), mensaje, cid),
    )
    conn.execute(
        "UPDATE web_carga_intento SET estado=%s,resultado=%s,mensaje=%s,terminado_en=now() WHERE web_carga_intento_id=%s",
        (estado, Jsonb(datos), mensaje, operacion),
    )
    conn.commit()


def preparar_ocr(conn, carga, ruta):
    from asistia.db import registrar_documento

    tipo = "ASISTENCIA" if carga["tipo"] == "asistencia" else "CALENDARIO_2026"
    anterior = conn.execute(
        "SELECT cierre_documento_id FROM cierre_documento WHERE sha256=%s AND tipo=%s",
        (carga["sha256"], tipo),
    ).fetchone()
    if anterior:
        return anterior["cierre_documento_id"]
    with conn.cursor() as cur:
        did = registrar_documento(cur, ruta, "application/octet-stream")
    return conn.execute(
        """INSERT INTO cierre_documento(sha256,tipo,ruta,rutas,documento_recibido_id,metodo)
      VALUES(%s,%s,%s,%s,%s,'NATIVO_FALLIDO') RETURNING cierre_documento_id""",
        (carga["sha256"], tipo, str(ruta), Jsonb([str(ruta)]), did),
    ).fetchone()["cierre_documento_id"]
