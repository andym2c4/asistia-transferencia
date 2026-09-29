"""Reglas generales disponibles durante la clasificación explícita de un reporte."""

import hashlib
import json

from asistia import categorias as cat
from asistia.leyendas import normal

from . import ErrorDeTrabajo


def contexto_catalogo(conn, dominio="asistencia"):
    reglas = cat.catalogo(conn, dominio)
    por_nombre = {normal(r["nombre"]): r["categoria_id"] for r in reglas}
    por_nombre.update(
        {e["significado"]: e["categoria_id"] for e in cat.equivalencias(conn, dominio)}
    )
    opciones = [
        {
            k: r[k]
            for k in ("categoria_id", "nombre", "version", "es_remunerado", "es_falta")
        }
        | {"version_id": str(r["categoria_version_id"])}
        | ({"grupo_actividad": r["grupo_actividad"]} if dominio == "calendario" else {})
        for r in reglas
    ]
    huella = hashlib.sha256(
        json.dumps([opciones, por_nombre], sort_keys=True).encode()
    ).hexdigest()
    return {"reglas": opciones, "equivalencias": por_nombre, "huella": huella}


def aplicar_regla_formulario(conn, formulario, clasificacion, dominio="asistencia"):
    if formulario.get("modo_regla") != "auto":
        return clasificacion
    contexto = contexto_catalogo(conn, dominio)
    if formulario.get("huella_catalogo") != contexto["huella"]:
        raise ErrorDeTrabajo(
            "Las reglas de código cambiaron. Recarga el documento para usar la versión actual.",
            409,
        )
    elegido = formulario.get("categoria_global", "")
    try:
        cid = (
            int(elegido)
            if elegido
            else contexto["equivalencias"].get(normal(clasificacion["tipo_dia"]))
        )
    except (TypeError, ValueError):
        raise ErrorDeTrabajo("Selecciona una categoría global válida.") from None
    if cid is None:
        return clasificacion
    try:
        regla = cat.categoria(conn, cid)
    except ValueError as exc:
        raise ErrorDeTrabajo(str(exc)) from None
    if regla["dominio"] != dominio:
        raise ErrorDeTrabajo(f"Selecciona una regla de {dominio}.")
    conflicto = cat.incompatibilidad(clasificacion, regla)
    if conflicto:
        raise ErrorDeTrabajo(conflicto, 409)
    return cat.componer(clasificacion, regla, normal(clasificacion["tipo_dia"]))
