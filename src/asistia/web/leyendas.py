"""Revisión de leyendas locales con autor, motivo y protección frente a cambios."""

import hashlib
import json
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from asistia.cierre.ajustes import ajustar_calendario
from asistia.leyendas import (
    aplicar_leyenda_asistencia,
    aplicar_leyenda_calendario,
    interpretar_descripcion,
)

from . import ErrorDeTrabajo
from .lecturas import uno
from .revision import registrar, verificar_edicion, ya_registrada


def huella_calendario(conn, cvid):
    cv = uno(
        conn,
        "SELECT to_jsonb(c) AS dato FROM calendarizacion_version c WHERE calendarizacion_version_id=%s",
        (cvid,),
    )
    dias = conn.execute(
        "SELECT to_jsonb(d) AS dato FROM dia_calendarizacion d WHERE calendarizacion_version_id=%s ORDER BY fecha",
        (cvid,),
    ).fetchall()
    return hashlib.sha256(
        json.dumps([cv, *dias], sort_keys=True, default=str).encode()
    ).hexdigest()


def definir_categoria(formulario, dominio, uid, motivo, anterior):
    codigo = formulario.get("codigo", "").strip()
    descripcion = formulario.get("tipo_dia", "").strip()
    pago = formulario.get("remuneracion", "")
    if not codigo or len(codigo) > 60 or not descripcion or len(descripcion) > 500:
        raise ErrorDeTrabajo(
            "Indica el código y su significado según la leyenda (hasta 500 caracteres)."
        )
    if pago not in {"", "SI", "NO"}:
        raise ErrorDeTrabajo(
            "Selecciona remunerado, no remunerado o pendiente de clasificar."
        )
    c = interpretar_descripcion(descripcion, dominio)
    c["es_remunerado"] = {"SI": True, "NO": False, "": None}[pago]
    if dominio == "calendario":
        grupo = formulario.get("grupo_actividad", "")
        if grupo not in {"", "LECTIVO", "GESTION", "NO_LECTIVO_NI_GESTION"}:
            raise ErrorDeTrabajo("Selecciona una agrupación de actividad válida.")
        c["grupo_actividad"] = grupo or c["grupo_actividad"]
    else:
        falta = formulario.get("es_falta", "")
        if falta not in {"", "SI", "NO"}:
            raise ErrorDeTrabajo("Indica si la categoría representa una ausencia.")
        c["es_falta"] = {"SI": True, "NO": False, "": c["es_falta"]}[falta]
    c.update(
        fuente="REVISION_WEB",
        autor=uid,
        motivo=motivo,
        fecha=datetime.now(UTC).isoformat(),
        evidencia=anterior.get(codigo, {}).get("evidencia", {}),
    )
    return codigo, c


def guardar_asistencia(conn, rid, formulario, motivo, huella, operacion, uid):
    objetivo = {
        k: formulario.get(k, "")
        for k in ("codigo", "tipo_dia", "remuneracion", "es_falta")
    }
    if "modo_regla" in formulario:
        objetivo.update(
            {
                k: formulario.get(k, "")
                for k in ("modo_regla", "categoria_global", "huella_catalogo")
            }
        )
    if ya_registrada(conn, operacion, rid, "CLASIFICAR_LEYENDA", motivo, objetivo):
        return
    r = verificar_edicion(conn, rid, huella)
    antes = r["clasificacion_codigos"]
    codigo, c = definir_categoria(formulario, "asistencia", uid, motivo, antes)
    from .reporte_reglas import aplicar_regla_formulario

    c = aplicar_regla_formulario(conn, formulario, c)
    despues = {**antes, codigo: c}
    aplicar_leyenda_asistencia(conn, rid, despues, usar_catalogo=False)
    from asistia.calidad_leyendas import REGLA_ALERTA, pendiente

    if not any(pendiente(c) for c in despues.values()):
        conn.execute(
            """UPDATE validacion_reporte SET estado='RESUELTA',resuelta_por=%s,resuelta_en=now(),motivo_resolucion=%s
            WHERE reporte_asistencia_id=%s AND codigo_regla=%s AND estado='PENDIENTE'""",
            (
                uid,
                "Todos los códigos afectados cuentan con revisión explícita. Último sustento: "
                + motivo,
                rid,
                REGLA_ALERTA,
            ),
        )
    conn.execute(
        "UPDATE reporte_asistencia SET estado='EN_VALIDACION',validado_por=NULL,validado_en=NULL WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    registrar(
        conn,
        operacion,
        rid,
        "CLASIFICAR_LEYENDA",
        motivo,
        {"clasificacion_codigos": antes},
        {"clasificacion_codigos": despues, "objetivo": objetivo},
        uid,
    )


def guardar_calendario(conn, cvid, formulario, motivo, huella, operacion, uid):
    cv = uno(
        conn,
        "SELECT * FROM calendarizacion_version WHERE calendarizacion_version_id=%s FOR UPDATE",
        (cvid,),
    )
    conn.execute(
        "SELECT 1 FROM calendarizacion_local WHERE calendarizacion_local_id=%s FOR UPDATE",
        (cv["calendarizacion_local_id"],),
    )
    previo = conn.execute(
        "SELECT calendarizacion_version_id,procedencia_extraccion FROM calendarizacion_version WHERE procedencia_extraccion->'revision_leyenda'->>'operacion'=%s",
        (str(operacion),),
    ).fetchone()
    objetivo = {
        k: formulario.get(k, "")
        for k in ("codigo", "tipo_dia", "remuneracion", "grupo_actividad")
    }
    if "modo_regla" in formulario:
        objetivo.update(
            {
                k: formulario.get(k, "")
                for k in ("modo_regla", "categoria_global", "huella_catalogo")
            }
        )
    if previo:
        registro = previo["procedencia_extraccion"]["revision_leyenda"]
        if (
            registro["objetivo"] != objetivo
            or registro["motivo"] != motivo
            or registro["padre"] != str(cvid)
        ):
            raise ErrorDeTrabajo("Ese envío ya corresponde a otra clasificación.", 409)
        return previo["calendarizacion_version_id"]
    posterior = conn.execute(
        "SELECT 1 FROM calendarizacion_version WHERE calendarizacion_local_id=%s AND version>%s",
        (cv["calendarizacion_local_id"], cv["version"]),
    ).fetchone()
    if posterior or huella != huella_calendario(conn, cvid):
        raise ErrorDeTrabajo(
            "Hay una versión posterior o cambios en el calendario. Abre la versión actual antes de clasificar.",
            409,
        )
    if cv["estado"] in {"HISTORICA", "RECHAZADA"}:
        raise ErrorDeTrabajo(
            "Esta versión solo permite consulta. Abre la versión actual.", 409
        )
    codigo, c = definir_categoria(
        formulario, "calendario", uid, motivo, cv["clasificacion_codigos"]
    )
    from .reporte_reglas import aplicar_regla_formulario

    c = aplicar_regla_formulario(conn, formulario, c, "calendario")
    nuevo = ajustar_calendario(
        conn,
        cvid,
        {},
        f"Revisión de leyenda {codigo}: {motivo} · operación {operacion}",
    )
    conn.execute(
        """UPDATE calendarizacion_version n SET origen='CORRECCION_UGEL',creado_por=%s,
        total_lectivo_reportado_raw=p.total_lectivo_reportado_raw,total_gestion_reportado_raw=p.total_gestion_reportado_raw,
        total_no_lectivo_reportado_raw=p.total_no_lectivo_reportado_raw,horas_lectivas_reportadas_raw=p.horas_lectivas_reportadas_raw
        FROM calendarizacion_version p WHERE n.calendarizacion_version_id=%s AND p.calendarizacion_version_id=%s""",
        (uid, nuevo, cvid),
    )
    conn.execute(
        """UPDATE dia_calendarizacion n SET confianza=p.confianza FROM dia_calendarizacion p
        WHERE n.calendarizacion_version_id=%s AND p.calendarizacion_version_id=%s AND n.fecha=p.fecha""",
        (nuevo, cvid),
    )
    aplicar_leyenda_calendario(
        conn, nuevo, {**cv["clasificacion_codigos"], codigo: c}, usar_catalogo=False
    )
    motivo_visible = f"Revisión de leyenda {codigo}: {motivo}"
    conn.execute(
        """UPDATE calendarizacion_version SET motivo_version=%s,
        procedencia_extraccion=jsonb_set(procedencia_extraccion,'{ajuste_documental,motivo}',to_jsonb(%s::text))
        WHERE calendarizacion_version_id=%s""",
        (motivo_visible, motivo_visible, nuevo),
    )
    conn.execute(
        "UPDATE calendarizacion_version SET procedencia_extraccion=procedencia_extraccion||%s WHERE calendarizacion_version_id=%s",
        (
            Jsonb(
                {
                    "revision_leyenda": {
                        "autor": uid,
                        "motivo": motivo,
                        "operacion": str(operacion),
                        "padre": str(cvid),
                        "objetivo": objetivo,
                    }
                }
            ),
            nuevo,
        ),
    )
    return nuevo


def codigos_documento(documento, dias):
    codigos = set(documento["clasificacion_codigos"])
    codigos.update(
        d.get("codigo_interpretado") or d.get("codigo_reportado_raw") for d in dias
    )
    return [
        {"codigo": codigo, **documento["clasificacion_codigos"].get(codigo, {})}
        for codigo in sorted(c for c in codigos if c)
    ]
