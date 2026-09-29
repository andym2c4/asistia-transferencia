"""Identificacion de institucion educativa por contenido del documento (nombre + nivel), nunca por
lo que declare quien carga el archivo -- mismo principio que ADR-016/ADR-018 de v1.

Compartido entre el importador de calendario y el de asistencia: ambos leen un encabezado con
"nombre de la IE" y "nivel" en texto libre y necesitan resolverlo contra `institucion_educativa`
con la misma ambiguedad real (NEXUS guarda a veces un codigo en `nombre_ie`, a veces el nombre
completo; la calificacion del nivel a veces la trae NEXUS mas larga, a veces el documento).
"""

from __future__ import annotations

import re
import unicodedata

import psycopg

NIVELES_CONOCIDOS = ("SECUNDARIA", "PRIMARIA", "INICIAL")


def sin_acentos(texto: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", texto or "")
        if not unicodedata.combining(c)
    )


def extraer_codigo_numerico(texto: str) -> str | None:
    # 1-7 digitos: se vio codigo de 5 (Tactamal "18091") y de 3 (Membrillo "258") en archivos
    # reales -- NEXUS usa el mismo campo nombre_ie para lo que en unos casos es un codigo y en
    # otros un nombre (ver docs/casos/IE_258_MEMBRILLO_JUNIO_2026.md §3), no hay una longitud fija.
    m = re.search(r"\b(\d{1,7})\b", texto or "")
    return m.group(1) if m else None


_COMILLAS = str.maketrans("", "", "\"'«»“”‘’")

# variante gramatical masculina vista en archivos reales ("NIVEL SECUNDARIO", "SECUNDARIO /
# EDUCACIÓN BÁSICA REGULAR") -- se resuelve al mismo NIVELES_CONOCIDOS canonico, nunca se guarda
# la variante para no romper comparaciones rio abajo (ej. _nivel_efectivo_fila en asistencia.py).
_ALIAS_NIVEL = {"SECUNDARIO": "SECUNDARIA", "PRIMARIO": "PRIMARIA"}


def normalizar_nombre_ie(texto: str) -> str:
    sin_prefijo = re.sub(
        r"^\s*I\.?\s*E\.?\s*N[°º]?\s*", "", texto or "", flags=re.IGNORECASE
    )
    # comillas alrededor del nombre (caso real: '"VIRGEN DEL ROSARIO"') -- NEXUS nunca las guarda.
    return sin_prefijo.translate(_COMILLAS).strip()


def _primer_nivel_conocido(texto: str) -> str | None:
    """Busca cual de los NIVELES_CONOCIDOS (incluidas variantes gramaticales, ver _ALIAS_NIVEL)
    aparece primero como palabra suelta en el texto, en vez de asumir que es la primera palabra
    del texto. Corrige un bug real (revision de corpus 2026-09-10, ~40 archivos reales de nivel
    Inicial): el texto trae "EDUCACIÓN INICIAL" (o "EDUCACIÓN INICIAL - EBR"), no solo "INICIAL"
    -- tomar solo la primera palabra devolvia "EDUCACIÓN", que nunca calzaba con nada, y esas
    instituciones (aunque si existian como candidato por codigo/nombre, confirmado en la base
    real) nunca se resolvian. \\b resuelve de paso el mismo problema si el nivel viene pegado a
    una barra ("INICIAL/EBR")."""
    texto_norm = (texto or "").strip().upper()
    mejor: str | None = None
    mejor_pos = None
    for palabra in (*NIVELES_CONOCIDOS, *_ALIAS_NIVEL):
        m = re.search(rf"\b{palabra}\b", texto_norm)
        if m and (mejor_pos is None or m.start() < mejor_pos):
            mejor, mejor_pos = _ALIAS_NIVEL.get(palabra, palabra), m.start()
    return mejor


def resolver_institucion(
    cur: psycopg.Cursor, nombre_ie_raw: str, nivel_raw: str
) -> int | None:
    """Resuelve por codigo o por nombre (segun cual traiga `institucion_educativa.nombre_ie` para
    esa fila -- NEXUS no es consistente, ver `extraer_codigo_numerico`), y siempre exige que el
    nivel coincida, aunque haya un unico candidato por ese lado. Con un solo corte NEXUS puede
    faltar la institucion de otro nivel del mismo local (caso real: nexus 2026-06-01.xlsx no trae
    secundaria de Tactamal); aceptar el unico candidato sin comprobar su nivel habria vinculado el
    calendario/reporte de secundaria a la institucion de primaria en silencio. Mismo principio de
    ADR-016 de v1: el nivel desambigua siempre, no solo cuando hay mas de un candidato.

    La comparacion de nivel busca cual de NIVELES_CONOCIDOS aparece en el texto (ver
    _primer_nivel_conocido), no un startswith en una sola direccion ni asumir que es la primera
    palabra: el nivel mas calificado a veces lo trae NEXUS ("Inicial - Jardín" vs "Inicial" del
    documento, caso real Membrillo), a veces el documento ("Secundaria o Avanzado" vs "Secundaria"
    de NEXUS, caso real Cristobal Benque), y a veces el documento antepone una palabra generica
    antes del nivel ("EDUCACIÓN INICIAL", ~40 archivos reales de nivel Inicial) -- nada de esto se
    resuelve comparando solo la primera palabra en una posicion fija."""
    codigo = extraer_codigo_numerico(nombre_ie_raw)
    nombre_norm = normalizar_nombre_ie(nombre_ie_raw)

    candidatos: dict[int, dict] = {}
    if codigo:
        # `nombre_ie` no siempre es el codigo solo -- confirmado con datos reales: NEXUS a veces
        # lo guarda como "18140 DIVINO NIÑO JESUS" (codigo + nombre pegados con un espacio, nunca
        # otro separador en los casos reales revisados), no solo "18140". Sin el LIKE, cualquier
        # institucion registrada asi nunca calzaba con el codigo extraido del documento.
        cur.execute(
            "SELECT institucion_educativa_id, nivel_modalidad FROM institucion_educativa "
            "WHERE nombre_ie = %s OR nombre_ie LIKE %s",
            (codigo, codigo + " %"),
        )
        for fila in cur.fetchall():
            candidatos[fila["institucion_educativa_id"]] = fila
    if nombre_norm:
        cur.execute(
            "SELECT institucion_educativa_id, nivel_modalidad FROM institucion_educativa "
            "WHERE UPPER(nombre_ie) = UPPER(%s)",
            (nombre_norm,),
        )
        filas = cur.fetchall()
        if not filas:
            # Sin match exacto: reintenta ignorando tildes. Caso real (2026-09-12): NEXUS guarda
            # "RAMON CASTILLA" sin tilde, el documento trae "RAMÓN CASTILLA" -- UPPER() de
            # Postgres no quita acentos, así que el match exacto nunca calzaba pese a ser
            # evidentemente la misma institución.
            objetivo = sin_acentos(nombre_norm).upper()
            cur.execute(
                "SELECT institucion_educativa_id, nivel_modalidad, nombre_ie FROM institucion_educativa"
            )
            filas = [
                f
                for f in cur.fetchall()
                if sin_acentos(f["nombre_ie"]).upper() == objetivo
            ]
        for fila in filas:
            candidatos[fila["institucion_educativa_id"]] = fila

    nivel_doc = _primer_nivel_conocido(nivel_raw)
    coincide = [
        c
        for c in candidatos.values()
        if nivel_doc and _primer_nivel_conocido(c["nivel_modalidad"]) == nivel_doc
    ]
    if len(coincide) == 1:
        return coincide[0]["institucion_educativa_id"]
    return None


MESES = {
    "ENERO": 1,
    "FEBRERO": 2,
    "MARZO": 3,
    "ABRIL": 4,
    "MAYO": 5,
    "JUNIO": 6,
    "JULIO": 7,
    "AGOSTO": 8,
    "SEPTIEMBRE": 9,
    "SETIEMBRE": 9,
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}
