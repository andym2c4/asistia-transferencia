"""Confirmación anual en un paso, con reglas visibles y una versión conservada."""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from asistia.categorias import resolver_entrada
from asistia.leyendas import aplicar_leyenda_calendario

from . import ErrorDeTrabajo
from .calendario_anual import calendario_anual
from .calendarios import marcar_vigente
from .lecturas import uno
from .leyendas import huella_calendario


def propuesta(conn, cvid):
    cv = uno(
        conn,
        """SELECT cv.*,cl.anio,cl.institucion_educativa_id
        FROM calendarizacion_version cv JOIN calendarizacion_local cl USING(calendarizacion_local_id)
        WHERE calendarizacion_version_id=%s""",
        (cvid,),
    )
    dias = conn.execute(
        """SELECT d.*,c.codigo_interno grupo_recibido FROM dia_calendarizacion d
        LEFT JOIN catalogo_tipo_dia c USING(tipo_dia_id) WHERE calendarizacion_version_id=%s ORDER BY fecha""",
        (cvid,),
    ).fetchall()
    categorias = resolver_entrada(conn, "calendario", cv["clasificacion_codigos"])
    # Simular exactamente la aplicación: las decisiones propias de cada día se conservan.
    simulados = []
    for d in dias:
        c = categorias.get(d["codigo_interpretado"] or d["codigo_reportado_raw"])
        if (
            c
            and d["estado_captura"] != "SIN_ASIGNAR"
            and not d["evidencia_interpretacion"].get("clasificacion_aceptada")
        ):
            grupo = c.get("grupo_actividad")
            d = {
                **d,
                "grupo_recibido": grupo,
                "estado_captura": "REGISTRADO" if grupo else "CODIGO_DESCONOCIDO",
            }
        simulados.append(d)
    anual = calendario_anual({**cv, "clasificacion_codigos": categorias}, simulados)
    posterior = conn.execute(
        "SELECT 1 FROM calendarizacion_version WHERE calendarizacion_local_id=%s AND version>%s",
        (cv["calendarizacion_local_id"], cv["version"]),
    ).fetchone()
    errores = []
    if posterior:
        errores.append(
            "Hay una versión posterior. Abre la última versión para confirmarla."
        )
    if cv["estado"] in {"HISTORICA", "RECHAZADA"}:
        errores.append("Esta versión es solo de consulta.")
    if cv["procedencia_extraccion"].get("tipo") == "DERIVADO_2025":
        errores.append(
            f"La fuente es una proyección de 2025. Carga el calendario real de {cv['anio']} antes de confirmar."
        )
    if anual["pendientes_revision"]:
        errores.append(
            "Quedan días o categorías por definir. Revisa los avisos de la leyenda y las fechas desde marzo; los vacíos de enero y febrero se conservan."
        )
    cambios = categorias != cv["clasificacion_codigos"]
    huella = hashlib.sha256(
        json.dumps(
            [huella_calendario(conn, cvid), categorias], sort_keys=True, default=str
        ).encode()
    ).hexdigest()
    return {
        "huella": huella,
        "categorias": categorias,
        "errores": errores,
        "cambios": cambios,
        "listo": not errores,
        "confirmado": cv["estado"] == "VIGENTE" and not errores and not cambios,
        "reglas": [
            {
                "codigo": k,
                "nombre": c.get("tipo_dia") or "Sin significado",
                "pago": c.get("es_remunerado"),
                "actividad": {
                    "LECTIVO": "Lectivo",
                    "GESTION": "Gestión",
                    "NO_LECTIVO_NI_GESTION": "No lectivo ni de gestión",
                }.get(c.get("grupo_actividad"), "Actividad por definir"),
                "general": c.get("categoria_general"),
                "nueva": c != cv["clasificacion_codigos"].get(k),
            }
            for k, c in sorted(categorias.items())
        ],
    }


def confirmar(conn, cvid, huella, operacion, uid):
    cv = uno(
        conn,
        """SELECT cv.* FROM calendarizacion_version cv JOIN calendarizacion_local cl USING(calendarizacion_local_id)
        WHERE calendarizacion_version_id=%s FOR UPDATE OF cv,cl""",
        (cvid,),
    )
    previo = conn.execute(
        """SELECT calendarizacion_version_id,procedencia_extraccion->'confirmacion_total' registro
        FROM calendarizacion_version WHERE procedencia_extraccion->'confirmacion_total'->>'operacion'=%s
        AND procedencia_extraccion->'confirmacion_total'->>'resultado'=calendarizacion_version_id::text""",
        (str(operacion),),
    ).fetchone()
    if previo:
        r = previo["registro"]
        if (r["padre"], r["huella"], r["autor"]) != (str(cvid), huella, uid):
            raise ErrorDeTrabajo(
                "Ese envío corresponde a otra confirmación. Recarga para revisar el estado actual.",
                409,
            )
        return previo["calendarizacion_version_id"]
    revision = propuesta(conn, cvid)
    if huella != revision["huella"]:
        raise ErrorDeTrabajo(
            "El calendario o sus reglas cambiaron. Recarga y compara antes de confirmar.",
            409,
        )
    if revision["errores"]:
        raise ErrorDeTrabajo(" ".join(revision["errores"]), 409)
    if revision["confirmado"]:
        return cvid
    nuevo = uuid.uuid4()
    motivo = "Calendario anual cotejado y confirmado en conjunto por RRHH."
    registro = {
        "padre": str(cvid),
        "resultado": str(nuevo),
        "huella": huella,
        "operacion": str(operacion),
        "autor": uid,
        "fecha": datetime.now(UTC).isoformat(),
        "motivo": motivo,
        "categorias_anteriores": cv["clasificacion_codigos"],
        "categorias_confirmadas": revision["categorias"],
    }
    meta = {**cv["procedencia_extraccion"], "confirmacion_total": registro}
    conn.execute(
        """INSERT INTO calendarizacion_version(calendarizacion_version_id,calendarizacion_local_id,version,version_padre_id,
        documento_recibido_id,fuente_derivada_id,origen,estado,motivo_version,procedencia_extraccion,clasificacion_codigos,
        total_lectivo_reportado_raw,total_gestion_reportado_raw,total_no_lectivo_reportado_raw,horas_lectivas_reportadas_raw,creado_por)
        SELECT %s,calendarizacion_local_id,version+1,calendarizacion_version_id,documento_recibido_id,fuente_derivada_id,
        'CORRECCION_UGEL','BORRADOR',%s,%s,clasificacion_codigos,total_lectivo_reportado_raw,total_gestion_reportado_raw,
        total_no_lectivo_reportado_raw,horas_lectivas_reportadas_raw,%s FROM calendarizacion_version WHERE calendarizacion_version_id=%s""",
        (nuevo, motivo, Jsonb(meta), uid, cvid),
    )
    conn.execute(
        """INSERT INTO dia_calendarizacion(calendarizacion_version_id,fecha,tipo_dia_id,codigo_reportado_raw,estado_captura,
        hoja_origen,celda_origen,observacion,confianza,modelo_extraccion,version_extraccion,codigo_interpretado,evidencia_interpretacion)
        SELECT %s,fecha,tipo_dia_id,codigo_reportado_raw,estado_captura,hoja_origen,celda_origen,observacion,confianza,
        modelo_extraccion,version_extraccion,codigo_interpretado,evidencia_interpretacion FROM dia_calendarizacion WHERE calendarizacion_version_id=%s""",
        (nuevo, cvid),
    )
    aplicar_leyenda_calendario(conn, nuevo, revision["categorias"], usar_catalogo=False)
    marcar_vigente(conn, nuevo, uid)
    return nuevo
