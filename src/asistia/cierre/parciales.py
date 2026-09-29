"""Recupera unidades completas; ninguna omisión se rellena con valores supuestos."""

import json
from collections import Counter

from .ingesta import bloques_normalizados, validar_bloque


def recuperar_parcial(item, resultado):
    copia = json.loads(json.dumps(resultado))
    validos, pendientes, excluidos = [], [], []
    for n, bloque in enumerate(bloques_normalizados(item, resultado), 1):
        n = bloque.get("indice_bloque_original", n)
        bloque["indice_bloque_original"] = n
        try:
            validar_bloque(bloque)
            validos.append(bloque)
            continue
        except (ValueError, TypeError) as exc:
            motivo = str(exc)
        campo = "meses" if bloque.get("tipo") == "calendario" else "personas"
        unidades = bloque.get(campo, [])
        aceptadas = []
        meses = Counter(u.get("mes") for u in unidades) if campo == "meses" else {}
        for i, unidad in enumerate(unidades, 1):
            try:
                if campo == "meses" and meses[unidad.get("mes")] > 1:
                    raise ValueError(
                        "Mes repetido en el bloque; conservar las alternativas sin elegir una silenciosamente."
                    )
                validar_bloque({**bloque, campo: [unidad]})
                aceptadas.append(unidad)
            except (ValueError, TypeError) as exc:
                pendientes.append(
                    {
                        "bloque_original": n,
                        "pagina": bloque.get("pagina"),
                        "hoja": bloque.get("hoja"),
                        "unidad": campo,
                        "indice": i,
                        "mes": unidad.get("mes"),
                        "fila": unidad.get("fila"),
                        "motivo": str(exc),
                    }
                )
                excluidos.append({"bloque_original": n, "indice": i, "datos": unidad})
        if aceptadas:
            validos.append({**bloque, campo: aceptadas})
        elif not unidades:
            pendientes.append(
                {
                    "bloque_original": n,
                    "pagina": bloque.get("pagina"),
                    "hoja": bloque.get("hoja"),
                    "motivo": motivo,
                }
            )
            excluidos.append({"bloque_original": n, "datos": bloque})
    if not pendientes or not any(
        b.get("tipo") in {"asistencia", "calendario"} for b in validos
    ):
        return None
    nota = "Extracción parcial: se cargan únicamente meses o filas con estructura diaria completa. Las unidades omitidas se conservan para revisión; no se han rellenado marcas."
    for b in validos:
        if b.get("tipo") in {"asistencia", "calendario"}:
            b.setdefault("supuestos", []).append(nota)
    copia["datos"]["bloques"] = validos
    copia["datos"].setdefault("advertencias", []).append(nota)
    copia["pendientes_extraccion"] = pendientes
    copia["datos_no_aplicados"] = excluidos
    return copia
