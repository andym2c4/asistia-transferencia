"""Interpretación técnica por fuente; originales y versiones anteriores permanecen intactos."""

import hashlib
import json
from datetime import UTC, datetime

from psycopg.types.json import Jsonb


def ajustar_calendario(conn, version_id, clasificaciones, motivo, fechas=None):
    if not motivo:
        raise ValueError("El ajuste requiere evidencia y motivo.")
    fechas = fechas or {}
    fuente = conn.execute(
        "SELECT * FROM calendarizacion_version WHERE calendarizacion_version_id=%s FOR UPDATE",
        (version_id,),
    ).fetchone()
    if not fuente:
        raise ValueError("No existe el calendario fuente.")
    tipos = {
        r["codigo_interno"]: r["tipo_dia_id"]
        for r in conn.execute("SELECT * FROM catalogo_tipo_dia WHERE activo").fetchall()
    }
    if any(
        t not in tipos
        for t in [*clasificaciones.values(), *[v["tipo"] for v in fechas.values()]]
    ):
        raise ValueError("Tipo de actividad no reconocido.")
    decision = {
        "version_fuente": str(version_id),
        "clasificaciones": clasificaciones,
        "fechas": fechas,
        "motivo": motivo,
        "autor": "AGENTE_TECNICO",
    }
    huella = hashlib.sha256(json.dumps(decision, sort_keys=True).encode()).hexdigest()
    anterior = conn.execute(
        "SELECT calendarizacion_version_id FROM calendarizacion_version WHERE procedencia_extraccion->'ajuste_documental'->>'huella'=%s",
        (huella,),
    ).fetchone()
    if anterior:
        return str(anterior["calendarizacion_version_id"])
    dias = conn.execute(
        "SELECT * FROM dia_calendarizacion WHERE calendarizacion_version_id=%s ORDER BY fecha",
        (version_id,),
    ).fetchall()
    if set(fechas) - {str(d["fecha"]) for d in dias}:
        raise ValueError("Una fecha corregida no existe en la versión fuente.")
    meta = dict(fuente["procedencia_extraccion"])
    meta["supuestos"] = [
        *meta.get("supuestos", []),
        "Interpretación técnica por fuente: " + motivo,
    ]
    meta["ajuste_documental"] = {
        **decision,
        "huella": huella,
        "fecha": datetime.now(UTC).isoformat(),
    }
    version = conn.execute(
        "SELECT max(version)+1 n FROM calendarizacion_version WHERE calendarizacion_local_id=%s",
        (fuente["calendarizacion_local_id"],),
    ).fetchone()["n"]
    nuevo = conn.execute(
        """INSERT INTO calendarizacion_version(calendarizacion_local_id,version,version_padre_id,
        documento_recibido_id,fuente_derivada_id,origen,motivo_version,procedencia_extraccion)
        VALUES(%s,%s,%s,%s,%s,'CREACION_MANUAL',%s,%s) RETURNING calendarizacion_version_id""",
        (
            fuente["calendarizacion_local_id"],
            version,
            version_id,
            fuente["documento_recibido_id"],
            fuente["fuente_derivada_id"],
            motivo,
            Jsonb(meta),
        ),
    ).fetchone()["calendarizacion_version_id"]
    conn.execute(
        "UPDATE calendarizacion_version SET clasificacion_codigos=%s WHERE calendarizacion_version_id=%s",
        (Jsonb(fuente["clasificacion_codigos"]), nuevo),
    )
    for d in dias:
        observacion = d["observacion"]
        correccion = fechas.get(str(d["fecha"]))
        tipo = (
            correccion["tipo"]
            if correccion
            else clasificaciones.get(d["codigo_reportado_raw"])
        )
        if tipo:
            observacion = (
                "Interpretación técnica: "
                + (
                    json.dumps(correccion, ensure_ascii=False)
                    if correccion
                    else f"{d['codigo_reportado_raw']} = {tipo}"
                )
                + ". "
                + motivo
            )
        conn.execute(
            """INSERT INTO dia_calendarizacion(calendarizacion_version_id,fecha,tipo_dia_id,
            codigo_reportado_raw,estado_captura,hoja_origen,celda_origen,observacion,modelo_extraccion,version_extraccion)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                nuevo,
                d["fecha"],
                tipos[tipo] if tipo else d["tipo_dia_id"],
                d["codigo_reportado_raw"],
                "REGISTRADO" if tipo else d["estado_captura"],
                d["hoja_origen"],
                d["celda_origen"],
                observacion,
                d["modelo_extraccion"],
                d["version_extraccion"],
            ),
        )
    conn.execute(
        """UPDATE dia_calendarizacion n SET codigo_interpretado=d.codigo_interpretado, evidencia_interpretacion=d.evidencia_interpretacion
        FROM dia_calendarizacion d WHERE n.calendarizacion_version_id=%s AND d.calendarizacion_version_id=%s AND n.fecha=d.fecha""",
        (nuevo, version_id),
    )
    return str(nuevo)
