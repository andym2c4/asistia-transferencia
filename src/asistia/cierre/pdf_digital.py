"""Cotejo geométrico de tablas digitales que se partieron al imprimir.

Poppler conserva palabras y cajas; las líneas vectoriales delimitan celdas vacías.
Solo se unen páginas contiguas con las mismas separaciones de filas y DNI únicos.
El OCR conserva su respuesta original, incluso cuando desplazó las marcas.
"""

import calendar
import copy
import re
import shutil
import subprocess
from datetime import date
from itertools import pairwise
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader


def leer_geometria(ruta):
    if not shutil.which("pdftotext"):
        return []
    resultado = subprocess.run(
        ["pdftotext", "-bbox-layout", str(ruta), "-"],
        capture_output=True,
        check=True,
        timeout=25,
    )
    raiz = ElementTree.fromstring(resultado.stdout)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    lector = PdfReader(ruta)
    salida = []
    for n, pagina in enumerate(raiz.findall(".//x:page", ns)):
        alto = float(pagina.attrib["height"])
        if lector.pages[n].rotation:
            salida.append({"palabras": [], "lineas": [], "pagina": n + 1})
            continue
        palabras = [
            {"texto": w.text or "", **{k: float(v) for k, v in w.attrib.items()}}
            for w in pagina.findall(".//x:word", ns)
        ]
        segmentos = []
        posicion = [None]

        def visitante(
            op, args, cm, tm, alto=alto, posicion=posicion, segmentos=segmentos
        ):
            if op in (b"m", b"l"):
                x, y = map(float, args)
                punto = (
                    x * cm[0] + y * cm[2] + cm[4],
                    alto - (x * cm[1] + y * cm[3] + cm[5]),
                )
                if op == b"l" and posicion[0] is not None:
                    segmentos.append((posicion[0], punto))
                posicion[0] = punto

        lector.pages[n].extract_text(visitor_operand_before=visitante)
        salida.append({"palabras": palabras, "lineas": segmentos, "pagina": n + 1})
    return salida


def centro(p):
    return (p["xMin"] + p["xMax"]) / 2, (p["yMin"] + p["yMax"]) / 2


def unicos(cifras, tolerancia=1):
    salida = []
    for n in sorted(cifras):
        if not salida or n - salida[-1] > tolerancia:
            salida.append(n)
    return salida


def celdas_diarias(pagina, anio, mes):
    palabras, lineas = pagina["palabras"], pagina["lineas"]
    if not 1 <= mes <= 12 or not 2020 <= anio <= 2100:
        return None
    cantidad = calendar.monthrange(anio, mes)[1]
    for uno in (p for p in palabras if p["texto"] == "1"):
        x, y = centro(uno)
        cabecera = sorted(
            [
                p
                for p in palabras
                if abs(centro(p)[1] - y) < 1
                and p["texto"].isdigit()
                and 1 <= int(p["texto"]) <= 31
            ],
            key=lambda p: centro(p)[0],
        )
        if [int(p["texto"]) for p in cabecera] not in (
            list(range(1, cantidad + 1)),
            list(range(1, cantidad)),
        ):
            continue
        verticales = [
            (a, b)
            for a, b in lineas
            if abs(a[0] - b[0]) < 0.2 and min(a[1], b[1]) < y < max(a[1], b[1])
        ]
        xs = unicos(a[0] for a, b in verticales)
        inicio = next((i for i in range(len(xs) - 1) if xs[i] < x < xs[i + 1]), None)
        if inicio is None or len(xs) < inicio + cantidad + 1:
            continue
        xs = xs[inicio : inicio + cantidad + 1]
        if any(not xs[d - 1] < centro(p)[0] < xs[d] for d, p in enumerate(cabecera, 1)):
            continue
        # Confirma la segunda fila de encabezado y permite solo el último número omitido.
        semanales = []
        for d in range(1, cantidad + 1):
            celda = [
                p
                for p in palabras
                if xs[d - 1] < centro(p)[0] < xs[d]
                and y + 4 < centro(p)[1] < y + 25
                and re.fullmatch("[LMXJVSD]", p["texto"])
            ]
            if len(celda) != 1:
                break
            esperados = ({"L"}, {"M"}, {"M", "X"}, {"J"}, {"V"}, {"S"}, {"D"})
            if celda[0]["texto"] not in esperados[date(anio, mes, d).weekday()]:
                break
            semanales.append(celda[0])
        if len(semanales) != cantidad:
            continue
        y_min = max(p["yMax"] for p in semanales)
        limite = min(max(a[1], b[1]) for a, b in verticales if abs(a[0] - xs[0]) < 1)
        ys = unicos(
            a[1]
            for a, b in lineas
            if abs(a[1] - b[1]) < 0.2
            and min(a[0], b[0]) <= xs[0] + 2
            and max(a[0], b[0]) >= xs[-1] - 2
            and y_min - 1 < a[1] <= limite + 1
        )
        if len(ys) < 2:
            continue
        filas = []
        for top, bottom in pairwise(ys):
            marcas = []
            for left, right in pairwise(xs):
                contenido = sorted(
                    [
                        p
                        for p in palabras
                        if left < centro(p)[0] < right and top < centro(p)[1] < bottom
                    ],
                    key=lambda p: (p["yMin"], p["xMin"]),
                )
                marcas.append(" ".join(p["texto"] for p in contenido) or None)
            if any(m and len(m) > 30 for m in marcas):
                return None
            filas.append({"limites_y": [top, bottom], "marcas": marcas})
        return {
            "filas": filas,
            "columnas": xs,
            "pagina": pagina["pagina"],
            "ultimo_numero_omitido": len(cabecera) != cantidad,
        }
    return None


def identidades_alineadas(pagina, grilla):
    dni_header = [p for p in pagina["palabras"] if p["texto"].upper() == "DNI"]
    if len(dni_header) != 1:
        return None
    x, y = centro(dni_header[0])
    ys = unicos(
        a[1]
        for a, b in pagina["lineas"]
        if abs(a[1] - b[1]) < 0.2 and min(a[0], b[0]) < x < max(a[0], b[0])
    )
    filas = []
    for fila in grilla["filas"]:
        top, bottom = fila["limites_y"]
        if top < y or not all(
            any(abs(limite - v) < 1 for v in ys) for limite in (top, bottom)
        ):
            return None
        dni = [
            p
            for p in pagina["palabras"]
            if re.fullmatch(r"\d{8}", p["texto"])
            and abs(centro(p)[0] - x) < 40
            and top < centro(p)[1] < bottom
        ]
        if len(dni) != 1:
            return None
        filas.append(
            {**fila, "dni": dni[0]["texto"], "pagina_identidad": pagina["pagina"]}
        )
    if len({f["dni"] for f in filas}) != len(filas):
        return None
    return filas


def cotejar_pdf_digital(ruta, extraccion):
    ruta = Path(ruta)
    if ruta.suffix.lower() != ".pdf" or not ruta.is_file():
        return extraccion
    if extraccion.get("cotejo_geometrico"):
        return extraccion
    geometria = leer_geometria(ruta)
    if not geometria:
        return extraccion
    r = copy.deepcopy(extraccion)
    cotejos = []
    for bloque in r["datos"]["bloques"]:
        if (
            bloque.get("tipo") != "asistencia"
            or not bloque.get("personas")
            or not isinstance(bloque.get("anio"), int)
            or not isinstance(bloque.get("mes"), int)
        ):
            continue
        personas = bloque["personas"]
        dnis = {p.get("dni") for p in personas}
        candidatos = []
        for n, pagina in enumerate(geometria):
            grilla = celdas_diarias(pagina, bloque["anio"], bloque["mes"])
            if not grilla:
                continue
            for identidad in geometria[max(0, n - 1) : n + 1]:
                filas = identidades_alineadas(identidad, grilla)
                if (
                    filas
                    and {f["dni"] for f in filas} == dnis
                    and len(filas) == len(personas)
                ):
                    candidatos.append((grilla, filas))
        if len(candidatos) != 1:
            continue
        grilla, filas = candidatos[0]
        por_dni = {f["dni"]: f for f in filas}
        cambios = 0
        for p in personas:
            f = por_dni[p["dni"]]
            recibidas = p.get("marcas", [])
            cambios += sum(a != b for a, b in zip(recibidas, f["marcas"])) + abs(
                len(recibidas) - len(f["marcas"])
            )
            p["marcas_ocr"] = recibidas
            p["marcas"] = f["marcas"]
            fuente = {
                "regla": "PDF_CELDAS_Y_FILAS_1",
                "pagina": grilla["pagina"],
                "pagina_identidad": f["pagina_identidad"],
                "limites_y": f["limites_y"],
                "ancla": "DNI y mismos bordes de fila",
            }
            p["fuentes_fragmentos"] = [fuente]
            p["localizadores_marcas"] = {
                str(d): {**fuente, "limites_x": grilla["columnas"][d - 1 : d + 1]}
                for d in range(1, len(f["marcas"]) + 1)
            }
        meta = {
            "regla": "PDF_CELDAS_Y_FILAS_1",
            "pagina_marcas": grilla["pagina"],
            "pagina_identidad": filas[0]["pagina_identidad"],
            "filas": len(filas),
            "marcas_rectificadas_frente_ocr": cambios,
            "ultimo_numero_omitido": grilla["ultimo_numero_omitido"],
        }
        bloque["reconstruccion"] = meta
        if grilla["ultimo_numero_omitido"]:
            bloque.setdefault("supuestos", []).append(
                "El último número del mes no está impreso. La última columna se identifica por los bordes, la secuencia 1..penúltimo y los días de semana coincidentes con todo el mes; requiere cotejo."
            )
        cotejos.append(meta)
    if cotejos:
        r["cotejo_geometrico"] = cotejos
    return r
