"""Originales y salidas locales conservados por contenido; nunca se sirven rutas del cliente."""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from asistia.db import sha256_de

from . import ErrorDeTrabajo

MAX_ARCHIVO = 20 * 1024 * 1024


def conservar(archivo):
    root = current_app.config["STORAGE_ROOT"]
    nombre = secure_filename(archivo.filename or "")[:180] or "documento.bin"
    h = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(dir=root, delete=False) as temporal:
        ruta_tmp = Path(temporal.name)
        try:
            for bloque in iter(lambda: archivo.stream.read(1024 * 1024), b""):
                size += len(bloque)
                if size > MAX_ARCHIVO:
                    raise ErrorDeTrabajo(
                        "El archivo supera 20 MB. Selecciona uno más pequeño.", 413
                    )
                h.update(bloque)
                temporal.write(bloque)
            temporal.flush()
            os.fsync(temporal.fileno())
            if size == 0:
                raise ErrorDeTrabajo(
                    "El archivo está vacío. Selecciona el original recibido."
                )
            sha = h.hexdigest()
            carpeta = root / "originales" / sha
            carpeta.mkdir(parents=True, exist_ok=True, mode=0o700)
            destino = carpeta / nombre
            try:
                os.link(ruta_tmp, destino)
                destino.chmod(0o400)
            except FileExistsError:
                if sha256_de(destino) != sha:
                    raise ErrorDeTrabajo(
                        "La copia conservada cambió. Es necesario revisar el almacenamiento.",
                        409,
                    )
            return destino, sha, size
        finally:
            ruta_tmp.unlink(missing_ok=True)


def comprobar_formato(ruta: Path, tipo: str):
    permitidos = {
        "nexus": {".xlsx", ".xls"},
        "calendario": {
            ".xlsx",
            ".xls",
            ".xlsm",
            ".pdf",
            ".docx",
            ".jpg",
            ".jpeg",
            ".png",
        },
        "asistencia": {
            ".xlsx",
            ".xls",
            ".xlsm",
            ".pdf",
            ".docx",
            ".jpg",
            ".jpeg",
            ".png",
        },
    }
    ext = ruta.suffix.lower()
    if ext not in permitidos[tipo]:
        return "Formato aún no soportado para esta carga. El original se conserva; selecciona un archivo del formato indicado."
    if ext in {".xlsx", ".xlsm", ".docx"}:
        try:
            with zipfile.ZipFile(ruta) as z:
                infos = z.infolist()
                if (
                    len(infos) > 5000
                    or sum(i.file_size for i in infos) > 100 * 1024 * 1024
                ):
                    return "El Excel descomprimido supera el límite de procesamiento (100 MB o 5000 partes)."
                esperado = "word/document.xml" if ext == ".docx" else "xl/workbook.xml"
                if esperado not in z.namelist():
                    return "El contenido no corresponde a un libro Excel válido."
        except zipfile.BadZipFile:
            return "No se pudo abrir este Excel. Puede estar dañado o tener una extensión incorrecta."
    if ext == ".pdf":
        with ruta.open("rb") as f:
            if not f.read(5).startswith(b"%PDF-"):
                return "El contenido no corresponde a un archivo PDF válido."
    if ext in {".png", ".jpg", ".jpeg"}:
        cabecera = ruta.read_bytes()[:8]
        if (ext == ".png" and cabecera != b"\x89PNG\r\n\x1a\n") or (
            ext != ".png" and not cabecera.startswith(b"\xff\xd8\xff")
        ):
            return "El contenido no corresponde a una imagen del formato indicado."
    return None


def ruta_verificada(ruta: str, sha: str) -> Path:
    path = Path(ruta).resolve()
    roots = [
        current_app.config["STORAGE_ROOT"],
        *map(Path, current_app.config["ORIGINAL_ROOTS"]),
    ]
    if not any(path.is_relative_to(r.resolve()) for r in roots):
        raise ErrorDeTrabajo(
            "El original está fuera del almacenamiento autorizado. Vuelve a cargar una copia del archivo.",
            409,
        )
    if not path.is_file() or sha256_de(path) != sha:
        raise ErrorDeTrabajo(
            "No se pudo verificar el original. El archivo falta o cambió; vuelve a cargar su copia sin alterar la anterior.",
            409,
        )
    return path
