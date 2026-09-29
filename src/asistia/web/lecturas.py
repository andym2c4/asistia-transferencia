"""Consultas del recorrido: cada lista conserva su grano, ámbito y pendientes."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from asistia.consolidado.universo_esperado import calendario_aplicable

from . import ErrorDeTrabajo


def uno(conn, sql, params=()):
    fila = conn.execute(sql, params).fetchone()
    if fila is None:
        raise ErrorDeTrabajo(
            "No encontramos ese registro. Vuelve a la lista y comprueba la versión.",
            404,
        )
    return fila


def reportes_mes(conn, periodo, nivel):
    return conn.execute(
        """
        WITH ultimos AS (
            SELECT DISTINCT ON (s.reporte_asistencia_serie_id) r.*,
                   s.nivel_modalidad, s.turno
            FROM reporte_asistencia_serie s JOIN reporte_asistencia r USING(reporte_asistencia_serie_id)
            WHERE s.periodo=%s AND r.estado NOT IN ('RECHAZADO','HISTORICA') AND fn_nivel_canonico(s.nivel_modalidad)=%s
            ORDER BY s.reporte_asistencia_serie_id, r.version DESC
        )
        SELECT r.*, ie.nombre_ie, ie.cod_mod, ie.anexo,
          (SELECT count(*) FROM trabajador_en_reporte t WHERE t.reporte_asistencia_id=r.reporte_asistencia_id) AS filas,
          (SELECT count(*) FROM trabajador_en_reporte t WHERE t.reporte_asistencia_id=r.reporte_asistencia_id
              AND (t.trabajador_id IS NULL OR t.rol_laboral_id IS NULL)) AS sin_identidad,
          (SELECT count(*) FROM validacion_reporte v WHERE v.reporte_asistencia_id=r.reporte_asistencia_id
              AND v.estado='PENDIENTE') AS pendientes,
          (SELECT count(*) FROM validacion_reporte v WHERE v.reporte_asistencia_id=r.reporte_asistencia_id
              AND v.estado='PENDIENTE' AND v.severidad='ERROR') AS criticos
        FROM ultimos r JOIN institucion_educativa ie USING(institucion_educativa_id)
        ORDER BY ie.nombre_ie, r.turno
        """,
        (periodo, nivel),
    ).fetchall()


def instituciones_mes(conn, periodo, nivel):
    # Conocidas en NEXUS/padrón importado: no es el manifiesto validado de M04.
    return conn.execute(
        """
        SELECT ie.institucion_educativa_id,ie.nombre_ie,ie.cod_mod,ie.anexo,
          EXISTS (SELECT 1 FROM reporte_asistencia r WHERE r.institucion_educativa_id=ie.institucion_educativa_id
                  AND r.periodo=%s AND r.estado NOT IN ('RECHAZADO','HISTORICA')) AS recibido,
          EXISTS (SELECT 1 FROM calendarizacion_local cl JOIN calendarizacion_version cv USING(calendarizacion_local_id)
                  WHERE cl.institucion_educativa_id=ie.institucion_educativa_id AND cl.anio=%s) AS calendario
        FROM institucion_educativa ie WHERE fn_nivel_canonico(ie.nivel_modalidad)=%s
        ORDER BY recibido DESC,ie.nombre_ie
        """,
        (periodo, periodo.year, nivel),
    ).fetchall()


def reporte(conn, rid):
    return uno(
        conn,
        "SELECT r.*,ie.nombre_ie,ie.cod_mod,ie.anexo,d.nombre_original,o.sha256,o.ruta_objeto,o.mime_type, "
        "s.nivel_modalidad,s.turno,u.nombre AS revisor "
        "FROM reporte_asistencia r LEFT JOIN institucion_educativa ie USING(institucion_educativa_id) "
        "LEFT JOIN reporte_asistencia_serie s USING(reporte_asistencia_serie_id) "
        "JOIN documento_recibido d USING(documento_recibido_id) JOIN objeto_archivo o USING(objeto_archivo_id) "
        "LEFT JOIN usuario u ON u.usuario_id=r.validado_por WHERE r.reporte_asistencia_id=%s",
        (rid,),
    )


def huella_reporte(conn, rid):
    """Token optimista sobre hechos, identidad, decisiones y versión, sin PII en el formulario."""
    partes = []
    for sql in (
        "SELECT to_jsonb(r) AS dato FROM reporte_asistencia r WHERE reporte_asistencia_id=%s",
        "SELECT to_jsonb(t) AS dato FROM trabajador_en_reporte t WHERE reporte_asistencia_id=%s ORDER BY trabajador_en_reporte_id",
        "SELECT to_jsonb(a) AS dato FROM asistencia_dia a JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id) WHERE t.reporte_asistencia_id=%s ORDER BY asistencia_dia_id",
        "SELECT to_jsonb(v) AS dato FROM validacion_reporte v WHERE reporte_asistencia_id=%s ORDER BY validacion_reporte_id",
    ):
        partes.extend(f["dato"] for f in conn.execute(sql, (rid,)).fetchall())
    return hashlib.sha256(
        json.dumps(_instantes_canonicos(partes), sort_keys=True, default=str).encode()
    ).hexdigest()


def _instantes_canonicos(valor):
    """La zona de una conexión no cambia la versión de un mismo instante persistido."""
    if isinstance(valor, dict):
        return {k: _instantes_canonicos(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_instantes_canonicos(v) for v in valor]
    if isinstance(valor, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})", valor
    ):
        return datetime.fromisoformat(valor).astimezone(UTC).isoformat()
    return valor


def alertas(conn, rid):
    filas = conn.execute(
        """SELECT v.*,t.nombres_reportados_raw,t.fila_detalle_origen,u.nombre AS revisor
           FROM validacion_reporte v LEFT JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id)
           LEFT JOIN usuario u ON u.usuario_id=v.resuelta_por
           WHERE v.reporte_asistencia_id=%s
           ORDER BY (v.estado='PENDIENTE') DESC,(v.severidad='ERROR') DESC,v.creado_en""",
        (rid,),
    ).fetchall()
    nombres_ie = {
        str(r["institucion_educativa_id"]): r["nombre_ie"]
        for r in conn.execute(
            "SELECT institucion_educativa_id,nombre_ie FROM institucion_educativa"
        ).fetchall()
    }
    for fila in filas:
        ev = fila["evidencia"]
        regla = fila["codigo_regla"]
        if regla == "INSTITUCION_ACTUALIZADA_AUTOMATICAMENTE":
            anterior = nombres_ie.get(
                str(ev.get("institucion_anterior_id")), "la institución del corte NEXUS"
            )
            nueva = nombres_ie.get(
                str(ev.get("institucion_nueva_id")), "la institución del reporte"
            )
            fila["explicacion"] = (
                f"NEXUS indica {anterior}; este reporte indica {nueva}. El vínculo del reporte se aplicó automáticamente y necesita tu revisión."
            )
        elif regla == "LEYENDA_POSIBLE_DESFASE":
            fila["explicacion"] = (
                "Los códigos y las descripciones de la leyenda podrían estar desalineados. "
                "Revisa las celdas del original; los códigos afectados permanecen sin interpretación automática."
            )
        elif regla == "ASISTENCIA_CONTRADICTORIA":
            fila["explicacion"] = (
                "Dos fuentes indican marcas distintas para la misma persona y fecha. Compara los originales y registra cuál corresponde."
            )
        elif "SIN_NEXUS" in regla or "VINCULO_CREADO" in regla:
            fila["explicacion"] = (
                "Se creó un vínculo a partir del reporte, sin una coincidencia suficiente en NEXUS. Comprueba la persona y su institución."
            )
        elif "MULTIPLES" in regla or "AMBIGUO" in regla:
            fila["explicacion"] = (
                "Hay más de un vínculo posible. Comprueba la plaza y la vigencia antes de confirmar."
            )
        else:
            fila["explicacion"] = (
                "Hay una observación sobre este registro. Contrasta la fuente y los datos antes de tomar una decisión."
            )
    return filas


def detalle_personas(conn, rid):
    return conn.execute(
        """SELECT t.*,v.tipo_registro,p.codigo_plaza,c.nombre AS rol,
           (SELECT count(*) FROM asistencia_dia a WHERE a.trabajador_en_reporte_id=t.trabajador_en_reporte_id
            AND a.estado_captura IN ('VACIO','ILEGIBLE','PENDIENTE')) AS dias_sin_resolver
           FROM trabajador_en_reporte t LEFT JOIN vinculo_trabajador_ie v USING(vinculo_trabajador_ie_id)
           LEFT JOIN plaza p USING(plaza_id) LEFT JOIN catalogo_rol_laboral c ON c.rol_laboral_id=t.rol_laboral_id
           WHERE t.reporte_asistencia_id=%s ORDER BY t.fila_detalle_origen NULLS LAST,t.fila_resumen_origen""",
        (rid,),
    ).fetchall()


def salidas(conn, periodo, nivel):
    return conn.execute(
        "SELECT w.*,c.periodo,c.nivel_modalidad,c.version,u.nombre AS autor "
        "FROM web_salida w JOIN consolidado_dre c USING(consolidado_dre_id) "
        "JOIN usuario u ON u.usuario_id=w.creado_por WHERE c.periodo=%s AND fn_nivel_canonico(c.nivel_modalidad)=%s "
        "ORDER BY c.generado_en DESC",
        (periodo, nivel),
    ).fetchall()


def salida(conn, sid):
    return uno(
        conn,
        "SELECT w.*,c.periodo,c.nivel_modalidad,c.version,o.ruta_objeto,o.sha256,u.nombre AS autor "
        "FROM web_salida w JOIN consolidado_dre c USING(consolidado_dre_id) "
        "JOIN objeto_archivo o ON o.objeto_archivo_id=w.objeto_archivo_id "
        "JOIN usuario u ON u.usuario_id=w.creado_por WHERE w.web_salida_id=%s",
        (sid,),
    )


def cambios_posteriores(conn, salida):
    fuentes = salida["manifiesto"]["reportes"]
    actuales = reportes_mes(conn, salida["periodo"], salida["nivel_modalidad"])
    actual = {
        str(r["reporte_asistencia_id"]): huella_reporte(
            conn, r["reporte_asistencia_id"]
        )
        for r in actuales
    }
    if actual != {r["id"]: r["huella"] for r in fuentes}:
        return True
    contexto = salida["manifiesto"].get("huella_contexto_calculo")
    if contexto and contexto != huella_contexto_calculo(conn, actuales):
        return True
    coherencia = salida["manifiesto"].get("coherencia")
    if coherencia is not None:
        from asistia.coherencia import revisar_reportes

        vivos = revisar_reportes(conn, actuales)
        if {rid: c["huella"] for rid, c in vivos.items()} != {
            rid: c["huella"] for rid, c in coherencia.items()
        }:
            return True
    calendarios = salida["manifiesto"].get("calendarios", [])
    return calendarios != calendarios_actuales(conn, actuales, salida["periodo"].year)


def huella_contexto_calculo(conn, reportes):
    """Datos vivos consumidos: nombres, identidad y vínculos con efecto en la nómina."""
    ids = [r["reporte_asistencia_id"] for r in reportes]
    partes = []
    consultas = (
        (
            "SELECT to_jsonb(ie) AS dato FROM institucion_educativa ie WHERE institucion_educativa_id IN "
            "(SELECT institucion_educativa_id FROM reporte_asistencia WHERE reporte_asistencia_id=ANY(%s)) ORDER BY institucion_educativa_id"
        ),
        (
            "SELECT to_jsonb(t) AS dato FROM trabajador t WHERE trabajador_id IN "
            "(SELECT trabajador_id FROM trabajador_en_reporte WHERE reporte_asistencia_id=ANY(%s)) ORDER BY trabajador_id"
        ),
        (
            "WITH elegidos AS (SELECT v.* FROM vinculo_trabajador_ie v WHERE vinculo_trabajador_ie_id IN "
            "(SELECT vinculo_trabajador_ie_id FROM trabajador_en_reporte WHERE reporte_asistencia_id=ANY(%s))) "
            "SELECT to_jsonb(v) AS dato FROM vinculo_trabajador_ie v WHERE EXISTS "
            "(SELECT 1 FROM elegidos e WHERE e.vinculo_trabajador_ie_id=v.vinculo_trabajador_ie_id "
            "OR e.trabajador_id=v.trabajador_id OR e.plaza_id=v.plaza_id) ORDER BY v.vinculo_trabajador_ie_id"
        ),
    )
    for sql in consultas:
        partes.append([r["dato"] for r in conn.execute(sql, (ids,)).fetchall()])
    return hashlib.sha256(
        json.dumps(_instantes_canonicos(partes), sort_keys=True, default=str).encode()
    ).hexdigest()


def calendarios_actuales(conn, reportes, anio):
    registros = []
    for iid in sorted({r["institucion_educativa_id"] for r in reportes}):
        with conn.cursor() as cur:
            cvid, estado = calendario_aplicable(cur, iid, anio)
        huella = None
        if cvid:
            dias = [
                f["dato"]
                for f in conn.execute(
                    "SELECT to_jsonb(d) AS dato FROM dia_calendarizacion d WHERE calendarizacion_version_id=%s ORDER BY fecha",
                    (cvid,),
                ).fetchall()
            ]
            huella = hashlib.sha256(
                json.dumps(
                    {
                        "dias": dias,
                        "clasificacion_codigos": conn.execute(
                            "SELECT clasificacion_codigos FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
                            (cvid,),
                        ).fetchone()["clasificacion_codigos"],
                    },
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
        origen = (
            conn.execute(
                "SELECT cv.version,cv.documento_recibido_id,o.objeto_archivo_id,o.sha256 "
                "FROM calendarizacion_version cv LEFT JOIN documento_recibido d USING(documento_recibido_id) "
                "LEFT JOIN objeto_archivo o USING(objeto_archivo_id) WHERE calendarizacion_version_id=%s",
                (cvid,),
            ).fetchone()
            if cvid
            else None
        )
        registros.append(
            {
                "institucion_id": iid,
                "nombre_ie": next(
                    r["nombre_ie"]
                    for r in reportes
                    if r["institucion_educativa_id"] == iid
                ),
                "version_id": str(cvid) if cvid else None,
                "estado": estado,
                "huella_dias": huella,
                "version": origen["version"] if origen else None,
                "objeto_archivo_id": str(origen["objeto_archivo_id"])
                if origen and origen["objeto_archivo_id"]
                else None,
                "sha256": origen["sha256"] if origen else None,
            }
        )
    return registros
