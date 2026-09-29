"""Configuración privada anclada a la raíz del proyecto, independiente del cwd."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]


def ruta_interna(ruta: str | Path) -> Path:
    """Resolver recursos propios sin aceptar escapes ni enlaces a otro proyecto."""
    p = Path(ruta)
    p = (p if p.is_absolute() else RAIZ_PROYECTO / p).resolve()
    if not p.is_relative_to(RAIZ_PROYECTO.resolve()):
        raise ValueError(
            "La configuración y sus recursos deben estar dentro de la raíz de ASISTIA."
        )
    return p


def leer_env(ruta: str | Path = ".env") -> dict[str, str]:
    archivo = ruta_interna(ruta)
    if not archivo.exists():
        return {}
    valores = {}
    for numero, linea in enumerate(archivo.read_text().splitlines(), 1):
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if linea.startswith("export "):
            linea = linea[7:].lstrip()
        clave, sep, valor = linea.partition("=")
        clave = clave.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clave):
            raise ValueError(
                f"Formato de configuración inválido en línea {numero}; revisar el archivo local."
            )
        try:
            valores[clave.upper()] = " ".join(shlex.split(valor, comments=True))
        except ValueError:
            raise ValueError(
                f"Comillas incompletas en configuración, línea {numero}."
            ) from None
    return valores


def entorno_local() -> dict[str, str]:
    """Variables explícitas del proceso prevalecen sobre el .env privado local."""
    return {**leer_env(), **os.environ}
