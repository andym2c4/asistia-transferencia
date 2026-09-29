"""Relectura y división recuperable de PDF sin perder las páginas del original."""

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .gemini import VERSION
from .ingesta import SALIDA, bloques_normalizados, escribir_json, validar_bloque


def comprobar(item, resultado):
    from .pdf_digital import cotejar_pdf_digital

    resultado = cotejar_pdf_digital(item["ruta"], resultado)
    for bloque in bloques_normalizados(item, resultado):
        validar_bloque(bloque)
    return resultado


def extraer_documento(motor, item, contexto):
    ruta = Path(item["ruta"])
    from .conversion import contiene_vectores, representar_docx

    if contiene_vectores(ruta):
        representacion, meta = representar_docx(ruta)
        resultado = extraer_documento(
            motor, {**item, "ruta": str(representacion)}, contexto + "\n" + meta["nota"]
        )
        resultado = json.loads(json.dumps(resultado))
        resultado["sha256"] = meta["sha256_original"]
        resultado["conversion"] = meta
        for bloque in resultado["datos"]["bloques"]:
            bloque.setdefault("supuestos", []).append(meta["nota"])
        return resultado
    relectura = item.get("contexto_tecnico", {}).get("relectura_ocr")
    if relectura:
        contexto += f"\nNueva lectura solicitada para cotejar las unidades pendientes: {relectura}."
    # Una respuesta conservada pero inválida no debe ocultar una lectura válida de otro modelo.
    incompletas = []
    for modelo in getattr(motor, "modelos", []):
        cache = motor.destino_cache(ruta, contexto, modelo)
        if cache.is_file():
            try:
                return comprobar(item, json.loads(cache.read_text()))
            except (ValueError, TypeError, KeyError):
                incompletas.append(json.loads(cache.read_text()))
                continue
    from .parciales import recuperar_parcial

    for incompleta in incompletas:
        parcial = recuperar_parcial(item, incompleta)
        if parcial:
            return parcial
    # La respuesta completa puede estar conservada como unión de páginas aunque
    # nunca haya existido una respuesta válida del PDF entero.
    if ruta.suffix.lower() == ".pdf" and ruta.is_file():
        huella = hashlib.sha256(ruta.read_bytes()).hexdigest()
        cache_paginas = (
            SALIDA
            / "fragmentos"
            / huella
            / (
                hashlib.sha256((VERSION + "|" + contexto).encode()).hexdigest()
                + ".json"
            )
        )
        if cache_paginas.is_file():
            conservada = json.loads(cache_paginas.read_text())
            try:
                return comprobar(item, conservada)
            except (ValueError, TypeError):
                parcial = recuperar_parcial(item, conservada)
                if parcial:
                    return parcial
    if ruta.suffix.lower() == ".pdf" and len(PdfReader(ruta).pages) > 200:
        raise ValueError(
            "PDF supera 200 páginas: revisar impresión, páginas repetidas y contenido útil antes de solicitar OCR. El original se conserva."
        )
    try:
        resultado = motor.extraer(ruta, contexto)
        return comprobar(item, resultado)
    except (ValueError, TypeError) as exc:
        if "resultado" in locals():
            parcial = recuperar_parcial(item, resultado)
            if parcial:
                return parcial
        if ruta.suffix.lower() == ".pdf" and len(PdfReader(ruta).pages) > 1:
            return extraer_paginas(motor, item, contexto)
        resultado = motor.extraer(
            ruta,
            contexto
            + "\nRelectura completa del original por error de formato: "
            + str(exc)
            + f". Modelo solicitado para esta relectura: {getattr(motor, 'modelo', 'actual')}"
            + ". Verifica año, número de días de cada mes y cada trabajador; no añadir valores para rellenar una omisión.",
        )
        return comprobar(item, resultado)


def extraer_paginas(motor, item, contexto):
    ruta = Path(item["ruta"])
    origen = hashlib.sha256(ruta.read_bytes()).hexdigest()
    directorio = SALIDA / "fragmentos" / origen
    directorio.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache = directorio / (
        hashlib.sha256((VERSION + "|" + contexto).encode()).hexdigest() + ".json"
    )
    if cache.is_file():
        return comprobar(item, json.loads(cache.read_text()))
    documento = PdfReader(ruta)
    bloques = []
    fuentes = []
    advertencias = []
    for n, pagina in enumerate(documento.pages, 1):
        fragmento = directorio / f"pagina_{n:04d}.pdf"
        if not fragmento.exists():
            escritor = PdfWriter()
            escritor.add_page(pagina)
            with fragmento.open("xb") as salida:
                fragmento.chmod(0o600)
                escritor.write(salida)
        respuesta = motor.extraer(
            fragmento,
            contexto
            + f"\nFragmento: contiene únicamente la página física {n} de {len(documento.pages)} del original. Transcribir todos los bloques de esta página, sin reconstruir lo que falte. Modelo de relectura: {getattr(motor, 'modelo', 'actual')}.",
        )
        for bloque in respuesta["datos"]["bloques"]:
            bloque = json.loads(json.dumps(bloque))
            bloque["pagina_reportada_por_motor"] = bloque.get("pagina")
            bloque["pagina"] = n
            bloques.append(bloque)
        advertencias.extend(respuesta["datos"].get("advertencias", []))
        fuentes.append(
            {
                "pagina_original": n,
                "sha256_fragmento": respuesta["sha256"],
                "modelo": respuesta["modelo"],
                "version": respuesta["version"],
            }
        )
    # Solo propagar un encabezado cuando todo el documento identifica un único valor.
    for tipo in ("asistencia", "calendario"):
        del_tipo = [b for b in bloques if b.get("tipo") == tipo]
        for campo in ("institucion", "cod_mod", "anexo", "nivel", "anio", "mes"):
            valores = {b[campo] for b in del_tipo if b.get(campo) not in (None, "")}
            if len(valores) != 1:
                continue
            valor = next(iter(valores))
            for b in del_tipo:
                if b.get(campo) in (None, ""):
                    b[campo] = valor
                    b.setdefault("supuestos", []).append(
                        f"{campo} no visible en esta página; se usa el único valor explícito de los otros bloques del mismo documento."
                    )
    modelos = sorted({f["modelo"] for f in fuentes})
    resultado = {
        "datos": {"bloques": bloques, "advertencias": advertencias},
        "sha256": origen,
        "modelo": ", ".join(modelos),
        "version": "cierre-fragmentos-v1",
        "fragmentos": fuentes,
        "contexto": contexto,
    }
    try:
        comprobar(item, resultado)
    except (ValueError, TypeError):
        from .parciales import recuperar_parcial

        parcial = recuperar_parcial(item, resultado)
        if not parcial:
            raise
        resultado = parcial
    escribir_json(cache, resultado)
    return resultado
