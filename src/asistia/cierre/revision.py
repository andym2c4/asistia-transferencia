"""Revisión técnica reproducible. Un supuesto se conserva; no se convierte en aprobación humana."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

from psycopg.types.json import Jsonb

from asistia.consolidado.universo_esperado import calendario_aplicable
from asistia.leyendas import categoria_dia
from asistia.web.lecturas import huella_reporte

from .ingesta import SALIDA, escribir_json, indice_personas, normal


def proyectar_calendarios(conn, anio=2026):
    fuentes = conn.execute(
        """SELECT DISTINCT ON (cl.institucion_educativa_id) cv.*,cl.institucion_educativa_id
      FROM calendarizacion_local cl JOIN calendarizacion_version cv USING(calendarizacion_local_id)
      WHERE cl.anio=%s AND cv.estado NOT IN ('RECHAZADA','HISTORICA')
      ORDER BY cl.institucion_educativa_id,(cv.estado='VIGENTE') DESC,cv.version DESC""",
        (anio - 1,),
    ).fetchall()
    resultado = []
    for fuente in fuentes:
        iid = fuente["institucion_educativa_id"]
        # Fuentes 2026 tienen prioridad, incluso un borrador: nunca se disfraza la proyección como original.
        actual = conn.execute(
            """SELECT cv.calendarizacion_version_id FROM calendarizacion_local cl
          JOIN calendarizacion_version cv USING(calendarizacion_local_id)
          WHERE cl.institucion_educativa_id=%s AND cl.anio=%s
          AND cv.estado NOT IN ('RECHAZADA','HISTORICA') LIMIT 1""",
            (iid, anio),
        ).fetchone()
        if actual:
            continue
        dias = conn.execute(
            "SELECT * FROM dia_calendarizacion WHERE calendarizacion_version_id=%s ORDER BY fecha",
            (fuente["calendarizacion_version_id"],),
        ).fetchall()
        if not dias:
            continue
        local = conn.execute(
            """INSERT INTO calendarizacion_local(institucion_educativa_id,anio) VALUES(%s,%s)
          ON CONFLICT(institucion_educativa_id,anio) DO UPDATE SET anio=EXCLUDED.anio RETURNING calendarizacion_local_id""",
            (iid, anio),
        ).fetchone()["calendarizacion_local_id"]
        version = conn.execute(
            "SELECT COALESCE(max(version),0)+1 AS n FROM calendarizacion_version WHERE calendarizacion_local_id=%s",
            (local,),
        ).fetchone()["n"]
        meta = {
            "tipo": "DERIVADO_2025",
            "anio_fuente": anio - 1,
            "anio_destino": anio,
            "fuente_version_id": str(fuente["calendarizacion_version_id"]),
            "regla": "Mismo mes y día; conservar código original. No desplazar por día de semana.",
            "autor": "AGENTE_TECNICO",
            "advertencia": "Basado en 2025. No acredita el calendario real de 2026; días esperados solo estimados.",
        }
        cvid = conn.execute(
            """INSERT INTO calendarizacion_version(calendarizacion_local_id,version,fuente_derivada_id,
          documento_recibido_id,origen,motivo_version,procedencia_extraccion)
          VALUES(%s,%s,%s,%s,'CREACION_MANUAL',%s,%s) RETURNING calendarizacion_version_id""",
            (
                local,
                version,
                fuente["calendarizacion_version_id"],
                fuente["documento_recibido_id"],
                f"Basado en {anio - 1}; proyección técnica a {anio}, pendiente de calendario del año",
                Jsonb(meta),
            ),
        ).fetchone()["calendarizacion_version_id"]
        conn.execute(
            "UPDATE calendarizacion_version SET clasificacion_codigos=%s WHERE calendarizacion_version_id=%s",
            (Jsonb(fuente["clasificacion_codigos"]), cvid),
        )
        for d in dias:
            try:
                fecha = d["fecha"].replace(year=anio)
            except ValueError:
                continue
            conn.execute(
                """INSERT INTO dia_calendarizacion(calendarizacion_version_id,fecha,tipo_dia_id,
              codigo_reportado_raw,estado_captura,hoja_origen,celda_origen)
              VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (
                    cvid,
                    fecha,
                    d["tipo_dia_id"],
                    d["codigo_reportado_raw"],
                    d["estado_captura"],
                    d["hoja_origen"],
                    d["celda_origen"],
                ),
            )
        conn.execute(
            """UPDATE dia_calendarizacion n SET codigo_interpretado=d.codigo_interpretado,evidencia_interpretacion=d.evidencia_interpretacion
            FROM dia_calendarizacion d WHERE n.calendarizacion_version_id=%s AND d.calendarizacion_version_id=%s
            AND extract(month FROM n.fecha)=extract(month FROM d.fecha) AND extract(day FROM n.fecha)=extract(day FROM d.fecha)""",
            (cvid, fuente["calendarizacion_version_id"]),
        )
        resultado.append(
            {"institucion_id": iid, "calendarizacion_version_id": str(cvid), **meta}
        )
    conn.commit()
    acumulado = conn.execute(
        """SELECT cl.institucion_educativa_id AS institucion_id,
      cv.calendarizacion_version_id,cv.procedencia_extraccion AS procedencia
      FROM calendarizacion_local cl JOIN calendarizacion_version cv USING(calendarizacion_local_id)
      WHERE cl.anio=%s AND cv.procedencia_extraccion->>'tipo'='DERIVADO_2025'
      ORDER BY cl.institucion_educativa_id,cv.version""",
        (anio,),
    ).fetchall()
    escribir_json(SALIDA / "calendarios_proyectados.json", acumulado)
    return resultado


def resolver_identidades_tecnicas(conn, periodo):
    indice = indice_personas(conn)
    filas = conn.execute(
        """SELECT t.*,r.institucion_educativa_id,r.documento_recibido_id
      FROM trabajador_en_reporte t JOIN reporte_asistencia r USING(reporte_asistencia_id)
      WHERE r.periodo=%s AND t.trabajador_id IS NULL AND r.estado IN ('IMPORTADO','EN_VALIDACION')
      ORDER BY t.trabajador_en_reporte_id""",
        (periodo,),
    ).fetchall()
    cambios = []
    for fila in filas:
        candidatos = indice.get(normal(fila["nombres_reportados_raw"]), set())
        if len(candidatos) != 1:
            continue
        tid = next(iter(candidatos))
        vinculos = conn.execute(
            """SELECT * FROM vinculo_trabajador_ie
          WHERE trabajador_id=%s AND institucion_educativa_id=%s
          AND (fecha_inicio IS NULL OR fecha_inicio < (%s::date+interval '1 month'))
          AND (fecha_fin IS NULL OR fecha_fin >= %s)""",
            (tid, fila["institucion_educativa_id"], periodo, periodo),
        ).fetchall()
        if len(vinculos) != 1:
            continue
        v = vinculos[0]
        meta = {
            "accion": "IDENTIDAD_RESUELTA",
            "regla": "Nombre completo normalizado exacto y único; único vínculo compatible con institución/mes",
            "trabajador_en_reporte_id": str(fila["trabajador_en_reporte_id"]),
            "trabajador_id": tid,
            "vinculo_id": v["vinculo_trabajador_ie_id"],
            "autor": "AGENTE_TECNICO",
            "supuesto": "El nombre completo del reporte corresponde a la única persona del padrón con ese nombre y vínculo.",
        }
        conn.execute(
            """UPDATE trabajador_en_reporte SET trabajador_id=%s,rol_laboral_id=%s,
          vinculo_trabajador_ie_id=%s,estado_match='RESUELTO',metodo_match='NOMBRE_EXACTO_TECNICO'
          WHERE trabajador_en_reporte_id=%s""",
            (
                tid,
                v["rol_laboral_id"],
                v["vinculo_trabajador_ie_id"],
                fila["trabajador_en_reporte_id"],
            ),
        )
        conn.execute(
            "UPDATE asistencia_dia SET hecho_asistencia_dia_id=NULL WHERE trabajador_en_reporte_id=%s",
            (fila["trabajador_en_reporte_id"],),
        )
        conn.execute(
            """UPDATE reporte_asistencia SET procedencia_extraccion=procedencia_extraccion ||
          jsonb_build_object('resoluciones_tecnicas',COALESCE(procedencia_extraccion->'resoluciones_tecnicas','[]'::jsonb)||%s::jsonb)
          WHERE reporte_asistencia_id=%s""",
            (Jsonb([meta]), fila["reporte_asistencia_id"]),
        )
        cambios.append(meta)
    conn.commit()
    return cambios


def resolver_roles_tecnicos(conn, periodo):
    from asistia.importar.asistencia import rol_por_texto

    roles = {
        r["codigo"]: r["rol_laboral_id"]
        for r in conn.execute("SELECT * FROM catalogo_rol_laboral").fetchall()
    }
    cambios = []
    for t in conn.execute(
        """SELECT t.* FROM trabajador_en_reporte t JOIN reporte_asistencia r USING(reporte_asistencia_id)
        WHERE r.periodo=%s AND r.estado IN ('IMPORTADO','EN_VALIDACION')
        AND t.trabajador_id IS NOT NULL AND t.rol_laboral_id IS NULL""",
        (periodo,),
    ).fetchall():
        codigo = rol_por_texto(t["cargo_reportado_raw"], t["condicion_reportada_raw"])
        if not codigo:
            continue
        tid = t["trabajador_en_reporte_id"]
        conn.execute(
            "UPDATE trabajador_en_reporte SET rol_laboral_id=%s,estado_match='RESUELTO',metodo_match='ROL_TEXTO_TECNICO' WHERE trabajador_en_reporte_id=%s",
            (roles[codigo], tid),
        )
        v = conn.execute(
            "SELECT fn_resolver_vinculo_por_reporte(%s) v", (tid,)
        ).fetchone()["v"]
        if v:
            conn.execute(
                "UPDATE trabajador_en_reporte SET vinculo_trabajador_ie_id=%s WHERE trabajador_en_reporte_id=%s",
                (v, tid),
            )
        conn.execute(
            "UPDATE asistencia_dia SET hecho_asistencia_dia_id=NULL WHERE trabajador_en_reporte_id=%s",
            (tid,),
        )
        meta = {
            "accion": "ROL_RESUELTO",
            "trabajador_en_reporte_id": str(tid),
            "rol": codigo,
            "cargo_recibido": t["cargo_reportado_raw"],
            "condicion_recibida": t["condicion_reportada_raw"],
            "autor": "AGENTE_TECNICO",
            "regla": "Cargo o condición explícitos; abreviatura inequívoca. No aprobación de RRHH.",
        }
        conn.execute(
            """UPDATE reporte_asistencia SET procedencia_extraccion=procedencia_extraccion||jsonb_build_object(
            'resoluciones_tecnicas',COALESCE(procedencia_extraccion->'resoluciones_tecnicas','[]'::jsonb)||%s::jsonb)
            WHERE reporte_asistencia_id=%s""",
            (Jsonb([meta]), t["reporte_asistencia_id"]),
        )
        cambios.append(meta)
    conn.commit()
    return cambios


def revisar_instituciones(conn, periodo=date(2026, 7, 1), evidencia_dir=None):
    destino = Path(evidencia_dir) if evidencia_dir else SALIDA
    cambios = resolver_identidades_tecnicas(conn, periodo)
    cambios_roles = resolver_roles_tecnicos(conn, periodo)
    instituciones = conn.execute(
        "SELECT *,fn_nivel_canonico(nivel_modalidad) AS nivel FROM institucion_educativa ORDER BY cod_mod,anexo"
    ).fetchall()
    nomina_sin_reporte = {}
    for v in conn.execute(
        """WITH ultimos AS (
        SELECT DISTINCT ON(COALESCE(reporte_asistencia_serie_id::text,reporte_asistencia_id::text)) reporte_asistencia_id
        FROM reporte_asistencia WHERE periodo=%s AND estado NOT IN ('RECHAZADO','HISTORICA')
        ORDER BY COALESCE(reporte_asistencia_serie_id::text,reporte_asistencia_id::text),version DESC)
        SELECT v.institucion_educativa_id,v.vinculo_trabajador_ie_id,v.trabajador_id
        FROM vinculo_trabajador_ie v WHERE v.trabajador_id IS NOT NULL
          AND (v.fecha_inicio IS NULL OR v.fecha_inicio < %s::date+interval '1 month')
          AND (v.fecha_fin IS NULL OR v.fecha_fin >= %s)
          AND NOT EXISTS(SELECT 1 FROM trabajador_en_reporte t JOIN ultimos u USING(reporte_asistencia_id)
                         WHERE t.vinculo_trabajador_ie_id=v.vinculo_trabajador_ie_id)
          AND EXISTS(SELECT 1 FROM generate_series(%s::date,(%s::date+interval '1 month'-interval '1 day')::date,interval '1 day') f
                     WHERE fn_vinculo_presencia_esperada(v.vinculo_trabajador_ie_id,f::date))""",
        (periodo, periodo, periodo, periodo, periodo),
    ).fetchall():
        nomina_sin_reporte.setdefault(v["institucion_educativa_id"], []).append(v)
    salida = []
    for ie in instituciones:
        iid = ie["institucion_educativa_id"]
        reportes = conn.execute(
            """SELECT DISTINCT ON(COALESCE(reporte_asistencia_serie_id::text,reporte_asistencia_id::text)) *
          FROM reporte_asistencia WHERE institucion_educativa_id=%s AND periodo=%s AND estado NOT IN ('RECHAZADO','HISTORICA')
          ORDER BY COALESCE(reporte_asistencia_serie_id::text,reporte_asistencia_id::text),version DESC""",
            (iid, periodo),
        ).fetchall()
        fuentes = []
        problemas = []
        supuestos = []
        remuneracion_pendiente = {"calendario": set(), "asistencia": set()}
        if not ie["nivel"]:
            problemas.append("Nivel educativo no aplicable o sin identificar")
        if not reportes:
            problemas.append(
                "No se encontró reporte diario de este mes entre las fuentes procesadas"
            )
        if nomina_sin_reporte.get(iid):
            problemas.append(
                f"{len(nomina_sin_reporte[iid])} vínculos con presencia esperada según padrón no tienen fila en los reportes aplicables; verificar continuidad o documento faltante"
            )
        identidades = ilegibles = vacios = criticos = filas_total = 0
        for r in reportes:
            rid = r["reporte_asistencia_id"]
            conteos = conn.execute(
                """SELECT count(*) AS filas,count(*) FILTER(WHERE trabajador_id IS NULL OR rol_laboral_id IS NULL) AS identidades
              FROM trabajador_en_reporte WHERE reporte_asistencia_id=%s""",
                (rid,),
            ).fetchone()
            dias = conn.execute(
                """SELECT count(*) FILTER(WHERE a.estado_captura='VACIO') AS vacios,
              count(*) FILTER(WHERE a.estado_captura IN ('ILEGIBLE','PENDIENTE')) AS ilegibles
              FROM asistencia_dia a JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id)
              WHERE t.reporte_asistencia_id=%s""",
                (rid,),
            ).fetchone()
            alertas = conn.execute(
                "SELECT codigo_regla,severidad,count(*) AS n FROM validacion_reporte WHERE reporte_asistencia_id=%s AND estado='PENDIENTE' GROUP BY codigo_regla,severidad",
                (rid,),
            ).fetchall()
            identidades += conteos["identidades"]
            filas_total += conteos["filas"]
            vacios += dias["vacios"]
            ilegibles += dias["ilegibles"]
            criticos += sum(a["n"] for a in alertas if a["severidad"] == "ERROR")
            fuentes.append(
                {
                    "reporte_id": str(rid),
                    "version": r["version"],
                    "huella": huella_reporte(conn, rid),
                    "filas": conteos,
                    "dias": dias,
                    "alertas": alertas,
                    "procedencia": r["procedencia_extraccion"],
                }
            )
            supuestos.extend(r["procedencia_extraccion"].get("supuestos", []))
            for dia in conn.execute(
                """SELECT DISTINCT a.codigo_reportado_raw,a.codigo_interpretado,a.evidencia_interpretacion
                FROM asistencia_dia a JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id)
                WHERE t.reporte_asistencia_id=%s""",
                (rid,),
            ).fetchall():
                codigo = dia["codigo_interpretado"] or dia["codigo_reportado_raw"]
                if codigo and categoria_dia(r, dia).get("es_remunerado") is None:
                    remuneracion_pendiente["asistencia"].add(codigo)
        if identidades:
            problemas.append(f"{identidades} filas sin identidad/rol suficiente")
        if ilegibles:
            problemas.append(f"{ilegibles} marcas ilegibles o pendientes")
        if criticos:
            problemas.append(
                f"{criticos} observaciones críticas requieren evidencia adicional"
            )
        with conn.cursor() as cur:
            cvid, cestado = calendario_aplicable(cur, iid, periodo.year)
        cal = None
        esperados_vacios = None
        if cvid:
            cal = conn.execute(
                "SELECT procedencia_extraccion,documento_recibido_id,version,clasificacion_codigos FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
                (cvid,),
            ).fetchone()
            cal = {"id": str(cvid), "estado": cestado, **cal}
            supuestos.extend(cal["procedencia_extraccion"].get("supuestos", []))
            faltantes = conn.execute(
                """SELECT count(*) AS n FROM generate_series(%s::date,(%s::date+interval '1 month' - interval '1 day')::date,interval '1 day') f
              LEFT JOIN dia_calendarizacion d ON d.calendarizacion_version_id=%s AND d.fecha=f::date
              WHERE d.tipo_dia_id IS NULL""",
                (periodo, periodo, cvid),
            ).fetchone()["n"]
            cal["dias_mes_sin_determinar"] = faltantes
            for dia in conn.execute(
                """SELECT DISTINCT codigo_reportado_raw,codigo_interpretado,evidencia_interpretacion
                FROM dia_calendarizacion WHERE calendarizacion_version_id=%s AND date_trunc('month',fecha)=%s""",
                (cvid, periodo),
            ).fetchall():
                codigo = dia["codigo_interpretado"] or dia["codigo_reportado_raw"]
                if codigo and categoria_dia(cal, dia).get("es_remunerado") is None:
                    remuneracion_pendiente["calendario"].add(codigo)
            if faltantes:
                problemas.append(
                    f"{faltantes} días del mes sin actividad determinada en calendario"
                )
            if cal["procedencia_extraccion"].get("tipo") == "DERIVADO_2025":
                supuestos.append(
                    "Calendario basado en 2025, no adecuado para certificar 2026"
                )
            if reportes:
                esperados_vacios = conn.execute(
                    """SELECT count(*) AS n FROM asistencia_dia a JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id)
                  JOIN dia_calendarizacion d ON d.fecha=a.fecha AND d.calendarizacion_version_id=%s
                  JOIN catalogo_tipo_dia c ON c.tipo_dia_id=d.tipo_dia_id
                  WHERE t.reporte_asistencia_id=ANY(%s) AND a.estado_captura='VACIO' AND c.codigo_interno IN ('LECTIVO','GESTION')
                  AND (t.vinculo_trabajador_ie_id IS NULL OR fn_vinculo_presencia_esperada(t.vinculo_trabajador_ie_id,a.fecha))""",
                    (cvid, [r["reporte_asistencia_id"] for r in reportes]),
                ).fetchone()["n"]
                if esperados_vacios:
                    problemas.append(
                        f"{esperados_vacios} marcas vacías en días esperados"
                    )
        else:
            problemas.append("Sin calendario disponible para el año")
        remuneracion_pendiente = {
            dominio: sorted(codigos)
            for dominio, codigos in remuneracion_pendiente.items()
        }
        for dominio, codigos in remuneracion_pendiente.items():
            if codigos:
                problemas.append(
                    f"Definir con sustento la remuneración de {len(codigos)} categorías de {dominio}: "
                    + ", ".join(codigos)
                )
        evidencia_dre = conn.execute(
            "SELECT count(*) AS n,count(DISTINCT trabajador_id) AS personas FROM padron_evidencia WHERE institucion_educativa_id=%s",
            (iid,),
        ).fetchone()
        result = (
            "REQUIERE_DATOS"
            if problemas
            else ("CON_SUPUESTOS" if supuestos else "LISTA_TECNICAMENTE")
        )
        detalle = {
            "fuentes": fuentes,
            "calendario": cal,
            "problemas": problemas,
            "supuestos": supuestos,
            "padron_dre": evidencia_dre,
            "vinculos_sin_reporte": nomina_sin_reporte.get(iid, []),
            "filas": filas_total,
            "sin_identidad": identidades,
            "vacios": vacios,
            "ilegibles": ilegibles,
            "criticos": criticos,
            "vacios_en_dias_esperados": esperados_vacios,
            "remuneracion_pendiente": remuneracion_pendiente,
            "alcance": "Revisión técnica automática de las fuentes disponibles; no aprobación de RRHH.",
            "siguiente_accion": problemas[0]
            if problemas
            else "Cotejar la salida y confirmar con RRHH",
        }
        huella = hashlib.sha256(
            json.dumps(detalle, sort_keys=True, default=str).encode()
        ).hexdigest()
        conn.execute(
            """INSERT INTO cierre_revision_institucion(institucion_educativa_id,periodo,huella,resultado,detalle)
          VALUES(%s,%s,%s,%s,%s) ON CONFLICT(institucion_educativa_id,periodo,huella) DO NOTHING""",
            (
                iid,
                periodo,
                huella,
                result,
                Jsonb(json.loads(json.dumps(detalle, default=str))),
            ),
        )
        salida.append(
            {
                "institucion_id": iid,
                "cod_mod": ie["cod_mod"],
                "anexo": ie["anexo"],
                "nombre": ie["nombre_ie"],
                "nivel": ie["nivel"] or ie["nivel_modalidad"],
                "periodo": str(periodo),
                "resultado": result,
                **detalle,
            }
        )
    conn.commit()
    escribir_json(
        destino / f"revision_instituciones_{periodo:%Y-%m}.json",
        {
            "instituciones": salida,
            "resoluciones_identidad": cambios,
            "resoluciones_roles": cambios_roles,
            "por_resultado": dict(Counter(r["resultado"] for r in salida)),
        },
    )
    ruta = destino / f"revision_instituciones_{periodo:%Y-%m}.csv"
    with ruta.open("w") as f:
        ruta.chmod(0o600)
        campos = [
            "cod_mod",
            "anexo",
            "nombre",
            "nivel",
            "periodo",
            "resultado",
            "filas",
            "sin_identidad",
            "vacios",
            "ilegibles",
            "criticos",
            "siguiente_accion",
        ]
        escritor = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        escritor.writeheader()
        escritor.writerows(salida)
    return {
        "instituciones": len(salida),
        "resoluciones_identidad": len(cambios),
        "resoluciones_roles": len(cambios_roles),
        "por_resultado": dict(Counter(r["resultado"] for r in salida)),
    }
