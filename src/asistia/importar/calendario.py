"""Importador de calendarizaciones: Excel nativo y PDF nativo (familia UGEL Luya 2026).

Fuera de alcance: PDF escaneado/foto (necesita OCR o vision, no evidencia de que esos formatos
predominen -- medido en la sesion: de 130 PDF de calendarizacion reales, una muestra de 20 dio
55% con texto nativo extraible y 45% probablemente escaneados). `.docx` tampoco se soporta.

Identifica la institucion por contenido del encabezado (nombre + nivel), nunca por lo que declare
quien carga el archivo -- mismo principio que ADR-016 de v1 (ver `asistia.identificacion`, modulo
compartido con el importador de asistencia).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import openpyxl
import psycopg
import pypdf

from asistia.db import registrar_documento
from asistia.identificacion import MESES as _MESES
from asistia.identificacion import resolver_institucion
from asistia.leyendas import (
    aplicar_leyenda_calendario,
    leer_leyenda_pdf,
    leer_leyenda_xlsx,
    normalizar_leyenda,
)

_FAMILIA_FORMATO_DEFECTO = "UGEL_LUYA_CALENDARIO_2026"

_MESES_EN_ORDEN = [
    "ENERO",
    "FEBRERO",
    "MARZO",
    "ABRIL",
    "MAYO",
    "JUNIO",
    "JULIO",
    "AGOSTO",
    "SEPTIEMBRE",
    "OCTUBRE",
    "NOVIEMBRE",
    "DICIEMBRE",
]
_NIVELES_CONOCIDOS = ("SECUNDARIA", "PRIMARIA", "INICIAL")


@dataclass
class ResultadoImportacionCalendario:
    calendarizacion_version_id: str | None = None
    dias_registrados: int = 0
    dias_vacios: int = 0
    dias_codigo_desconocido: int = 0
    dias_duplicados_en_grilla: int = 0
    meses_sin_alinear: list[str] | None = None
    error: str | None = None


def _extraer_anio(texto: str) -> int | None:
    m = re.search(r"\b(20\d{2})\b", texto or "")
    return int(m.group(1)) if m else None


def _preparar_version(
    cur: psycopg.Cursor,
    ruta: Path,
    mime_type: str,
    nombre_ie_raw: str | None,
    nivel_raw: str | None,
    anio: int | None,
) -> tuple[str, str] | tuple[None, None]:
    """Resuelve institucion, registra procedencia, y crea la calendarizacion_version BORRADOR.
    Devuelve (calendarizacion_version_id, error) -- exactamente uno de los dos es None."""
    if anio is None or not nombre_ie_raw:
        return None, f"{ruta.name}: no se pudo leer año/institución del encabezado"

    institucion_educativa_id = resolver_institucion(
        cur, str(nombre_ie_raw), str(nivel_raw or "")
    )
    if institucion_educativa_id is None:
        return None, (
            f"{ruta.name}: no se pudo identificar la institución de forma única "
            f"(nombre_ie={nombre_ie_raw!r}, nivel={nivel_raw!r})"
        )

    documento_recibido_id = registrar_documento(cur, ruta, mime_type)

    cur.execute(
        "SELECT calendarizacion_local_id FROM calendarizacion_local "
        "WHERE institucion_educativa_id = %s AND anio = %s",
        (institucion_educativa_id, anio),
    )
    fila = cur.fetchone()
    if fila:
        calendarizacion_local_id = fila["calendarizacion_local_id"]
    else:
        cur.execute(
            "INSERT INTO calendarizacion_local (institucion_educativa_id, anio) "
            "VALUES (%s, %s) RETURNING calendarizacion_local_id",
            (institucion_educativa_id, anio),
        )
        calendarizacion_local_id = cur.fetchone()["calendarizacion_local_id"]

    cur.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM calendarizacion_version "
        "WHERE calendarizacion_local_id = %s",
        (calendarizacion_local_id,),
    )
    version = cur.fetchone()["v"]

    # BORRADOR a proposito: promover a VIGENTE es una decision humana explicita (exige
    # aprobado_por/aprobado_en, CHECK de calendarizacion_version), no un efecto de importar.
    cur.execute(
        """
        INSERT INTO calendarizacion_version
            (calendarizacion_local_id, version, documento_recibido_id, origen, estado, motivo_version)
        VALUES (%s, %s, %s, 'IMPORTACION_IE', 'BORRADOR', %s)
        RETURNING calendarizacion_version_id
        """,
        (
            calendarizacion_local_id,
            version,
            documento_recibido_id,
            f"Importado de {ruta.name}",
        ),
    )
    return cur.fetchone()["calendarizacion_version_id"], None


def _escribir_dia(
    cur: psycopg.Cursor,
    calendarizacion_version_id: str,
    familia_formato: str,
    fecha: date,
    codigo_raw: str | None,
    hoja_origen: str,
    celda_origen: str,
    resultado: ResultadoImportacionCalendario,
) -> None:
    if codigo_raw is None or not str(codigo_raw).strip():
        cur.execute(
            """
            INSERT INTO dia_calendarizacion
                (calendarizacion_version_id, fecha, estado_captura, hoja_origen, celda_origen)
            VALUES (%s, %s, 'VACIO', %s, %s)
            ON CONFLICT (calendarizacion_version_id, fecha) DO NOTHING
            """,
            (calendarizacion_version_id, fecha, hoja_origen, celda_origen),
        )
        if cur.rowcount:
            resultado.dias_vacios += 1
        else:
            resultado.dias_duplicados_en_grilla += 1
        return

    cur.execute(
        "SELECT tipo_dia_id FROM catalogo_codigo_tipo_dia_fuente "
        "WHERE familia_formato = %s AND codigo_raw = %s AND vigente_hasta IS NULL",
        (familia_formato, str(codigo_raw).strip()),
    )
    tipo_fila = cur.fetchone()
    if tipo_fila:
        cur.execute(
            """
            INSERT INTO dia_calendarizacion
                (calendarizacion_version_id, fecha, tipo_dia_id, codigo_reportado_raw,
                 estado_captura, hoja_origen, celda_origen)
            VALUES (%s, %s, %s, %s, 'REGISTRADO', %s, %s)
            ON CONFLICT (calendarizacion_version_id, fecha) DO NOTHING
            """,
            (
                calendarizacion_version_id,
                fecha,
                tipo_fila["tipo_dia_id"],
                str(codigo_raw).strip(),
                hoja_origen,
                celda_origen,
            ),
        )
        if cur.rowcount:
            resultado.dias_registrados += 1
        else:
            resultado.dias_duplicados_en_grilla += 1
    else:
        cur.execute(
            """
            INSERT INTO dia_calendarizacion
                (calendarizacion_version_id, fecha, codigo_reportado_raw, estado_captura,
                 hoja_origen, celda_origen)
            VALUES (%s, %s, %s, 'CODIGO_DESCONOCIDO', %s, %s)
            ON CONFLICT (calendarizacion_version_id, fecha) DO NOTHING
            """,
            (
                calendarizacion_version_id,
                fecha,
                str(codigo_raw).strip(),
                hoja_origen,
                celda_origen,
            ),
        )
        if cur.rowcount:
            resultado.dias_codigo_desconocido += 1
        else:
            resultado.dias_duplicados_en_grilla += 1


# ============================================================
# Excel nativo
# ============================================================


def _valor_a_la_derecha(ws, fila: int, col: int, max_dist: int = 8):
    for dc in range(1, max_dist + 1):
        v = ws.cell(row=fila, column=col + dc).value
        if v is not None and str(v).strip():
            return v
    return None


def _leer_encabezado_xlsx(ws) -> tuple[str | None, str | None, str | None]:
    titulo = nombre_ie_raw = nivel_raw = None
    for row in ws.iter_rows(min_row=1, max_row=10):
        for c in row:
            if not isinstance(c.value, str):
                continue
            texto = c.value.strip().upper()
            if "CALENDARIZACI" in texto and "AÑO ESCOLAR" in texto:
                titulo = c.value
            elif texto.rstrip(":") == "NOMBRE DE LA IE":
                nombre_ie_raw = _valor_a_la_derecha(ws, c.row, c.column)
            elif texto.rstrip(":") == "NIVEL O CICLO":
                nivel_raw = _valor_a_la_derecha(ws, c.row, c.column)
    return titulo, nombre_ie_raw, nivel_raw


def _bloques_mes_xlsx(ws) -> list[tuple[int, int, int]]:
    """(mes_numero, fila_fecha, fila_tipo_de_dia) para cada bloque de mes en la columna A."""
    bloques = []
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=1):
        v = row[0].value
        if isinstance(v, str) and v.strip().upper() in _MESES:
            fila = row[0].row
            bloques.append((_MESES[v.strip().upper()], fila, fila + 1))
    return bloques


def _importar_xlsx(
    conn: psycopg.Connection, ruta: Path, familia_formato: str
) -> ResultadoImportacionCalendario:
    resultado = ResultadoImportacionCalendario()
    wb = openpyxl.load_workbook(ruta, data_only=True)
    hoja = "CAL-2025" if "CAL-2025" in wb.sheetnames else wb.sheetnames[-1]
    ws = wb[hoja]

    titulo, nombre_ie_raw, nivel_raw = _leer_encabezado_xlsx(ws)
    anio = _extraer_anio(titulo or "")

    with conn.cursor() as cur:
        calendarizacion_version_id, error = _preparar_version(
            cur,
            ruta,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            nombre_ie_raw,
            nivel_raw,
            anio,
        )
        if error:
            resultado.error = error
            return resultado
        resultado.calendarizacion_version_id = calendarizacion_version_id

        # La cuadricula "Semana 6" de este archivo real repite numeros de dia de semanas
        # anteriores del mismo mes en columnas sueltas (verificado: nunca con un codigo propio,
        # su celda "Tipo de dia" siempre esta vacia) -- procesar de izquierda a derecha hace que
        # la celda real (con codigo) se inserte primero y el ON CONFLICT descarte la decorativa
        # sin perder ni inventar nada. dias_duplicados_en_grilla cuenta esos descartes.
        for mes_num, fila_fecha, fila_tipo in _bloques_mes_xlsx(ws):
            for celda_fecha in ws[fila_fecha]:
                dia = celda_fecha.value
                if (
                    isinstance(dia, bool)
                    or not isinstance(dia, (int, float))
                    or not (1 <= dia <= 31)
                    or int(dia) != dia
                ):
                    continue
                dia = int(dia)
                try:
                    fecha = date(anio, mes_num, dia)
                except ValueError:
                    continue
                celda_tipo = ws.cell(row=fila_tipo, column=celda_fecha.column)
                _escribir_dia(
                    cur,
                    calendarizacion_version_id,
                    familia_formato,
                    fecha,
                    celda_tipo.value,
                    hoja,
                    celda_tipo.coordinate,
                    resultado,
                )

    categorias = normalizar_leyenda(
        leer_leyenda_xlsx(ws, control_alineacion=False), "calendario"
    )
    aplicar_leyenda_calendario(conn, calendarizacion_version_id, categorias)
    conteos = conn.execute(
        """SELECT count(*) FILTER(WHERE estado_captura='REGISTRADO') registrados,
        count(*) FILTER(WHERE estado_captura='VACIO') vacios,
        count(*) FILTER(WHERE estado_captura='CODIGO_DESCONOCIDO') desconocidos
        FROM dia_calendarizacion WHERE calendarizacion_version_id=%s""",
        (calendarizacion_version_id,),
    ).fetchone()
    resultado.dias_registrados = conteos["registrados"]
    resultado.dias_vacios = conteos["vacios"]
    resultado.dias_codigo_desconocido = conteos["desconocidos"]
    conn.commit()
    return resultado


# ============================================================
# PDF nativo
# ============================================================


def _paginas_texto(ruta: Path) -> list[str]:
    lector = pypdf.PdfReader(str(ruta))
    return [pagina.extract_text() or "" for pagina in lector.pages]


def _localizar_pagina_grilla(
    paginas: list[str],
) -> tuple[int, list[str]] | tuple[None, None]:
    """La grilla de calendario puede no estar en la primera pagina -- un PDF de calendarizacion
    real suele traer antes un proveido de tramite documentario y/o el oficio de la IE (mismo
    hallazgo que ADR-018 de v1 documenta para asistencia: "la primera pagina suele ser un oficio
    de remision, no el anexo"). Se identifica la pagina correcta por contenido: la que tiene la
    fila "Fecha 1 2 3 ..." de enero, no por posicion."""
    for i, texto in enumerate(paginas):
        lineas = texto.split("\n")
        for linea in lineas:
            if re.match(r"^Fecha\s+1\s+2\s+3\b", linea.strip()):
                return i, lineas
    return None, None


def _leer_encabezado_pdf(
    lineas: list[str],
) -> tuple[str | None, str | None, str | None]:
    titulo = nombre_ie_raw = nivel_raw = None
    for linea in lineas:
        texto = linea.strip()
        if not texto:
            continue
        texto_up = texto.upper()
        if titulo is None and "CALENDARIZACI" in texto_up and "AÑO ESCOLAR" in texto_up:
            titulo = texto
        m = re.search(
            r"Nombre de la IE:\s*(.+?)(?:\s+Modelo de servicio:|$)",
            texto,
            re.IGNORECASE,
        )
        if m and nombre_ie_raw is None:
            nombre_ie_raw = m.group(1).strip()
        # El valor de "Nivel o Ciclo" no queda adyacente a su etiqueta en el texto plano extraido
        # (verificado en dos PDF reales distintos, con posiciones distintas) -- se busca en cambio
        # cualquier linea que sea, ella sola, un nivel conocido ("Inicial", "Secundaria o
        # Avanzado", etc.), que si aparece consistente en ambos casos reales.
        if nivel_raw is None and texto_up.startswith(_NIVELES_CONOCIDOS):
            nivel_raw = texto
    return titulo, nombre_ie_raw, nivel_raw


def _tokens_mes(
    lineas: list[str], indice_fecha_enero: int
) -> list[tuple[list[str], list[str]]]:
    """12 pares (fecha_tokens, tipo_tokens), en orden Enero->Diciembre, a partir de la linea de
    Fecha de enero -- en los dos PDF reales verificados, los 12 pares de Fecha/Tipo de dia
    aparecen consecutivos y en orden, sin el nombre del mes intercalado (a diferencia del Excel)."""
    pares = []
    for i in range(12):
        idx_fecha = indice_fecha_enero + 2 * i
        idx_tipo = idx_fecha + 1
        if idx_tipo >= len(lineas):
            break
        fecha_tokens = lineas[idx_fecha].replace("Fecha", "", 1).split()
        tipo_tokens = lineas[idx_tipo].replace("Tipo de día", "", 1).split()
        pares.append((fecha_tokens, tipo_tokens))
    return pares


def _alinear_fecha_tipo(
    fecha_tokens: list[str], tipo_tokens: list[str]
) -> list[tuple[str, str | None]] | None:
    """Empareja por indice, no por conteo de dias validos -- verificado contra dos PDF reales que
    esto es necesario: una celda de "Fecha" fuera de rango (p. ej. un 'D' suelto heredado del mes
    anterior) y su celda "Tipo de dia" correspondiente comparten la misma posicion, no se
    desalinean entre si. Devuelve None cuando el desfase no coincide con ninguno de los dos
    patrones ya verificados -- no se adivina, ver ResultadoImportacionCalendario.meses_sin_alinear.
    """
    n_fecha = len(fecha_tokens)
    n_tipo = len(tipo_tokens)
    if n_tipo == 0:
        return [(tok, None) for tok in fecha_tokens]
    if n_tipo == n_fecha:
        return list(zip(fecha_tokens, tipo_tokens, strict=True))
    if n_tipo == n_fecha + 1:
        # verificado en Membrillo y Cristobal Benque: marzo trae un codigo extra al final, sin
        # dia correspondiente (probable clasificacion de abril 1 heredada visualmente).
        return list(zip(fecha_tokens, tipo_tokens[:n_fecha], strict=True))
    return None


def _importar_pdf(
    conn: psycopg.Connection, ruta: Path, familia_formato: str
) -> ResultadoImportacionCalendario:
    resultado = ResultadoImportacionCalendario()
    paginas = _paginas_texto(ruta)
    idx_pagina, lineas = _localizar_pagina_grilla(paginas)
    if idx_pagina is None:
        resultado.error = (
            f"{ruta.name}: no se encontró una página con la grilla de calendario"
        )
        return resultado

    titulo, nombre_ie_raw, nivel_raw = _leer_encabezado_pdf(lineas)
    anio = _extraer_anio(titulo or "")
    indice_fecha_enero = next(
        i
        for i, linea in enumerate(lineas)
        if re.match(r"^Fecha\s+1\s+2\s+3\b", linea.strip())
    )

    with conn.cursor() as cur:
        calendarizacion_version_id, error = _preparar_version(
            cur, ruta, "application/pdf", nombre_ie_raw, nivel_raw, anio
        )
        if error:
            resultado.error = error
            return resultado
        resultado.calendarizacion_version_id = calendarizacion_version_id

        meses_sin_alinear = []
        for mes_idx, (fecha_tokens, tipo_tokens) in enumerate(
            _tokens_mes(lineas, indice_fecha_enero)
        ):
            mes_num = mes_idx + 1
            pares = _alinear_fecha_tipo(fecha_tokens, tipo_tokens)
            if pares is None:
                meses_sin_alinear.append(_MESES_EN_ORDEN[mes_idx])
                continue
            for pos, (dia_tok, codigo_raw) in enumerate(pares):
                if not dia_tok.isdigit() or not (1 <= int(dia_tok) <= 31):
                    continue
                try:
                    fecha = date(anio, mes_num, int(dia_tok))
                except ValueError:
                    continue
                celda_origen = f"pagina{idx_pagina + 1}:mes{mes_num}:pos{pos}"
                _escribir_dia(
                    cur,
                    calendarizacion_version_id,
                    familia_formato,
                    fecha,
                    codigo_raw,
                    f"pagina {idx_pagina + 1}",
                    celda_origen,
                    resultado,
                )
        resultado.meses_sin_alinear = meses_sin_alinear or None

    aplicar_leyenda_calendario(
        conn,
        calendarizacion_version_id,
        normalizar_leyenda(leer_leyenda_pdf(ruta), "calendario"),
    )
    conteos = conn.execute(
        """SELECT count(*) FILTER(WHERE estado_captura='REGISTRADO') registrados,
        count(*) FILTER(WHERE estado_captura='VACIO') vacios,
        count(*) FILTER(WHERE estado_captura='CODIGO_DESCONOCIDO') desconocidos
        FROM dia_calendarizacion WHERE calendarizacion_version_id=%s""",
        (calendarizacion_version_id,),
    ).fetchone()
    resultado.dias_registrados = conteos["registrados"]
    resultado.dias_vacios = conteos["vacios"]
    resultado.dias_codigo_desconocido = conteos["desconocidos"]
    conn.commit()
    return resultado


# ============================================================
# Punto de entrada
# ============================================================


def importar_calendario(
    conn: psycopg.Connection,
    ruta: Path,
    familia_formato: str = _FAMILIA_FORMATO_DEFECTO,
) -> ResultadoImportacionCalendario:
    sufijo = ruta.suffix.lower()
    if sufijo == ".xlsx":
        return _importar_xlsx(conn, ruta, familia_formato)
    if sufijo == ".pdf":
        return _importar_pdf(conn, ruta, familia_formato)
    resultado = ResultadoImportacionCalendario()
    resultado.error = (
        f"{ruta.name}: formato no soportado ({sufijo}) -- solo .xlsx y .pdf nativo"
    )
    return resultado
