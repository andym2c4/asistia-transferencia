"""Auditar recursos propios sin mostrar credenciales ni localizadores nominales."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from asistia.cierre.gemini import configuracion
from asistia.configuracion import RAIZ_PROYECTO, entorno_local, ruta_interna
from asistia.db import conectar


def main():
    root = RAIZ_PROYECTO
    env = root / ".env"
    assert env.is_file() and env.stat().st_mode & 0o777 == 0o600
    subprocess.run(["git", "check-ignore", "-q", str(env)], cwd=root, check=True)
    claves, _ = configuracion()
    assert claves
    entorno = entorno_local()
    binario = ruta_interna(entorno["ASISTIA_X2T"])
    fonts = ruta_interna(entorno["ASISTIA_ONLYOFFICE_FONTS"])
    texto = (fonts / "AllFonts.js").read_text(encoding="utf-8-sig")
    nombres = json.loads(
        re.search(r'window\["__fonts_files"\]\s*=\s*(\[.*?\]);', texto, re.DOTALL)[1]
    )
    assert all(ruta_interna(n).is_file() for n in nombres)
    ldd = subprocess.check_output(["ldd", str(binario)], text=True)
    assert "not found" not in ldd and "/opt/onlyoffice" not in ldd
    links = [
        p
        for p in (root / "data/runtime/onlyoffice").rglob("*")
        if p.is_symlink() and not p.resolve().is_relative_to(root)
    ]
    assert not links
    with conectar() as c:
        c.execute("SET TRANSACTION READ ONLY")
        objetos = c.execute("SELECT ruta_objeto FROM objeto_archivo").fetchall()
        externos = [
            Path(r["ruta_objeto"])
            for r in objetos
            if not Path(r["ruta_objeto"]).resolve().is_relative_to(root)
        ]
        assert not any(p.exists() for p in externos)
        rutas = c.execute(
            "SELECT ruta AS valor FROM cierre_documento UNION ALL SELECT ruta_copia AS valor FROM web_carga"
        ).fetchall()
        assert all(
            Path(r["valor"]).resolve().is_relative_to(root) for r in rutas if r["valor"]
        )
    resultado = {
        "fecha_utc": datetime.now(UTC).isoformat(),
        "env": {
            "ruta": ".env",
            "modo": "0600",
            "excluido_git": True,
            "claves_gemini": len(claves),
        },
        "conversor": {
            "ruta": str(binario.relative_to(root)),
            "fuentes_locales": len(nombres),
            "bibliotecas_onlyoffice_locales": True,
            "symlinks_externos": len(links),
        },
        "archivos": {
            "objetos_registrados": len(objetos),
            "rutas_operativas_comprobadas": len(rutas),
            "dependencias_vivas_fuera_root": 0,
            "referencias_historicas_ausentes_externas": len(externos),
        },
        "postgresql": "Volumen Docker externo conservado por decisión explícita del usuario.",
        "entorno": "Python, Docker/PostgreSQL y bibliotecas estándar del sistema son dependencias de plataforma; recursos específicos y secretos locales dentro del proyecto.",
    }
    out = root / "docs/evidencias/autonomia-2026-09-15"
    out.mkdir(parents=True, exist_ok=True)
    (out / "auditoria.json").write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(resultado, ensure_ascii=False))


if __name__ == "__main__":
    main()
