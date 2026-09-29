"""Copiar conversor y fuentes instalados a ASISTIA; ejecución de preparación local."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--origen",
        type=Path,
        required=True,
        help="Directorio que contiene x2t y bibliotecas",
    )
    parser.add_argument(
        "--indice-fuentes",
        type=Path,
        required=True,
        help="AllFonts.js de la instalación origen",
    )
    args = parser.parse_args()
    destino = ROOT / "data/runtime/onlyoffice"
    destino.mkdir(parents=True, exist_ok=True)
    if (destino / "manifiesto.json").exists():
        raise SystemExit(
            "El conversor local ya está preparado; no se sobrescribe su versión."
        )
    if not (args.origen / "x2t").is_file():
        raise SystemExit("El origen no contiene x2t.")
    shutil.copytree(
        args.origen, destino / "converter", dirs_exist_ok=True, symlinks=False
    )
    for recurso in ["editors/sdkjs", "editors/web-apps/vendor/xregexp", "dictionaries"]:
        shutil.copytree(
            args.origen.parent / recurso,
            destino / recurso,
            dirs_exist_ok=True,
            symlinks=False,
        )
    licencia = args.origen.parent / "LICENSE.txt"
    if licencia.exists():
        shutil.copy2(licencia, destino / "LICENSE.txt")
    texto = args.indice_fuentes.read_text(encoding="utf-8-sig")
    bloque = re.search(r'window\["__fonts_files"\]\s*=\s*(\[.*?\]);', texto, re.DOTALL)
    if not bloque:
        raise SystemExit("No se reconoce el índice de fuentes instalado.")
    fuentes = json.loads(bloque[1])
    carpeta = destino / "fonts"
    (carpeta / "files").mkdir(parents=True, exist_ok=True)
    propias = []
    for i, nombre in enumerate(fuentes):
        origen = Path(nombre)
        if not origen.is_file():
            raise FileNotFoundError(
                "Falta una fuente requerida por el índice; preparación incompleta."
            )
        nombre_local = f"{i:04d}{origen.suffix.lower()}"
        shutil.copy2(origen, carpeta / "files" / nombre_local)
        propias.append("__ASISTIA_FONTS__/files/" + nombre_local)
    plantilla = (
        texto[: bloque.start(1)]
        + json.dumps(propias, ensure_ascii=False)
        + texto[bloque.end(1) :]
    )
    (carpeta / "AllFonts.template.js").write_text(plantilla)
    (carpeta / "AllFonts.js").write_text(
        plantilla.replace("__ASISTIA_FONTS__", str(carpeta))
    )
    archivos = [p for p in destino.rglob("*") if p.is_file()]
    manifiesto = {
        "metodo": "COPIA_REAL_DE_CONVERSOR_Y_FUENTES",
        "fuentes": len(fuentes),
        "archivos": {
            str(p.relative_to(destino)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(archivos)
        },
        "bytes": sum(p.stat().st_size for p in archivos),
        "dependencias_sistema": "Linux x86_64, glibc y bibliotecas estándar; bibliotecas de ONLYOFFICE incluidas",
    }
    (destino / "manifiesto.json").write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "directorio": str(destino.relative_to(ROOT)),
                "fuentes": len(fuentes),
                "archivos": len(archivos),
                "bytes": manifiesto["bytes"],
            }
        )
    )


if __name__ == "__main__":
    main()
