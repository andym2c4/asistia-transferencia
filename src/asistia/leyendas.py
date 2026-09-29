"""Significado local de códigos: fuente, actividad y remuneración independientes.

La descripción recibida es la categoría; los catálogos antiguos solo son una
agrupación de compatibilidad. Una letra nunca basta para decidir remuneración.
"""

import re

from psycopg.types.json import Jsonb

from asistia.identificacion import sin_acentos


def normal(valor):
    return re.sub(r"\s+", " ", sin_acentos(str(valor or "")).upper()).strip()


def interpretar_descripcion(descripcion, dominio):
    t = normal(descripcion)
    pago = None
    if re.search(r"\b(SIN GOCE|NO REMUNERAD[OA]S?)\b", t):
        pago = False
    elif re.search(r"\b(CON GOCE|REMUNERAD[OA]S?)\b", t):
        pago = True
    grupo = estado = None
    falta = None
    if dominio == "calendario":
        if re.search(r"NO LECTIV|NO LABOR|FERIAD|DESCANS|VACACION|FIN DE SEMANA", t):
            grupo = "NO_LECTIVO_NI_GESTION"
        elif "GESTION" in t:
            grupo = "GESTION"
        elif re.search(r"LECTIV|CLASES|UNIDAD DE APRENDIZAJE", t):
            grupo = "LECTIVO"
    else:
        if "TERCERA TARDANZA" in t and "INASISTENCIA INJUSTIFICADA" in t:
            estado, falta, pago = "3T", True, False
        elif "INASISTENCIA INJUSTIFICADA" in t or t == "FALTA INJUSTIFICADA":
            estado, falta, pago = "I", True, False
        elif "INASISTENCIA JUSTIFICADA" in t:
            estado, falta = "J", True
        elif "LICENCIA" in t:
            estado, falta = (
                ("L" if pago is True else "LSG" if pago is False else None),
                False,
            )
        elif "PERMISO" in t:
            estado, falta = (
                ("P" if pago is False else "PCG" if pago is True else None),
                False,
            )
        elif re.fullmatch(
            r"(?:DIA |DIAS )?(?:LABORADO[S]?|TRABAJADO[S]?|ASISTENCIA)", t
        ):
            estado, falta = "A", False
        elif "FERIADO" in t:
            estado, falta = "F", False
        elif "TARDANZA" in t:
            estado, falta = "T", False
        elif "COMISION" in t:
            estado, falta = "C", False
        elif "CAPACITACION" in t:
            estado, falta = "U", False
        elif t in {"HUELGA", "PARO", "HUELGA/PARO", "HUELGA O PARO"}:
            estado = "H"
        estado = estado or "OTRO_REPORTADO"
    return {
        "tipo_dia": str(descripcion).strip(),
        "es_remunerado": pago,
        "grupo_actividad": grupo,
        "estado_asistencia_codigo": estado,
        "es_falta": falta,
    }


def normalizar_leyenda(entradas, dominio):
    """Solo texto explícito determina pago; un booleano sugerido por OCR no basta."""
    salida = {}
    for entrada in entradas or []:
        codigo = str(entrada.get("codigo") or "").strip()
        descripcion = entrada.get("descripcion")
        if (
            not codigo
            or len(codigo) > 60
            or not isinstance(descripcion, str)
            or not descripcion.strip()
        ):
            raise ValueError(
                "Cada código de leyenda necesita código y descripción de la fuente."
            )
        valor = {
            **interpretar_descripcion(descripcion, dominio),
            "fuente": "LEYENDA_DOCUMENTAL",
            "evidencia": entrada,
        }
        if entrada.get("revision_leyenda"):
            from asistia.calidad_leyendas import suspender

            valor = suspender(valor, entrada["revision_leyenda"])
        if codigo in salida and (
            salida[codigo].get("conflictos")
            or salida[codigo]["tipo_dia"] != valor["tipo_dia"]
        ):
            conflictos = salida[codigo].get("conflictos", [salida[codigo]["evidencia"]])
            if entrada not in conflictos:
                conflictos.append(entrada)
            revision = valor.get("revision_leyenda") or salida[codigo].get(
                "revision_leyenda"
            )
            salida[codigo] = {
                "tipo_dia": "Código con significados contradictorios",
                "es_remunerado": None,
                "es_falta": None,
                "grupo_actividad": None,
                "estado_asistencia_codigo": None,
                "fuente": "LEYENDA_EN_CONFLICTO",
                "conflictos": conflictos,
                "evidencia": {"alternativas": conflictos},
            }
            if revision:
                from asistia.calidad_leyendas import suspender

                salida[codigo] = suspender(salida[codigo], revision)
            continue
        salida[codigo] = valor
    return salida


def leer_leyenda_xlsx(ws, *, conocidas=(), control_alineacion=True):
    """Pares explícitos por bloque/columna; una firma lateral no es una leyenda.

    Un rótulo abre las columnas a dos celdas de distancia. Cada par prolonga únicamente
    su columna hasta una fila vacía; sin rótulo se exige significado reconocible.
    Se conservan códigos numéricos cortos, textos locales y celdas originales.
    """
    entradas = []
    columnas = {}
    corto = re.compile(r"^[A-Za-z0-9./+-]{1,12}$")
    rotulo = re.compile(r"^\s*LEYENDA\s*[:=–-]?\s*", re.IGNORECASE)
    metadatos = {
        "DNI",
        "D.N.I.",
        "RUC",
        "TEL",
        "TELEFONO",
        "CELULAR",
        "CORREO",
        "EMAIL",
        "FECHA",
        "FIRMA",
    }
    conocidas = {normal(t) for t in conocidas}
    for row in ws.iter_rows(
        max_row=min(ws.max_row or 1500, 1500), max_col=min(ws.max_column or 120, 120)
    ):
        celdas = [c for c in row if c.value not in (None, "")]
        for celda in celdas:
            if rotulo.match(str(celda.value)):
                for col in range(max(1, celda.column - 2), celda.column + 3):
                    columnas[col] = celda.row
        for idx, celda in enumerate(celdas):
            texto = rotulo.sub("", str(celda.value)).strip()
            match = re.fullmatch(r"([A-Za-z0-9./+-]{1,12})\s*[:=–-]\s*(.{5,})", texto)
            codigo, descripcion, localizador = None, None, celda.coordinate
            if match:
                codigo, descripcion = match.groups()
            elif (
                len(texto) <= 4
                and corto.fullmatch(texto)
                and idx + 1 < len(celdas)
                and celdas[idx + 1].column - celda.column <= 5
            ):
                siguiente = str(celdas[idx + 1].value).strip()
                if len(siguiente) >= 5:
                    codigo, descripcion = texto, siguiente
                    localizador += ":" + celdas[idx + 1].coordinate
            if (
                not codigo
                or normal(codigo) in metadatos
                or (codigo.isdecimal() and len(codigo) > 4)
                or not any(c.isalpha() for c in descripcion)
                or "LEYENDA" in normal(descripcion)
            ):
                continue
            en_bloque = celda.row - columnas.get(celda.column, -1500) <= 2
            if not (
                en_bloque
                or descripcion_de_categoria(descripcion)
                or normal(descripcion) in conocidas
            ):
                continue
            entrada = {
                "codigo": codigo,
                "descripcion": descripcion,
                "hoja": ws.title,
                "celda": localizador,
            }
            if entrada not in entradas:
                entradas.append(entrada)
            if en_bloque:
                columnas[celda.column] = celda.row
    from asistia.calidad_leyendas import detectar_desfases, marcar_entradas

    return (
        marcar_entradas(entradas, detectar_desfases(ws, conocidas))
        if control_alineacion
        else entradas
    )


def descripcion_de_categoria(texto):
    """Admite tablas de equivalencias sin el rótulo LEYENDA, no una fila nominal."""
    return bool(
        re.match(
            r"^(?:DIA\b|DIAS\b|LECTIV|GESTION\b|SABADOS?\b|DOMINGOS?\b|FERIAD|VACACION|LICENCIA\b|PERMISO\b|INASISTENCIA\b|ASISTENCIA\b|TARDANZAS?\b|TERCERA TARDANZA\b|HUELGA\b|COMISION\b|CAPACITACION\b|UNIDAD DE APRENDIZAJE\b|SEMANA DE GESTION\b)",
            normal(texto),
        )
    )


def leer_leyenda_pdf(ruta):
    """Leyendas con texto digital y columnas separadas; escaneos siguen por OCR."""
    from pypdf import PdfReader

    entradas = []
    for n, pagina in enumerate(PdfReader(ruta).pages, 1):
        activa = False
        for linea in (pagina.extract_text(extraction_mode="layout") or "").splitlines():
            if "LEYENDA" in normal(linea):
                activa = True
                continue
            m = re.match(
                r"^\s*([A-Za-z0-9./+-]{1,4})(?:\s{2,}|\s*[:=]\s*)(\S.{4,})$", linea
            )
            if not m:
                continue
            codigo, descripcion = m.groups()
            # Una firma en otra columna no forma parte del significado de la leyenda.
            descripcion = re.split(r"\s{8,}", descripcion)[0].strip()
            if not activa and not descripcion_de_categoria(descripcion):
                continue
            entradas.append(
                {
                    "codigo": codigo,
                    "descripcion": descripcion,
                    "pagina": n,
                    "linea": linea.strip(),
                }
            )
    return entradas


CLASIFICACION_SIN_ASIGNAR = {
    "tipo_dia": "Sin asignar",
    "grupo_actividad": None,
    "es_remunerado": None,
}


def aplicar_leyenda_calendario(conn, cvid, categorias, *, usar_catalogo=True):
    from asistia.categorias import resolver_entrada

    if usar_catalogo:
        categorias = resolver_entrada(conn, "calendario", categorias)
    conn.execute(
        "UPDATE calendarizacion_version SET clasificacion_codigos=%s WHERE calendarizacion_version_id=%s",
        (Jsonb(categorias), cvid),
    )
    for codigo, c in categorias.items():
        tipo = conn.execute(
            "SELECT tipo_dia_id FROM catalogo_tipo_dia WHERE codigo_interno=%s",
            (c.get("grupo_actividad"),),
        ).fetchone()
        conn.execute(
            """UPDATE dia_calendarizacion SET tipo_dia_id=%s,estado_captura=%s
            WHERE calendarizacion_version_id=%s AND COALESCE(codigo_interpretado,codigo_reportado_raw)=%s
            AND estado_captura<>'SIN_ASIGNAR'
            AND NOT (evidencia_interpretacion ? 'clasificacion_aceptada')""",
            (
                tipo["tipo_dia_id"] if tipo else None,
                "REGISTRADO" if tipo else "CODIGO_DESCONOCIDO",
                cvid,
                codigo,
            ),
        )


def aplicar_leyenda_asistencia(conn, rid, categorias, *, usar_catalogo=True):
    from asistia.calidad_leyendas import registrar_alerta
    from asistia.categorias import resolver_entrada

    if usar_catalogo:
        categorias = resolver_entrada(conn, "asistencia", categorias)
    conn.execute(
        "UPDATE reporte_asistencia SET clasificacion_codigos=%s WHERE reporte_asistencia_id=%s",
        (Jsonb(categorias), rid),
    )
    for codigo, c in categorias.items():
        estado = conn.execute(
            "SELECT estado_asistencia_id FROM catalogo_estado_asistencia WHERE codigo=%s",
            (c.get("estado_asistencia_codigo"),),
        ).fetchone()
        conn.execute(
            """UPDATE asistencia_dia a SET estado_asistencia_id=%s,estado_captura=%s,hecho_asistencia_dia_id=NULL
            FROM trabajador_en_reporte t WHERE a.trabajador_en_reporte_id=t.trabajador_en_reporte_id
            AND t.reporte_asistencia_id=%s AND COALESCE(a.codigo_interpretado,a.codigo_reportado_raw)=%s
            AND NOT a.validado AND NOT (a.evidencia_interpretacion ? 'clasificacion_aceptada')""",
            (
                estado["estado_asistencia_id"] if estado else None,
                "REGISTRADO" if estado else "PENDIENTE",
                rid,
                codigo,
            ),
        )
    registrar_alerta(conn, rid, categorias)


def interpretar_feriado(conn, rid, ws=None, dias_grid=None):
    """FERIADO completo en una columna o celda combinada; nunca una F o I aislada."""
    filas = conn.execute(
        """SELECT a.*,t.fila_detalle_origen FROM asistencia_dia a
        JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id)
        WHERE t.reporte_asistencia_id=%s ORDER BY a.fecha,t.fila_detalle_origen""",
        (rid,),
    ).fetchall()
    grupos = {}
    for f in filas:
        origen = (
            (f["celda_origen"] or "").split(";fila:")[0]
            if ";fila:" in (f["celda_origen"] or "")
            else "hoja"
        )
        grupos.setdefault((f["fecha"], origen), []).append(f)
    aceptados = {}
    for (fecha, origen), dias in grupos.items():
        for i in range(len(dias) - 6):
            tramo = dias[i : i + 7]
            if [normal(d["codigo_reportado_raw"]) for d in tramo] == list("FERIADO"):
                evidencia = {
                    "regla": "FERIADO_VERTICAL_1",
                    "texto": "FERIADO",
                    "fecha": str(fecha),
                    "origen": origen,
                    "celdas": [d["celda_origen"] for d in tramo],
                }
                for d in tramo:
                    aceptados[d["asistencia_dia_id"]] = evidencia
        if ws is not None:
            columna = dict(dias_grid or []).get(fecha.day)
            for rango in ws.merged_cells.ranges:
                if rango.min_col != columna or rango.max_col != columna:
                    continue
                if (
                    re.sub(r"\s+", "", normal(ws.cell(rango.min_row, columna).value))
                    != "FERIADO"
                ):
                    continue
                for d in dias:
                    if rango.min_row <= d["fila_detalle_origen"] <= rango.max_row:
                        aceptados[d["asistencia_dia_id"]] = {
                            "regla": "FERIADO_CELDA_COMBINADA_1",
                            "texto": "FERIADO",
                            "hoja": ws.title,
                            "rango": str(rango),
                        }
    if aceptados:
        eid = conn.execute(
            "SELECT estado_asistencia_id FROM catalogo_estado_asistencia WHERE codigo='F'"
        ).fetchone()["estado_asistencia_id"]
        for did, evidencia in aceptados.items():
            conn.execute(
                """UPDATE asistencia_dia SET codigo_interpretado='FERIADO',evidencia_interpretacion=%s,
                estado_asistencia_id=%s,estado_captura='REGISTRADO',hecho_asistencia_dia_id=NULL
                WHERE asistencia_dia_id=%s AND NOT validado""",
                (Jsonb(evidencia), eid, did),
            )
        categoria = {
            **interpretar_descripcion("Feriado", "asistencia"),
            "fuente": "ANOTACION_COMPARTIDA",
            "evidencia": {"regla": "FERIADO_VERTICAL_O_COMBINADO_1"},
        }
        conn.execute(
            "UPDATE reporte_asistencia SET clasificacion_codigos=jsonb_build_object('FERIADO',%s::jsonb)||clasificacion_codigos WHERE reporte_asistencia_id=%s",
            (Jsonb(categoria), rid),
        )
    return len(aceptados)


def codigos_usados_asistencia(dias):
    """Marcas aceptadas/por leer; vacío y no aplica no activan leyendas impresas."""
    return {
        d.get("codigo_interpretado") or d.get("codigo_reportado_raw")
        for d in dias
        if d.get("estado_captura") not in {"VACIO", "NO_APLICA"}
        and (d.get("codigo_interpretado") or d.get("codigo_reportado_raw"))
    }


def categoria_dia(documento, dia):
    if dia.get("estado_captura") == "SIN_ASIGNAR":
        return dict(CLASIFICACION_SIN_ASIGNAR)
    if dia.get("evidencia_interpretacion", {}).get("clasificacion_aceptada"):
        return dia["evidencia_interpretacion"]["clasificacion_aceptada"]
    codigo = dia.get("codigo_interpretado") or dia.get("codigo_reportado_raw")
    return documento.get("clasificacion_codigos", {}).get(codigo, {})


def finalizar_asistencia(conn, rid, categorias, ws=None, dias_grid=None):
    """Resolver antes de materializar hechos: las letras de FERIADO no crean faltas transitorias."""
    conn.execute(
        "UPDATE reporte_asistencia SET clasificacion_codigos=%s WHERE reporte_asistencia_id=%s",
        (Jsonb(categorias), rid),
    )
    interpretar_feriado(conn, rid, ws, dias_grid)
    categorias = conn.execute(
        "SELECT clasificacion_codigos FROM reporte_asistencia WHERE reporte_asistencia_id=%s",
        (rid,),
    ).fetchone()["clasificacion_codigos"]
    aplicar_leyenda_asistencia(conn, rid, categorias)
    # Compatibilidad de captura para fuentes antiguas sin leyenda. No asigna remuneración.
    conn.execute(
        """UPDATE asistencia_dia a SET estado_asistencia_id=c.estado_asistencia_id,
        estado_captura='REGISTRADO',hecho_asistencia_dia_id=NULL
        FROM trabajador_en_reporte t,catalogo_estado_asistencia c
        WHERE a.trabajador_en_reporte_id=t.trabajador_en_reporte_id AND t.reporte_asistencia_id=%s
        AND a.codigo_interpretado IS NULL AND a.codigo_reportado_raw=c.codigo
        AND NOT (%s::jsonb ? a.codigo_reportado_raw)""",
        (rid, Jsonb(categorias)),
    )
