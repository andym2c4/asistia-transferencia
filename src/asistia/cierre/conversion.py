"""Representación local de Word con gráficos vectoriales; conserva el original."""

import hashlib
import json
import subprocess
from xml.etree import ElementTree
from zipfile import ZipFile

from asistia.configuracion import entorno_local, ruta_interna

from .ingesta import SALIDA, escribir_json


def contiene_vectores(ruta):
    if ruta.suffix.lower() != ".docx":
        return False
    with ZipFile(ruta) as z:
        return any(
            n.startswith("word/media/") and n.lower().endswith((".emf", ".wmf"))
            for n in z.namelist()
        )


def representar_docx(ruta):
    huella = hashlib.sha256(ruta.read_bytes()).hexdigest()
    directorio = SALIDA / "representaciones" / huella
    directorio.mkdir(parents=True, exist_ok=True, mode=0o700)
    destino = directorio / "documento.pdf"
    manifiesto = directorio / "procedencia.json"
    if destino.is_file() and manifiesto.is_file():
        meta = json.loads(manifiesto.read_text())
        if (
            hashlib.sha256(destino.read_bytes()).hexdigest()
            == meta["sha256_representacion"]
        ):
            return destino, meta
    entorno = entorno_local()
    ejecutable = ruta_interna(
        entorno.get("ASISTIA_X2T", "data/runtime/onlyoffice/converter/x2t")
    )
    fuentes = ruta_interna(
        entorno.get("ASISTIA_ONLYOFFICE_FONTS", "data/runtime/onlyoffice/fonts")
    )
    if not ejecutable.is_file() or not (fuentes / "AllFonts.js").is_file():
        raise ValueError(
            "Word contiene EMF/WMF: configurar conversor local ONLYOFFICE y fuentes para leer todas sus imágenes. No se omiten los gráficos."
        )
    # El índice conserva el orden de las fuentes; al trasladar la raíz se regeneran solo rutas.
    plantilla = fuentes / "AllFonts.template.js"
    if plantilla.is_file():
        contenido = plantilla.read_text().replace("__ASISTIA_FONTS__", str(fuentes))
        indice = fuentes / "AllFonts.js"
        if indice.read_text() != contenido:
            indice.write_text(contenido)
    temporal = directorio / "conversion.pdf"
    raiz = ElementTree.Element("TaskQueueDataConvert")
    for k, v in {
        "m_sFileFrom": str(ruta.resolve()),
        "m_sFileTo": str(temporal.resolve()),
        "m_nFormatTo": "513",
        "m_sFontDir": str(fuentes),
        "m_sAllFontsPath": str(fuentes / "AllFonts.js"),
    }.items():
        ElementTree.SubElement(raiz, k).text = v
    parametros = directorio / "conversion.xml"
    ElementTree.ElementTree(raiz).write(
        parametros, encoding="utf-8", xml_declaration=True
    )
    parametros.chmod(0o600)
    try:
        proceso = subprocess.run(
            [str(ejecutable), str(parametros.resolve())],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError(
            "La representación local de Word superó un minuto; el original se conserva."
        ) from None
    if proceso.returncode or not temporal.is_file():
        raise ValueError(
            "No se pudo representar Word con todos sus gráficos; revisar el documento original."
        )
    temporal.chmod(0o600)
    temporal.replace(destino)
    meta = {
        "metodo": "ONLYOFFICE_X2T_DOCX_PDF",
        "version": "cierre-docx-vector-v1",
        "sha256_original": huella,
        "sha256_representacion": hashlib.sha256(destino.read_bytes()).hexdigest(),
        "ruta_representacion": str(destino),
        "nota": "Las páginas citadas corresponden a la representación PDF local del Word original, incluidos sus gráficos EMF/WMF. Cotejo técnico, no aprobación RRHH.",
    }
    escribir_json(manifiesto, meta)
    return destino, meta
