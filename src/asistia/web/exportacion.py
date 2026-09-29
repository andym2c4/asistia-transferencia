"""Descarga principal derivada del Excel conservado, sin recalcular ni escribirlo."""

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import openpyxl


def primera_hoja(ruta: Path) -> BytesIO:
    libro = openpyxl.load_workbook(ruta, keep_links=False)
    try:
        for hoja in list(libro)[1:]:
            libro.remove(hoja)
        libro.active = 0
        libro.worksheets[0].sheet_view.tabSelected = True
        for vista in libro.views:
            vista.activeTab = 0
            vista.firstSheet = 0
        intermedio = BytesIO()
        libro.save(intermedio)
    finally:
        libro.close()
    # openpyxl actualiza la fecha de modificación al guardar. Conservar los
    # metadatos del original y fijar los tiempos ZIP hace estable esta descarga.
    destino = BytesIO()
    with (
        ZipFile(ruta) as original,
        ZipFile(intermedio) as generado,
        ZipFile(destino, "w", compression=ZIP_DEFLATED) as salida,
    ):
        for entrada in generado.infolist():
            contenido = (
                original.read(entrada.filename)
                if entrada.filename == "docProps/core.xml"
                and entrada.filename in original.namelist()
                else generado.read(entrada.filename)
            )
            entrada.date_time = (1980, 1, 1, 0, 0, 0)
            salida.writestr(entrada, contenido)
    destino.seek(0)
    return destino
