"""Representaciones privadas del original, aisladas y sin contenido interactivo."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep
from xml.etree import ElementTree

import openpyxl
import xlrd
from flask import current_app
from PIL import Image, ImageChops
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from asistia.configuracion import entorno_local, ruta_interna
from asistia.db import sha256_de

from . import ErrorDeTrabajo

RECETA = "original-visual-v1"
EXCEL = {".xlsx", ".xlsm", ".xls"}
IMAGEN = {".png", ".jpg", ".jpeg"}
MAX_PAGINAS = 100


def hojas_excel(path):
    """Solo hojas visibles; conserva índices originales para el conversor."""
    if path.suffix.lower() == ".xls":
        wb = xlrd.open_workbook(path, on_demand=True)
        try:
            return [
                {"indice": n, "nombre": s.name}
                for n, s in enumerate(wb.sheets())
                if not s.visibility
            ]
        finally:
            wb.release_resources()
    wb = openpyxl.load_workbook(path, read_only=True, keep_links=False)
    try:
        return [
            {"indice": n, "nombre": s.title}
            for n, s in enumerate(wb.worksheets)
            if s.sheet_state == "visible"
        ]
    finally:
        wb.close()


def hoja_inicial(hojas, solicitada, origen):
    if solicitada:
        for h in hojas:
            if h["nombre"] == solicitada:
                return h
        raise ErrorDeTrabajo("No se encontró esa hoja visible en el original.", 404)
    return next(
        (h for h in hojas if h["nombre"] == origen),
        next(
            (
                h
                for h in hojas
                if h["nombre"].upper().replace(" ", "") in {"ANEXO3", "ANEXO03"}
            ),
            hojas[0] if hojas else None,
        ),
    )


@contextmanager
def bloqueo(carpeta):
    with (carpeta / "vista.lock").open("a") as lock:
        limite = monotonic() + 30
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if monotonic() >= limite:
                    raise ErrorDeTrabajo(
                        "La vista sigue preparándose. Vuelve a intentarlo en unos segundos.",
                        409,
                    ) from None
                sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def ejecutar_aislado(comando, entrada, trabajo, *, runtime=None, fuentes=None):
    """Sin red ni acceso al resto de datos; solo original y runtime de lectura."""
    if not shutil.which("bwrap"):
        raise ErrorDeTrabajo(
            "El visor no está disponible en este servidor. Puedes descargar el original.",
            503,
        )
    args = ["bwrap", "--die-with-parent", "--unshare-all", "--new-session"]
    for sistema in ("/usr", "/lib", "/lib64"):
        if Path(sistema).exists():
            args += ["--ro-bind", sistema, sistema]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    if runtime:
        args += ["--ro-bind", str(runtime), str(runtime)]
    if fuentes and (not runtime or not fuentes.is_relative_to(runtime)):
        args += ["--ro-bind", str(fuentes), str(fuentes)]
    args += [
        "--ro-bind",
        str(entrada),
        "/entrada" + entrada.suffix.lower(),
        "--bind",
        str(trabajo),
        "/trabajo",
        "--clearenv",
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        *comando,
    ]
    try:
        result = subprocess.run(args, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ErrorDeTrabajo(
            "No se pudo preparar la vista a tiempo. Reintenta o descarga el original.",
            503,
        ) from None
    if result.returncode:
        raise ErrorDeTrabajo(
            "No se pudo representar este documento. Puedes reintentar o descargar el original.",
            422,
        )


def convertir_pdf(path, trabajo, hoja):
    entorno = entorno_local()
    ejecutable = ruta_interna(
        entorno.get("ASISTIA_X2T", "data/runtime/onlyoffice/converter/x2t")
    ).resolve()
    fuentes = ruta_interna(
        entorno.get("ASISTIA_ONLYOFFICE_FONTS", "data/runtime/onlyoffice/fonts")
    ).resolve()
    if not ejecutable.is_file() or not (fuentes / "AllFonts.js").is_file():
        raise ErrorDeTrabajo(
            "El conversor del visor no está disponible. Puedes descargar el original.",
            503,
        )
    plantilla = fuentes / "AllFonts.template.js"
    indice = (
        plantilla.read_text().replace("__ASISTIA_FONTS__", str(fuentes))
        if plantilla.is_file()
        else (fuentes / "AllFonts.js").read_text()
    )
    (trabajo / "AllFonts.js").write_text(indice)
    opciones = {}
    if hoja is not None:
        opciones = {
            "spreadsheetLayout": {
                "ignorePrintArea": True,
                "fitToWidth": 1,
                "fitToHeight": 0,
                "headings": True,
                "gridLines": True,
                "orientation": "landscape",
            },
            "adjustOptions": {"activeSheetsArray": [hoja["indice"]]},
        }
    raiz = ElementTree.Element("TaskQueueDataConvert")
    for k, v in {
        "m_sFileFrom": "/entrada" + path.suffix.lower(),
        "m_sFileTo": "/trabajo/documento.pdf",
        "m_nFormatTo": "513",
        "m_sFontDir": str(fuentes),
        "m_sAllFontsPath": "/trabajo/AllFonts.js",
        "m_sJsonParams": json.dumps(opciones),
    }.items():
        ElementTree.SubElement(raiz, k).text = v
    ElementTree.ElementTree(raiz).write(
        trabajo / "conversion.xml", encoding="utf-8", xml_declaration=True
    )
    ejecutar_aislado(
        [str(ejecutable), "/trabajo/conversion.xml"],
        path,
        trabajo,
        runtime=ejecutable.parent.parent,
        fuentes=fuentes,
    )
    pdf = trabajo / "documento.pdf"
    if not pdf.is_file():
        raise ErrorDeTrabajo(
            "No se generó una vista del documento. Descarga el original para consultarlo.",
            422,
        )
    return pdf


def preparar(path, sha, hoja=None):
    """Caché por contenido/receta/hoja. Publicación atómica tras verificar el resultado."""
    carpeta = (
        current_app.config["STORAGE_ROOT"]
        / "vistas"
        / RECETA
        / sha
        / (f"hoja-{hoja['indice']}" if hoja else "documento")
    )
    carpeta.mkdir(parents=True, exist_ok=True, mode=0o700)
    with bloqueo(carpeta):
        meta_path = carpeta / "vista.json"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text())
            pdf = carpeta / "documento.pdf"
            if meta["tipo"] == "imagen" or (
                pdf.is_file() and sha256_de(pdf) == meta["sha256_pdf"]
            ):
                return carpeta, meta
        with tempfile.TemporaryDirectory(dir=carpeta) as tmp:
            trabajo = Path(tmp)
            if path.suffix.lower() in IMAGEN:
                # No decodificamos ni alteramos el original para servirlo como imagen.
                meta = {"tipo": "imagen", "paginas": 1}
            else:
                pdf = (
                    path
                    if path.suffix.lower() == ".pdf"
                    else convertir_pdf(path, trabajo, hoja)
                )
                try:
                    reader = PdfReader(pdf)
                    cantidad = len(reader.pages)
                except (PyPdfError, ValueError, OSError):
                    raise ErrorDeTrabajo(
                        "El documento no se puede previsualizar. Descarga el original para revisarlo.",
                        422,
                    ) from None
                if not 1 <= cantidad <= MAX_PAGINAS:
                    raise ErrorDeTrabajo(
                        "Este documento supera el límite del visor (100 páginas). Descarga el original para verlo completo.",
                        422,
                    )
                meta = {
                    "tipo": "excel" if hoja else "pdf",
                    "paginas": cantidad,
                    "sha256_pdf": sha256_de(pdf),
                }
                if pdf == path:
                    shutil.copyfile(pdf, trabajo / "documento.pdf")
                os.chmod(trabajo / "documento.pdf", 0o600)
                (trabajo / "documento.pdf").replace(carpeta / "documento.pdf")
            meta.update(receta=RECETA, sha256_original=sha)
            (trabajo / "vista.json").write_text(json.dumps(meta))
            (trabajo / "vista.json").chmod(0o600)
            (trabajo / "vista.json").replace(meta_path)
        return carpeta, meta


def pagina_png(carpeta, meta, pagina):
    if not 1 <= pagina <= meta["paginas"]:
        raise ErrorDeTrabajo("Esa página no existe en el documento.", 404)
    if not shutil.which("pdftoppm"):
        raise ErrorDeTrabajo(
            "El visor de páginas no está disponible. Puedes descargar el original.", 503
        )
    imagen = carpeta / f"pagina-{pagina}.png"
    with bloqueo(carpeta):
        if imagen.is_file():
            return imagen
        with tempfile.TemporaryDirectory(dir=carpeta) as tmp:
            trabajo = Path(tmp)
            ejecutar_aislado(
                [
                    "/usr/bin/pdftoppm",
                    "-f",
                    str(pagina),
                    "-singlefile",
                    "-scale-to",
                    "3200",
                    "-png",
                    "/entrada.pdf",
                    "/trabajo/pagina",
                ],
                carpeta / "documento.pdf",
                trabajo,
            )
            result = trabajo / "pagina.png"
            if not result.is_file():
                raise ErrorDeTrabajo(
                    "No se pudo cargar esa página. Reintenta o descarga el original.",
                    422,
                )
            # Leer dimensiones también comprueba que la salida es una imagen decodificable.
            with Image.open(result) as png:
                png.verify()
            if meta["tipo"] == "excel":
                # Recorta solo margen blanco de la representación, sin alterar el libro.
                with Image.open(result) as png:
                    rgb = png.convert("RGB")
                    borde = ImageChops.difference(
                        rgb, Image.new("RGB", rgb.size, "white")
                    ).getbbox()
                    if borde:
                        x0, y0, x1, y1 = borde
                        rgb.crop(
                            (
                                max(0, x0 - 12),
                                max(0, y0 - 12),
                                min(rgb.width, x1 + 12),
                                min(rgb.height, y1 + 12),
                            )
                        ).save(result)
            result.chmod(0o600)
            result.replace(imagen)
    return imagen
