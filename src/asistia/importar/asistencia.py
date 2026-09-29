"""Importador de reportes de asistencia ANEXO 3/4 (Excel nativo).

Alcance de este primer corte (medido contra los 318 archivos reales de julio 2026 en
`ASISTENCIAS JULIO 2026`: 261 xlsx / 82%, 56 pdf / 18%, 1 docx): solo Excel, un solo periodo
(mes/anio) por archivo -- no se detecto en los archivos reales usados para construir esto un mismo
libro con mas de un mes.

Un mismo archivo si puede producir mas de un `reporte_asistencia` cuando combina niveles en un
solo ANEXO 3 -- caso real Tactamal (IEPySM, "Nivel/Modalidad Educativa: PRIMARIA - SECUNDARIA/EBR"
en el encabezado), donde primaria y secundaria son 2 `institucion_educativa` distintas pero se
reportan en la misma hoja. Ver `_nivel_efectivo_fila`/`_obtener_o_crear_reporte`; usa la columna
Especialidad/Area para separar personas por nivel cuando su valor coincide con un nivel conocido,
y `reporte_asistencia.indice_bloque` (ya previsto por ADR-018 de v1 para "varios bloques en el
mismo libro") para distinguir los reportes resultantes dentro del mismo documento.

ANEXO 3 manda sobre ANEXO 4 cuando ambos existen (mismo criterio que ADR-019 de v1): las marcas
diarias salen siempre de la grilla de ANEXO 3; ANEXO 4 se importa como resumen de contraste
(`resumen_asistencia_reportado`), no se usa para derivar marcas en este corte -- la derivacion de
ADR-019 (cuando solo existe ANEXO 4) queda fuera de alcance, declarada, no silenciosa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import openpyxl
import psycopg

from asistia.db import registrar_documento
from asistia.identificacion import MESES, NIVELES_CONOCIDOS, resolver_institucion
from asistia.identificacion import sin_acentos as _sin_acentos
from asistia.leyendas import finalizar_asistencia, leer_leyenda_xlsx, normalizar_leyenda

_CAMPOS_ANEXO3 = {
    "N°": "numero",
    "DNI": "dni",
    "APELLIDOS Y NOMBRES": "nombres",
    "CARGO": "cargo",
    "ESPECIALIDAD": "especialidad",
    "ÁREA": "especialidad",
    "CONDICIÓN": "condicion",
    "N° TELÉFONO": "telefono",
    "TELÉFONO": "telefono",
    "CORREO ELECTRÓNICO": "correo",
    "CORREO ELECTÓONICO": "correo",  # typo real visto en Tactamal
    "CORREO": "correo",
    "JOR. LAB.": "jornada",
}

_CARGO_DOCENTE = ("DOCENTE", "PROFESOR", "DIRECTOR", "AUXILIAR")


@dataclass
class ResultadoImportacionAsistencia:
    # nivel efectivo (ver _nivel_efectivo_fila) -> reporte_asistencia_id. Casi siempre 1 entrada;
    # 2 cuando el archivo combina niveles en un solo ANEXO 3 (caso real Tactamal, IEPySM).
    reportes_asistencia_id: dict[str, str] = field(default_factory=dict)
    total_trabajadores: int = 0
    resueltos: int = 0
    pendientes: int = 0
    alertas_vinculo_automatico: int = 0
    error: str | None = None
    errores_fila: list[str] = field(default_factory=list)


def _valor_a_la_derecha(ws, fila: int, col: int, max_dist: int = 10):
    for dc in range(1, max_dist + 1):
        v = ws.cell(row=fila, column=col + dc).value
        if v is not None and str(v).strip():
            return v
    return None


def _leer_encabezado(ws) -> dict[str, str | None]:
    campos = {"institucion": None, "periodo": None, "nivel": None, "turno": None}
    etiquetas = {
        "I.E": "institucion",
        "I.E:": "institucion",
        "PERIODO(MES/AÑO)": "periodo",
        "PERIODO (MES/AÑO)": "periodo",
        "NIVEL/MODALIDAD EDUCATIVA": "nivel",
        "TURNO": "turno",
    }
    for row in ws.iter_rows(min_row=1, max_row=10):
        for c in row:
            if not isinstance(c.value, str):
                continue
            texto = re.sub(r"\s+", " ", c.value.strip().upper()).rstrip(":")
            campo = etiquetas.get(texto)
            if campo and campos[campo] is None:
                campos[campo] = _valor_a_la_derecha(ws, c.row, c.column)
    return campos


def _parsear_periodo(valor: date | str | None) -> date | None:
    if valor is None:
        return None
    # celda con una fecha real de Excel (caso real: "MES" en SANTOTOMAS trae un datetime, no
    # texto con el nombre del mes) -- se toma el anio/mes directo, sin pasar por el regex de abajo
    # (que busca el NOMBRE del mes en texto y nunca lo encontraria en "2026-07-01 00:00:00").
    if isinstance(valor, date):
        return date(valor.year, valor.month, 1)
    texto = valor
    if not texto:
        return None
    # colapsa fragmentos de un mismo numero separados por espacio (visto en archivo real:
    # "JULIO - 2 026") antes de buscar el anio.
    limpio = re.sub(r"(?<=\d)\s+(?=\d)", "", str(texto))
    limpio_up = limpio.upper()
    anio_m = re.search(r"\b(20\d{2})\b", limpio_up)
    if not anio_m:
        return None
    anio = int(anio_m.group(1))
    for nombre_mes, num in MESES.items():
        if nombre_mes in limpio_up:
            return date(anio, num, 1)
    return None


def _buscar_periodo_en_encabezado(ws) -> date | None:
    """Respaldo cuando no se encuentra un valor de periodo junto a la etiqueta "Periodo(mes/año):"
    (caso real, mayoria del corpus: la plantilla no trae esa etiqueta en absoluto -- el DRE/UGEL o
    el I.E. traen el periodo como un valor suelto mas adelante en la misma fila, o esta en una
    celda de fecha real sin ningun rotulo cerca). Escanea las filas del encabezado buscando
    cualquier celda cuyo propio texto/valor ya resuelva un periodo valido (mismo _parsear_periodo,
    no una regla nueva) y solo lo acepta si TODAS las celdas que calzan concuerdan en el mismo mes
    y anio -- con una sola celda rescatable, o varias que dicen lo mismo, no hay nada que adivinar;
    con candidatos que discrepan, se deja sin resolver en vez de elegir uno a ciegas."""
    candidatos: set[date] = set()
    for row in ws.iter_rows(min_row=1, max_row=12):
        for c in row:
            periodo = _parsear_periodo(c.value)
            if periodo is not None:
                candidatos.add(periodo)
    if len(candidatos) == 1:
        return next(iter(candidatos))
    return None


def _localizar_fila_encabezado_columnas(ws) -> int | None:
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        for c in row:
            if isinstance(c.value, str) and c.value.strip().upper() == "DNI":
                return c.row
    return None


def _mapear_columnas(ws, fila: int) -> dict[str, int]:
    columnas: dict[str, int] = {}
    for c in ws[fila]:
        if not isinstance(c.value, str):
            continue
        texto = re.sub(r"\s+", " ", c.value.strip().upper())
        campo = _CAMPOS_ANEXO3.get(texto)
        if campo and campo not in columnas:
            columnas[campo] = c.column
    return columnas


def _fila_fin_datos(ws, fila_inicio: int) -> int:
    """Primera fila (desde `fila_inicio`) que marca el fin de la seccion de personas: la fila de
    "LEYENDA" (siempre presente en los ANEXO 3 reales revisados, Tactamal y Cristobal Benque) o,
    si no aparece, `ws.max_row + 1`. Devuelve una cota, no un punto de corte por fila en blanco:
    caso real Tactamal, la primera persona ocupa 2 filas combinadas (13:14) y la segunda fila de
    esa combinacion se lee en blanco -- cortar ahi (como hacia antes) perdia a las otras 11
    personas reales del archivo. Con la cota fija por LEYENDA, las filas en blanco intermedias se
    saltan sin terminar la lectura."""
    for row in ws.iter_rows(min_row=fila_inicio, max_row=ws.max_row):
        for c in row:
            if isinstance(c.value, str) and c.value.strip().upper().startswith(
                "LEYENDA"
            ):
                return c.row
    return ws.max_row + 1


def _dias_grid(ws, fila_dias: int) -> list[tuple[int, int]]:
    """(dia_numero, columna) para cada celda con un numero de dia valido en la fila."""
    dias = []
    for c in ws[fila_dias]:
        if isinstance(c.value, int) and 1 <= c.value <= 31:
            dias.append((c.value, c.column))
    return dias


_INICIALES_DIA_SEMANA = {
    "L",
    "M",
    "X",
    "J",
    "V",
    "S",
    "D",
    "LU",
    "MA",
    "MI",
    "JU",
    "VI",
    "SA",
    "DO",
}


def _fila_es_iniciales_dia_semana(ws, fila: int, columnas: list[int]) -> bool:
    """True si la fila bajo la grilla de dias trae iniciales del dia de la semana (L,M,X,J,V,S,D
    o Lu/Ma/Mi/Ju/Vi/Sa/Do, con o sin tilde/punto) o esta vacia -- patron real en 242 de 252
    archivos ANEXO 3 revisados (corpus data/raw, 2026-09-12), casi siempre entre la fila de
    numeros de dia y la primera persona. Si la mayoria de las celdas no encajan ahi (p.ej. son
    codigos de asistencia reales, caso real "PARTE MENSUAL DE ASISTENCIA" I.E. 18237), esa fila
    no es un separador y no debe saltarse -- evita perder la primera persona del reporte."""
    no_vacios = [
        v
        for v in (ws.cell(row=fila, column=c).value for c in columnas)
        if v not in (None, "")
    ]
    if not no_vacios:
        return True
    coincidencias = sum(
        isinstance(v, str)
        and _sin_acentos(v).strip().upper().rstrip(".") in _INICIALES_DIA_SEMANA
        for v in no_vacios
    )
    return coincidencias >= len(no_vacios) / 2


def _es_docente_por_cargo(cargo_raw: str | None) -> bool:
    if not cargo_raw:
        return False
    texto = cargo_raw.strip().upper()
    return any(k in texto for k in _CARGO_DOCENTE) or bool(
        re.match(
            r"^(?:PROF(?:[./\s]|$)|DOC(?:[.(/\s]|$)|AUX(?:[.\s]|$)|P[.]?\s*AULA\b)",
            texto,
        )
    )


def _resolver_rol(
    cur: psycopg.Cursor,
    dni: str,
    cargo_raw: str | None,
    condicion_raw: str | None = None,
) -> int | None:
    """Prioriza el rol que NEXUS ya conoce para este DNI (mas confiable que el texto libre de
    "Cargo" del reporte); si el trabajador es enteramente nuevo, usa un mapeo conservador por
    palabra clave -- sin coincidencia clara, no se adivina, se deja sin resolver."""
    cur.execute(
        """
        SELECT v.rol_laboral_id FROM vinculo_trabajador_ie v
        JOIN trabajador t ON t.trabajador_id = v.trabajador_id
        WHERE t.dni = %s AND v.tipo_registro <> 'POR_REPORTE'
        ORDER BY v.vinculo_trabajador_ie_id LIMIT 1
        """,
        (dni,),
    )
    fila = cur.fetchone()
    if fila:
        return fila["rol_laboral_id"]
    codigo = rol_por_texto(cargo_raw, condicion_raw)
    if codigo:
        cur.execute(
            "SELECT rol_laboral_id FROM catalogo_rol_laboral WHERE codigo = %s",
            (codigo,),
        )
        fila = cur.fetchone()
        return fila["rol_laboral_id"] if fila else None
    return None


def rol_por_texto(cargo_raw, condicion_raw=None):
    """Solo abreviaturas inequívocas; P/A, PIP y otros cargos ambiguos siguen pendientes."""
    cargo = _sin_acentos(str(cargo_raw or "")).upper().strip()
    condicion = _sin_acentos(str(condicion_raw or "")).upper()
    if re.search(r"\bCAS\b", condicion):
        return "CAS"
    if _es_docente_por_cargo(cargo):
        return "DOCENTE"
    if re.search(r"\bPEC\b|PROMOTOR[AO].*COMUNITARI", cargo):
        return "PEC"
    if re.search(
        r"ADMINISTRATIV|ADMINIS[.]|SECRETARI[AO]|VIGIL(?:ANTE|[.]|$)|MANTENIMIENTO|LIMPIEZA|LABORATORIO",
        cargo,
    ):
        return "ADMINISTRATIVO"
    return None


def _normalizar_dni(raw) -> str | None:
    # Misma identidad en NEXUS, Excel y OCR: nunca truncar dígitos significativos.
    from asistia.importar.nexus import _normalizar_dni as normalizar

    return normalizar(raw)


def _obtener_o_crear_trabajador(
    cur: psycopg.Cursor, dni: str, nombres_completos: str
) -> int:
    cur.execute("SELECT trabajador_id FROM trabajador WHERE dni = %s", (dni,))
    fila = cur.fetchone()
    if fila:
        return fila["trabajador_id"]
    # El reporte trae "Apellidos y Nombres" en un solo campo, no separado como NEXUS -- se
    # conserva junto en apellido_paterno (unica columna disponible sin adivinar donde corta cada
    # parte) y nombres se deja igual, para no fragmentar un nombre real con una regla fragil.
    cur.execute(
        "INSERT INTO trabajador (dni, apellido_paterno, apellido_materno, nombres) "
        "VALUES (%s, %s, '-', %s) RETURNING trabajador_id",
        (dni, (nombres_completos or "-").strip(), (nombres_completos or "-").strip()),
    )
    return cur.fetchone()["trabajador_id"]


# ============================================================
# ANEXO 4 (resumen) -- contraste, no fuente de marcas (ADR-019 de v1, regla 6)
# ============================================================


_CAMPOS_RESUMEN = (
    (("INASISTENCIA", "DIA"), "inasistencias_dias_raw"),
    (("TARDANZA", "HORA"), "tardanza_horas_raw"),
    (("TARDANZA", "MINUTO"), "tardanza_minutos_raw"),
    (("PERMISO", "HORA"), "permiso_sin_goce_horas_raw"),
    (("PERMISO", "MINUTO"), "permiso_sin_goce_minutos_raw"),
    (("HUELGA",), "huelga_paro_dias_raw"),
    (("PARO",), "huelga_paro_dias_raw"),
    (("OBSERVACION",), "observaciones_raw"),
)


def _mapear_columnas_resumen(ws, fila_categoria: int, fila_sub: int) -> dict[int, str]:
    """columna -> campo de resumen_asistencia_reportado, combinando la fila de categoria
    (con relleno hacia adelante para categorias en celdas combinadas) y la fila de subetiqueta."""
    columnas: dict[int, str] = {}
    categoria_actual = ""
    for col in range(1, ws.max_column + 1):
        v_cat = ws.cell(row=fila_categoria, column=col).value
        if isinstance(v_cat, str) and v_cat.strip():
            categoria_actual = v_cat.strip().upper()
        v_sub = ws.cell(row=fila_sub, column=col).value if fila_sub else None
        # sin acentos: el encabezado real trae "Días" (con tilde) y las claves de _CAMPOS_RESUMEN
        # usan "DIA" en ASCII -- sin esto, "DIA" nunca calzaba como subcadena de "DÍAS".
        combinado = _sin_acentos(f"{categoria_actual} {v_sub or ''}".upper())
        for claves, campo in _CAMPOS_RESUMEN:
            if all(k in combinado for k in claves):
                columnas.setdefault(col, campo)
                break
    return columnas


def _importar_anexo4(
    conn: psycopg.Connection,
    cur: psycopg.Cursor,
    ws,
    reporte_asistencia_id: str,
    col_dni_a_ter: dict[str, str],
    errores_fila: list[str],
) -> None:
    fila_cat = _localizar_fila_encabezado_columnas(ws)
    if fila_cat is None:
        return
    columnas_id = _mapear_columnas(ws, fila_cat)
    if "dni" not in columnas_id:
        return
    columnas_resumen = _mapear_columnas_resumen(ws, fila_cat, fila_cat + 1)

    fila_datos_inicio = fila_cat + 3
    fila_fin_datos = _fila_fin_datos(ws, fila_datos_inicio)
    fila_datos = fila_datos_inicio
    while fila_datos < fila_fin_datos:
        dni_val = ws.cell(row=fila_datos, column=columnas_id["dni"]).value
        nombres_val = (
            ws.cell(row=fila_datos, column=columnas_id["nombres"]).value
            if "nombres" in columnas_id
            else None
        )
        if dni_val is None and not nombres_val:
            fila_datos += 1
            continue
        dni = _normalizar_dni(dni_val)
        if not dni:
            fila_datos += 1
            continue

        try:
            with conn.transaction():
                _procesar_fila_anexo4(
                    cur,
                    ws,
                    fila_datos,
                    columnas_resumen,
                    dni,
                    dni_val,
                    nombres_val,
                    reporte_asistencia_id,
                    col_dni_a_ter,
                )
        except Exception as exc:  # noqa: BLE001
            errores_fila.append(f"ANEXO 4 fila {fila_datos}: {exc}")
        fila_datos += 1


def _procesar_fila_anexo4(
    cur: psycopg.Cursor,
    ws,
    fila_datos: int,
    columnas_resumen: dict[int, str],
    dni: str,
    dni_val,
    nombres_val,
    reporte_asistencia_id: str,
    col_dni_a_ter: dict[str, str],
) -> None:
    valores = {}
    for col, campo in columnas_resumen.items():
        v = ws.cell(row=fila_datos, column=col).value
        if v is not None and str(v).strip():
            valores[campo] = str(v).strip()

    trabajador_en_reporte_id = col_dni_a_ter.get(dni)
    if trabajador_en_reporte_id:
        cur.execute(
            """
            UPDATE trabajador_en_reporte SET fila_resumen_origen = %s
            WHERE trabajador_en_reporte_id = %s AND fila_resumen_origen IS NULL
            """,
            (fila_datos, trabajador_en_reporte_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO trabajador_en_reporte
                (reporte_asistencia_id, fila_resumen_origen, dni_reportado_raw, nombres_reportados_raw)
            VALUES (%s, %s, %s, %s)
            RETURNING trabajador_en_reporte_id
            """,
            (reporte_asistencia_id, fila_datos, str(dni_val), str(nombres_val or "-")),
        )
        trabajador_en_reporte_id = cur.fetchone()["trabajador_en_reporte_id"]

    if valores:
        columnas_ins = ", ".join(valores.keys())
        marcadores = ", ".join(["%s"] * len(valores))
        actualiza = ", ".join(f"{c} = EXCLUDED.{c}" for c in valores)
        cur.execute(
            f"""
            INSERT INTO resumen_asistencia_reportado (trabajador_en_reporte_id, {columnas_ins})
            VALUES (%s, {marcadores})
            ON CONFLICT (trabajador_en_reporte_id) DO UPDATE SET {actualiza}
            """,
            (trabajador_en_reporte_id, *valores.values()),
        )


# ============================================================
# Punto de entrada
# ============================================================


def _nivel_efectivo_fila(especialidad_raw: str | None, nivel_header: str) -> str | None:
    """El nivel real de esta persona: el de la columna Especialidad/Area solo cuando su valor
    coincide exactamente con un nivel conocido (PRIMARIA/SECUNDARIA/INICIAL) -- caso real Tactamal
    (IEPySM = Primaria y Secundaria de Menores), que combina ambos niveles en un solo ANEXO 3
    ("Nivel/Modalidad Educativa: PRIMARIA - SECUNDARIA/EBR" en el encabezado) y usa esa columna
    para distinguirlos persona por persona. En archivos de un solo nivel esa misma columna trae la
    especialidad real del docente (ej. "MAT.", "CYT" en Cristobal Benque) y no debe leerse como
    nivel -- de ahi la coincidencia exacta contra un nivel conocido, no una coincidencia parcial.

    Si el encabezado combina mas de un nivel conocido (caso IEPySM) y esta fila no trae una
    especialidad que lo desambigue, se devuelve None -- no "no inventar": adivinar heredaria el
    primer nivel del encabezado en silencio (bug real, revision de modelo 2026-09-10). Con un solo
    nivel en el encabezado, ese nivel sigue siendo la respuesta correcta sin ayuda de la fila."""
    if especialidad_raw:
        texto = especialidad_raw.strip().upper()
        if texto in NIVELES_CONOCIDOS:
            return texto
    nivel_header_norm = (nivel_header or "").upper()
    niveles_en_encabezado = [n for n in NIVELES_CONOCIDOS if n in nivel_header_norm]
    if len(niveles_en_encabezado) > 1:
        return None
    return nivel_header


def _obtener_o_crear_reporte(
    cur: psycopg.Cursor,
    cache: dict[str | None, str | None],
    nivel_efectivo: str | None,
    institucion_raw: str,
    documento_recibido_id: str,
    periodo: date,
    turno_raw: str,
    hoja_origen: str = "1",
) -> str | None:
    """reporte_asistencia_id para este nivel efectivo dentro del archivo, creando su serie/reporte
    la primera vez que aparece. Un mismo archivo puede producir mas de un reporte_asistencia
    (ver _nivel_efectivo_fila): comparten documento_recibido_id (mismo hash de archivo) pero cada
    uno tiene su propia institucion_educativa_id/nivel_modalidad, igual que si fueran 2 archivos.
    nivel_efectivo puede ser None (encabezado combina niveles y la fila no trae como desambiguar,
    ver _nivel_efectivo_fila) -- resolver_institucion ya devuelve None con nivel_raw vacio, asi que
    esto cae de forma natural en "institucion no resuelta" sin caso especial."""
    if nivel_efectivo in cache:
        return cache[nivel_efectivo]
    # indice_bloque distingue los reportes que salen del mismo documento (mismo
    # documento_recibido_id, misma hoja) cuando el archivo combina niveles -- ver ADR-018 de v1,
    # que ya preveia "varios bloques en el mismo libro" y reservo esta columna para eso.
    indice_bloque = len(cache) + 1

    institucion_educativa_id = resolver_institucion(
        cur, institucion_raw, nivel_efectivo
    )
    if institucion_educativa_id is None:
        cache[nivel_efectivo] = None
        return None

    cur.execute(
        "SELECT nivel_modalidad FROM institucion_educativa WHERE institucion_educativa_id = %s",
        (institucion_educativa_id,),
    )
    nivel_modalidad = cur.fetchone()["nivel_modalidad"]
    turno = (turno_raw or "TODOS").strip() or "TODOS"

    cur.execute(
        "SELECT reporte_asistencia_serie_id FROM reporte_asistencia_serie "
        "WHERE institucion_educativa_id = %s AND periodo = %s AND nivel_modalidad = %s AND turno = %s",
        (institucion_educativa_id, periodo, nivel_modalidad, turno),
    )
    fila = cur.fetchone()
    if fila:
        serie_id = fila["reporte_asistencia_serie_id"]
    else:
        cur.execute(
            "INSERT INTO reporte_asistencia_serie (institucion_educativa_id, periodo, nivel_modalidad, turno) "
            "VALUES (%s, %s, %s, %s) RETURNING reporte_asistencia_serie_id",
            (institucion_educativa_id, periodo, nivel_modalidad, turno),
        )
        serie_id = cur.fetchone()["reporte_asistencia_serie_id"]

    cur.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM reporte_asistencia WHERE reporte_asistencia_serie_id = %s",
        (serie_id,),
    )
    version = cur.fetchone()["v"]

    cur.execute(
        """
        INSERT INTO reporte_asistencia
            (reporte_asistencia_serie_id, institucion_educativa_id, documento_recibido_id, periodo,
             version, indice_bloque, tipo_fuente, institucion_reportada_raw,
             nivel_modalidad_reportada_raw, turno_reportado_raw, estado_match_ie, hoja_pagina_origen)
        VALUES (%s, %s, %s, %s, %s, %s, 'EXCEL_NATIVO', %s, %s, %s, 'RESUELTO', %s)
        RETURNING reporte_asistencia_id
        """,
        (
            serie_id,
            institucion_educativa_id,
            documento_recibido_id,
            periodo,
            version,
            indice_bloque,
            institucion_raw,
            nivel_efectivo,
            turno_raw,
            hoja_origen,
        ),
    )
    reporte_asistencia_id = cur.fetchone()["reporte_asistencia_id"]
    cache[nivel_efectivo] = reporte_asistencia_id
    return reporte_asistencia_id


def _nombre_hoja_coincide(nombre: str, numero: str) -> bool:
    # "ANEXO 3"/"ANEXO 03": visto en archivos reales con el numero con cero a la izquierda.
    n = re.sub(r"\s+", " ", nombre.strip().upper())
    return n.startswith((f"ANEXO {numero}", f"ANEXO 0{numero}"))


def _hoja_anexo3(wb) -> str | None:
    """Ubica la hoja ANEXO 3 por nombre primero (rapido, cubre la mayoria); si ninguna hoja se
    llama "ANEXO 3"/"ANEXO 03" (caso real: algunos archivos nombran las hojas "3JULIO"/"4JULIO",
    "Julio 1"/"Julio 2", etc, sin la palabra ANEXO), busca por contenido -- la hoja con una fila de
    encabezado DNI seguida de una grilla de dias (1..31) solo puede ser el ANEXO 3: el ANEXO 4
    tambien tiene columna DNI pero nunca trae esa grilla (tiene categorias de resumen en su
    lugar). Mismo principio de "identificar por contenido, no por lo declarado" que ya se usa para
    la institucion (ADR-016/018 de v1)."""
    for n in wb.sheetnames:
        if _nombre_hoja_coincide(n, "3"):
            return n
    for n in wb.sheetnames:
        ws = wb[n]
        fila_dni = _localizar_fila_encabezado_columnas(ws)
        if fila_dni is not None and _dias_grid(ws, fila_dni + 1):
            return n
    return None


def importar_asistencia(
    conn: psycopg.Connection, ruta
) -> ResultadoImportacionAsistencia:
    resultado = ResultadoImportacionAsistencia()
    wb = openpyxl.load_workbook(ruta, data_only=True)
    hoja3 = _hoja_anexo3(wb)
    if hoja3 is None:
        resultado.error = f"{ruta.name}: no se encontró una hoja ANEXO 3"
        return resultado
    ws3 = wb[hoja3]

    encabezado = _leer_encabezado(ws3)
    periodo = _parsear_periodo(encabezado["periodo"])
    if periodo is None:
        # respaldo: la mayoria del corpus real no trae la etiqueta "Periodo(mes/año):" (medido:
        # 124/261 archivos reales fallaban aqui, 105 de ellos con un valor de periodo inequivoco
        # en otra parte del encabezado) -- ver _buscar_periodo_en_encabezado.
        periodo = _buscar_periodo_en_encabezado(ws3)
    if periodo is None:
        resultado.error = f"{ruta.name}: no se pudo leer el período del encabezado ({encabezado['periodo']!r})"
        return resultado

    fila_cols = _localizar_fila_encabezado_columnas(ws3)
    if fila_cols is None:
        resultado.error = (
            f"{ruta.name}: no se encontró la fila de encabezado de columnas (DNI)"
        )
        return resultado
    columnas = _mapear_columnas(ws3, fila_cols)
    if "dni" not in columnas:
        resultado.error = (
            f"{ruta.name}: la hoja ANEXO 3 no tiene columna DNI reconocible"
        )
        return resultado

    fila_dias = fila_cols + 1
    dias_grid = _dias_grid(ws3, fila_dias)
    dias_presentes = {d for d, _ in dias_grid}
    continuaciones = []
    for otra in wb:
        if otra.title == ws3.title or _nombre_hoja_coincide(otra.title, "4"):
            continue
        for n in range(1, min(otra.max_row, 40) + 1):
            candidatos = [d for d, _ in _dias_grid(otra, n)]
            if (
                len(candidatos) >= 3
                and candidatos == list(range(min(candidatos), max(candidatos) + 1))
                and (
                    set(candidatos) - dias_presentes
                    or _localizar_fila_encabezado_columnas(otra)
                )
            ):
                continuaciones.append(otra.title)
                break
    if not dias_grid or continuaciones:
        resultado.error = "La tabla tiene columnas o filas en otras hojas, o carece de columnas diarias; requiere reconstrucción en lectura asistida."
        wb.close()
        return resultado
    columnas_dia = [c for _, c in dias_grid]
    fila_siguiente = fila_cols + 2
    fila_datos_inicio = (
        fila_cols + 3
        if _fila_es_iniciales_dia_semana(ws3, fila_siguiente, columnas_dia)
        else fila_siguiente
    )

    with conn.cursor() as cur:
        documento_recibido_id = registrar_documento(
            cur,
            ruta,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        institucion_raw = str(encabezado["institucion"] or "")
        nivel_header = str(encabezado["nivel"] or "")
        turno_raw = str(encabezado["turno"] or "")

        cache_reportes: dict[str, str | None] = {}
        col_dni_a_ter: dict[str, str] = {}
        fila_fin_datos = _fila_fin_datos(ws3, fila_datos_inicio)
        fila_datos = fila_datos_inicio
        while fila_datos < fila_fin_datos:
            dni_val = ws3.cell(row=fila_datos, column=columnas["dni"]).value
            nombres_val = (
                ws3.cell(row=fila_datos, column=columnas["nombres"]).value
                if "nombres" in columnas
                else None
            )
            if dni_val is None and not nombres_val:
                # fila en blanco intermedia (p.ej. la segunda fila de una persona con celdas
                # combinadas en 2 filas, caso real Tactamal) -- no es el fin de los datos, la
                # cota real es `fila_fin_datos` (ver _fila_fin_datos).
                fila_datos += 1
                continue

            especialidad_val = (
                ws3.cell(row=fila_datos, column=columnas["especialidad"]).value
                if "especialidad" in columnas
                else None
            )
            nivel_efectivo = _nivel_efectivo_fila(
                str(especialidad_val) if especialidad_val is not None else None,
                nivel_header,
            )
            reporte_asistencia_id = _obtener_o_crear_reporte(
                cur,
                cache_reportes,
                nivel_efectivo,
                institucion_raw,
                documento_recibido_id,
                periodo,
                turno_raw,
                hoja_origen=ws3.title,
            )
            if reporte_asistencia_id is None:
                resultado.errores_fila.append(
                    f"fila {fila_datos}: no se pudo identificar la institución para nivel "
                    f"{nivel_efectivo!r} (institucion={institucion_raw!r})"
                )
                fila_datos += 1
                continue

            resultado.total_trabajadores += 1
            try:
                # savepoint por persona: un error en una fila no debe inutilizar la transaccion
                # completa del reporte (Postgres aborta toda la transaccion tras el primer error
                # SQL hasta el proximo ROLLBACK -- conn.transaction() anidado usa SAVEPOINT).
                with conn.transaction():
                    _procesar_persona(
                        cur,
                        ws3,
                        fila_datos,
                        columnas,
                        dias_grid,
                        periodo,
                        reporte_asistencia_id,
                        col_dni_a_ter,
                        resultado,
                        diferir_marcas=True,
                    )
            except Exception as exc:  # noqa: BLE001
                resultado.errores_fila.append(f"fila {fila_datos}: {exc}")
            fila_datos += 1

        resultado.reportes_asistencia_id = {
            nivel: rid for nivel, rid in cache_reportes.items() if rid
        }
        if not resultado.reportes_asistencia_id:
            resultado.error = (
                f"{ruta.name}: no se pudo identificar la institución para ningún nivel presente "
                f"en el archivo (institucion={institucion_raw!r}, nivel={nivel_header!r})"
            )
            return resultado

        hoja4 = next((n for n in wb.sheetnames if _nombre_hoja_coincide(n, "4")), None)
        if hoja4:
            reporte_por_defecto = next(iter(resultado.reportes_asistencia_id.values()))
            _importar_anexo4(
                conn,
                cur,
                wb[hoja4],
                reporte_por_defecto,
                col_dni_a_ter,
                resultado.errores_fila,
            )

    from asistia.calidad_leyendas import referencias

    categorias = normalizar_leyenda(
        leer_leyenda_xlsx(ws3, conocidas=referencias(conn)), "asistencia"
    )
    for rid in resultado.reportes_asistencia_id.values():
        finalizar_asistencia(conn, rid, categorias, ws3, dias_grid)
    wb.close()
    conn.commit()
    return resultado


def _procesar_persona(
    cur: psycopg.Cursor,
    ws,
    fila: int,
    columnas: dict[str, int],
    dias_grid: list[tuple[int, int]],
    periodo: date,
    reporte_asistencia_id: str,
    col_dni_a_ter: dict[str, str],
    resultado: ResultadoImportacionAsistencia,
    *,
    diferir_marcas: bool = False,
) -> None:
    dni = _normalizar_dni(ws.cell(row=fila, column=columnas["dni"]).value)
    nombres = str(
        ws.cell(row=fila, column=columnas.get("nombres", columnas["dni"])).value or ""
    ).strip()
    cargo = (
        str(ws.cell(row=fila, column=columnas["cargo"]).value or "")
        if "cargo" in columnas
        else None
    )
    especialidad = (
        str(ws.cell(row=fila, column=columnas["especialidad"]).value or "")
        if "especialidad" in columnas
        else None
    )
    condicion = (
        str(ws.cell(row=fila, column=columnas["condicion"]).value or "")
        if "condicion" in columnas
        else None
    )
    telefono = (
        str(ws.cell(row=fila, column=columnas["telefono"]).value or "")
        if "telefono" in columnas
        else None
    )
    correo = (
        str(ws.cell(row=fila, column=columnas["correo"]).value or "")
        if "correo" in columnas
        else None
    )
    jornada_raw = (
        ws.cell(row=fila, column=columnas["jornada"]).value
        if "jornada" in columnas
        else None
    )
    try:
        jornada = float(jornada_raw) if jornada_raw not in (None, "") else None
    except (TypeError, ValueError):
        jornada = None

    trabajador_id = rol_laboral_id = None
    estado_match = "SIN_MATCH"
    if dni:
        trabajador_id = _obtener_o_crear_trabajador(cur, dni, nombres)
        rol_laboral_id = _resolver_rol(cur, dni, cargo, condicion)
        estado_match = "RESUELTO" if rol_laboral_id else "PENDIENTE"

    cur.execute(
        """
        INSERT INTO trabajador_en_reporte
            (reporte_asistencia_id, trabajador_id, rol_laboral_id, fila_detalle_origen,
             nombres_reportados_raw, dni_reportado_raw, cargo_reportado_raw, especialidad_reportada_raw,
             condicion_reportada_raw, telefono_reportado_raw, correo_reportado_raw,
             jornada_horas_reportada, estado_match)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING trabajador_en_reporte_id
        """,
        (
            reporte_asistencia_id,
            trabajador_id,
            rol_laboral_id,
            fila,
            nombres or "-",
            str(ws.cell(row=fila, column=columnas["dni"]).value or ""),
            cargo,
            especialidad,
            condicion,
            telefono,
            correo,
            jornada,
            estado_match,
        ),
    )
    trabajador_en_reporte_id = cur.fetchone()["trabajador_en_reporte_id"]
    if dni:
        col_dni_a_ter[dni] = trabajador_en_reporte_id

    if estado_match == "RESUELTO":
        resultado.resueltos += 1
        cur.execute(
            "SELECT fn_resolver_vinculo_por_reporte(%s) AS v",
            (trabajador_en_reporte_id,),
        )
        vinculo_id = cur.fetchone()["v"]
        if vinculo_id:
            cur.execute(
                "UPDATE trabajador_en_reporte SET vinculo_trabajador_ie_id = %s WHERE trabajador_en_reporte_id = %s",
                (vinculo_id, trabajador_en_reporte_id),
            )
            cur.execute(
                "SELECT 1 FROM vinculo_trabajador_ie_confirmacion "
                "WHERE trabajador_en_reporte_id = %s AND origen = 'AUTOMATICO_IMPORTACION' "
                "AND accion = 'CONFIRMA_INICIO'",
                (trabajador_en_reporte_id,),
            )
            if cur.fetchone():
                resultado.alertas_vinculo_automatico += 1
    else:
        resultado.pendientes += 1

    for dia_num, col in dias_grid:
        try:
            fecha = date(periodo.year, periodo.month, dia_num)
        except ValueError:
            continue
        codigo_raw = ws.cell(row=fila, column=col).value
        if codigo_raw is None or not str(codigo_raw).strip():
            cur.execute(
                """
                INSERT INTO asistencia_dia (trabajador_en_reporte_id, fecha, estado_captura, celda_origen)
                VALUES (%s, %s, 'VACIO', %s)
                ON CONFLICT (trabajador_en_reporte_id, fecha) DO NOTHING
                """,
                (
                    trabajador_en_reporte_id,
                    fecha,
                    ws.cell(row=fila, column=col).coordinate,
                ),
            )
            continue
        codigo = str(codigo_raw).strip()
        cur.execute(
            "SELECT estado_asistencia_id FROM catalogo_estado_asistencia WHERE codigo = %s",
            (codigo,),
        )
        estado_fila = cur.fetchone()
        if diferir_marcas:
            estado_fila = None
        if estado_fila:
            cur.execute(
                """
                INSERT INTO asistencia_dia
                    (trabajador_en_reporte_id, fecha, codigo_reportado_raw, estado_asistencia_id,
                     estado_captura, celda_origen)
                VALUES (%s, %s, %s, %s, 'REGISTRADO', %s)
                ON CONFLICT (trabajador_en_reporte_id, fecha) DO NOTHING
                """,
                (
                    trabajador_en_reporte_id,
                    fecha,
                    codigo,
                    estado_fila["estado_asistencia_id"],
                    ws.cell(row=fila, column=col).coordinate,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO asistencia_dia
                    (trabajador_en_reporte_id, fecha, codigo_reportado_raw, estado_captura, celda_origen)
                VALUES (%s, %s, %s, 'ILEGIBLE', %s)
                ON CONFLICT (trabajador_en_reporte_id, fecha) DO NOTHING
                """,
                (
                    trabajador_en_reporte_id,
                    fecha,
                    codigo,
                    ws.cell(row=fila, column=col).coordinate,
                ),
            )
