"""Reconstrucción de columnas partidas: solo filas ancladas y cobertura completa.

Un fragmento no es una segunda persona. Dos personas sin ancla compartida no se
unen por parecido de nombre ni por su posición accidental en páginas sucesivas.
"""

import calendar
import copy


def reconstruir_columnas(bloque):
    fragmentos = bloque.get("fragmentos_columnas")
    if not fragmentos:
        return bloque
    b = copy.deepcopy(bloque)
    cantidad = calendar.monthrange(b["anio"], b["mes"])[1]
    filas = {}
    for fragmento in fragmentos:
        for campo in ("anio", "mes", "institucion", "nivel", "turno"):
            if fragmento.get(campo) and b.get(campo) and fragmento[campo] != b[campo]:
                raise ValueError(
                    "Los fragmentos pertenecen a períodos o servicios distintos."
                )
        fuente = {k: fragmento[k] for k in ("pagina", "hoja") if fragmento.get(k)}
        if not fuente:
            raise ValueError("Un fragmento requiere página u hoja de origen.")
        dias = fragmento.get("dias", [])
        if len(set(dias)) != len(dias) or any(
            type(d) is not int or not 1 <= d <= cantidad for d in dias
        ):
            raise ValueError("Encabezado de días inválido en un fragmento.")
        vistas = set()
        for p in fragmento.get("personas", []):
            ancla = str(p.get("id_fila") or "").strip()
            if not ancla or ancla in vistas:
                raise ValueError(
                    "Cada fragmento necesita un ancla de fila única y verificable."
                )
            if not p.get("evidencia_ancla"):
                raise ValueError(
                    "Falta el localizador o sustento del ancla compartida."
                )
            vistas.add(ancla)
            if len(p.get("marcas", [])) != len(dias):
                raise ValueError("Un fragmento tiene columnas diarias incompletas.")
            f = filas.setdefault(
                ancla,
                {
                    "marcas_por_dia": {},
                    "fuentes_fragmentos": [],
                    "localizadores_marcas": {},
                },
            )
            for campo in ("dni", "nombre", "cargo", "condicion", "nivel"):
                valor = p.get(campo)
                if valor and f.get(campo) and f[campo] != valor:
                    raise ValueError(
                        "Dos fragmentos contradicen la identidad de la misma fila."
                    )
                if valor:
                    f[campo] = valor
            localizador = {
                **fuente,
                "fila": p.get("fila"),
                "ancla": ancla,
                "evidencia_ancla": p["evidencia_ancla"],
            }
            f["fuentes_fragmentos"].append(localizador)
            for d, marca in zip(dias, p["marcas"], strict=True):
                if d in f["marcas_por_dia"] and f["marcas_por_dia"][d] != marca:
                    raise ValueError(
                        "Los fragmentos contradicen una marca del mismo día."
                    )
                f["marcas_por_dia"][d] = marca
                f["localizadores_marcas"][str(d)] = localizador
    personas = []
    for orden, f in enumerate(filas.values(), 1):
        if not f.get("dni") and not f.get("nombre"):
            raise ValueError("Fragmento de días sin identidad enlazada.")
        if set(f["marcas_por_dia"]) != set(range(1, cantidad + 1)):
            raise ValueError(
                "La unión de fragmentos no cubre todos los días; no se rellenan huecos."
            )
        f["marcas"] = [f["marcas_por_dia"][d] for d in range(1, cantidad + 1)]
        del f["marcas_por_dia"]
        f["fila"] = orden
        personas.append(f)
    if not personas:
        raise ValueError("Tabla fragmentada sin filas recuperables.")
    if b.get("personas"):
        raise ValueError(
            "Una tabla debe aportar filas completas o fragmentos, sin duplicar ambas representaciones."
        )
    b["personas"] = personas
    b["reconstruccion"] = {
        "regla": "COLUMNAS_POR_ANCLA_1",
        "fragmentos": len(fragmentos),
        "filas": len(personas),
    }
    # La respuesta original de OCR se conserva en su caché; esta copia ya es normalizada.
    del b["fragmentos_columnas"]
    return b
