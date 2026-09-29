"""Ampliar fuentes ya importadas sin sobrescribir versiones ni correcciones."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from asistia.leyendas import (
    aplicar_leyenda_asistencia,
    interpretar_descripcion,
    interpretar_feriado,
)


def _copiar(conn, tabla, clave, fuente, cambios):
    datos = {k: v for k, v in fuente.items() if k not in {clave, "creado_en"}}
    datos.update(cambios)
    consulta = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
        sql.Identifier(tabla),
        sql.SQL(",").join(map(sql.Identifier, datos)),
        sql.SQL(",").join(sql.Placeholder() for _ in datos),
        sql.Identifier(clave),
    )
    return conn.execute(
        consulta,
        [Jsonb(v) if isinstance(v, (dict, list)) else v for v in datos.values()],
    ).fetchone()[clave]


def ampliar_leyenda_reporte(
    conn, rid, categorias, motivo, *, ws=None, dias_grid=None, usar_catalogo=True
):
    if not categorias or not motivo:
        raise ValueError("La ampliación requiere leyenda y motivo verificables.")
    fuente = conn.execute(
        "SELECT * FROM reporte_asistencia WHERE reporte_asistencia_id=%s FOR UPDATE",
        (rid,),
    ).fetchone()
    if not fuente:
        raise ValueError("No existe el reporte fuente.")
    huella = hashlib.sha256(
        json.dumps(
            {"padre": str(rid), "leyenda": categorias, "motivo": motivo}, sort_keys=True
        ).encode()
    ).hexdigest()
    previo = conn.execute(
        "SELECT reporte_asistencia_id FROM reporte_asistencia WHERE procedencia_extraccion->'ampliacion_leyenda'->>'huella'=%s",
        (huella,),
    ).fetchone()
    if previo:
        return str(previo["reporte_asistencia_id"])
    version = conn.execute(
        """SELECT COALESCE(max(version),0)+1 n FROM reporte_asistencia
        WHERE reporte_asistencia_serie_id=%s OR (documento_recibido_id=%s AND hoja_pagina_origen=%s AND indice_bloque=%s)""",
        (
            fuente["reporte_asistencia_serie_id"],
            fuente["documento_recibido_id"],
            fuente["hoja_pagina_origen"],
            fuente["indice_bloque"],
        ),
    ).fetchone()["n"]
    meta = {
        **fuente["procedencia_extraccion"],
        "ampliacion_leyenda": {
            "huella": huella,
            "padre": str(rid),
            "autor": "AGENTE_TECNICO",
            "motivo": motivo,
        },
    }
    nuevo = _copiar(
        conn,
        "reporte_asistencia",
        "reporte_asistencia_id",
        fuente,
        {
            "version": version,
            "version_padre_id": rid,
            "estado": "EN_VALIDACION",
            "validado_por": None,
            "validado_en": None,
            "procedencia_extraccion": meta,
            "clasificacion_codigos": categorias,
        },
    )
    personas = {}
    for persona in conn.execute(
        "SELECT * FROM trabajador_en_reporte WHERE reporte_asistencia_id=%s ORDER BY fila_detalle_origen",
        (rid,),
    ).fetchall():
        anterior = persona["trabajador_en_reporte_id"]
        tid = _copiar(
            conn,
            "trabajador_en_reporte",
            "trabajador_en_reporte_id",
            persona,
            {"reporte_asistencia_id": nuevo},
        )
        personas[anterior] = tid
        for dia in conn.execute(
            "SELECT * FROM asistencia_dia WHERE trabajador_en_reporte_id=%s ORDER BY fecha",
            (anterior,),
        ).fetchall():
            if dia["validado"] and not dia["evidencia_interpretacion"].get(
                "clasificacion_aceptada"
            ):
                cat = conn.execute(
                    "SELECT codigo,nombre FROM catalogo_estado_asistencia WHERE estado_asistencia_id=%s",
                    (dia["estado_asistencia_id"],),
                ).fetchone()
                if cat:
                    dia["codigo_interpretado"] = cat["codigo"]
                    dia["evidencia_interpretacion"] = {
                        **dia["evidencia_interpretacion"],
                        "clasificacion_aceptada": {
                            **interpretar_descripcion(cat["nombre"], "asistencia"),
                            "fuente": "CORRECCION_VERSION_PADRE",
                            "version_padre": str(rid),
                        },
                    }
                else:
                    dia["evidencia_interpretacion"] = {
                        **dia["evidencia_interpretacion"],
                        "dato_no_determinado": True,
                    }
            _copiar(
                conn,
                "asistencia_dia",
                "asistencia_dia_id",
                dia,
                {"trabajador_en_reporte_id": tid},
            )
        for resumen in conn.execute(
            "SELECT * FROM resumen_asistencia_reportado WHERE trabajador_en_reporte_id=%s",
            (anterior,),
        ).fetchall():
            _copiar(
                conn,
                "resumen_asistencia_reportado",
                "resumen_asistencia_reportado_id",
                resumen,
                {"trabajador_en_reporte_id": tid},
            )
    for alerta in conn.execute(
        "SELECT * FROM validacion_reporte WHERE reporte_asistencia_id=%s", (rid,)
    ).fetchall():
        _copiar(
            conn,
            "validacion_reporte",
            "validacion_reporte_id",
            alerta,
            {
                "reporte_asistencia_id": nuevo,
                "trabajador_en_reporte_id": personas.get(
                    alerta["trabajador_en_reporte_id"]
                ),
                "evidencia": {
                    **alerta["evidencia"],
                    "alerta_version_padre": str(alerta["validacion_reporte_id"]),
                },
            },
        )
    aplicar_leyenda_asistencia(conn, nuevo, categorias, usar_catalogo=usar_catalogo)
    interpretar_feriado(conn, nuevo, ws, dias_grid)
    return str(nuevo)


def releer_leyenda_excel(conn, rid, hoja, motivo, uid):
    """Relectura explícita y versionada; el llamador mantiene bloqueo de escritura.

    Conserva reglas ya aplicadas cuyo significado no cambió, revisiones locales
    y anotaciones ajenas al bloque nativo. No aprueba el reporte ni sus salidas.
    """
    import openpyxl

    from asistia.categorias import fuente_original
    from asistia.db import sha256_de
    from asistia.leyendas import leer_leyenda_xlsx, normal, normalizar_leyenda
    from asistia.web.revision import registrar

    if not motivo.strip():
        raise ValueError("La relectura requiere un motivo.")
    fuente = conn.execute(
        """SELECT r.*,o.ruta_objeto,o.sha256 FROM reporte_asistencia r
        JOIN documento_recibido d USING(documento_recibido_id)
        JOIN objeto_archivo o USING(objeto_archivo_id)
        WHERE reporte_asistencia_id=%s FOR UPDATE OF r""",
        (rid,),
    ).fetchone()
    if not fuente:
        raise ValueError("No existe el reporte fuente.")
    ruta = Path(fuente["ruta_objeto"])
    if (
        ruta.suffix.lower() not in {".xlsx", ".xlsm"}
        or sha256_de(ruta) != fuente["sha256"]
    ):
        raise ValueError("El original debe ser un Excel con hash verificado.")
    antes = fuente["clasificacion_codigos"]
    libro = openpyxl.load_workbook(ruta, data_only=True)
    try:
        if hoja not in libro.sheetnames:
            raise ValueError("La relectura requiere el nombre exacto de la hoja.")
        entradas = leer_leyenda_xlsx(libro[hoja])
    finally:
        libro.close()
    despues = normalizar_leyenda(entradas, "asistencia")
    if not despues:
        raise ValueError("No se encontró una leyenda nativa; se conserva el reporte.")
    for codigo, c in antes.items():
        original = fuente_original(c)
        misma_descripcion = codigo in despues and normal(
            original.get("tipo_dia")
        ) == normal(despues[codigo]["tipo_dia"])
        evidencia = original.get("evidencia", {})
        fuera_del_bloque = (
            original.get("fuente") != "LEYENDA_DOCUMENTAL"
            or evidencia.get("hoja") != hoja
            or not evidencia.get("celda")
        )
        if (
            c.get("fuente") == "REVISION_WEB"
            or fuera_del_bloque
            or (misma_descripcion and not despues[codigo].get("revision_leyenda"))
        ):
            despues[codigo] = c
    control = {
        "version_lector": "LEYENDA_XLSX_BLOQUES_2",
        "padre": str(rid),
        "sha256": fuente["sha256"],
        "hoja": hoja,
        "motivo": motivo,
        "autor": uid,
        "retirados": sorted(set(antes) - set(despues)),
        "entradas": entradas,
    }
    previo = conn.execute(
        "SELECT reporte_asistencia_id FROM reporte_asistencia WHERE procedencia_extraccion->'relectura_leyenda'=%s",
        (Jsonb(control),),
    ).fetchone()
    if previo:
        return str(previo["reporte_asistencia_id"])
    if conn.execute(
        "SELECT 1 FROM reporte_asistencia WHERE reporte_asistencia_serie_id=%s AND version>%s",
        (fuente["reporte_asistencia_serie_id"], fuente["version"]),
    ).fetchone():
        raise ValueError("Hay una versión posterior. Relee el reporte actual.")
    if despues == antes:
        return str(rid)
    nuevo = ampliar_leyenda_reporte(conn, rid, despues, motivo, usar_catalogo=False)
    # Una marca que perdiera su categoría tampoco puede conservar una conclusión
    # automática anterior. Las correcciones diarias explícitas prevalecen.
    conn.execute(
        """UPDATE asistencia_dia a SET estado_asistencia_id=NULL,estado_captura='PENDIENTE',hecho_asistencia_dia_id=NULL
        FROM trabajador_en_reporte t WHERE a.trabajador_en_reporte_id=t.trabajador_en_reporte_id
        AND t.reporte_asistencia_id=%s AND COALESCE(a.codigo_interpretado,a.codigo_reportado_raw)=ANY(%s)
        AND NOT a.validado AND NOT (a.evidencia_interpretacion ? 'clasificacion_aceptada')""",
        (nuevo, control["retirados"]),
    )
    conn.execute(
        "UPDATE reporte_asistencia SET procedencia_extraccion=procedencia_extraccion||%s WHERE reporte_asistencia_id=%s",
        (Jsonb({"relectura_leyenda": control}), nuevo),
    )
    registrar(
        conn,
        uuid4(),
        nuevo,
        "RELEER_LEYENDA_EXCEL",
        motivo,
        {"reporte": str(rid), "clasificacion_codigos": antes},
        {"control": control, "clasificacion_codigos": despues},
        uid,
    )
    return nuevo
