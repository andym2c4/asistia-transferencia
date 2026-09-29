"""Vista de un corte conservado de IF; no entrena ni cambia sus cupos al filtrar."""

from collections import Counter
from math import isfinite
from uuid import UUID

from asistia.niveles import NIVELES

VARIABLES = {
    "dias_justificados_por_registro_conocido": "Días justificados por registro conocido",
    "dias_injustificados_por_registro_conocido": "Días injustificados por registro conocido",
    "horas_no_justificadas_por_registro_conocido": "Horas no justificadas por registro conocido",
    "fraccion_registros_con_injustificadas": "Fracción de registros con injustificadas",
    "fraccion_conteos_desconocidos": "Fracción de conteos desconocidos",
}
ESTADOS = {
    "TODOS": "Todos los perfiles",
    "PRIORIZADO": "Priorizados por IF",
    "FUERA_CUPO": "Fuera del cupo",
    "SIN_DATOS": "Sin datos suficientes",
    "SIN_MODELO": "Sin modelo ejecutado",
    "SIN_PUNTUACION": "Puntuación pendiente",
}
TAMANO_PAGINA = 25


def identificador(valor):
    try:
        return UUID(str(valor))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(
            "La versión solicitada no es válida. Vuelve a Monitoreo."
        ) from None


def experimento_lote(conn, lote_id, experimento_id=None):
    """Selecciona metadatos y resultados, sin deserializar el modelo joblib."""
    if experimento_id:
        exp = conn.execute(
            "SELECT ml_experimento_id,ml_lote_id,creado_en,huella,manifiesto,resultados "
            "FROM ml_experimento WHERE ml_experimento_id=%s AND ml_lote_id=%s",
            (identificador(experimento_id), lote_id),
        ).fetchone()
        if not exp:
            raise ValueError(
                "El experimento no pertenece a este lote o ya no está disponible."
            )
        return exp
    return conn.execute(
        "SELECT ml_experimento_id,ml_lote_id,creado_en,huella,manifiesto,resultados "
        "FROM ml_experimento WHERE ml_lote_id=%s "
        "ORDER BY creado_en DESC,ml_experimento_id DESC LIMIT 1",
        (lote_id,),
    ).fetchone()


def _conteos(filas):
    estados = Counter(r["estado_monitor"] for r in filas)
    return {
        "total": len(filas),
        "puntuados": estados["PRIORIZADO"] + estados["FUERA_CUPO"],
        "priorizados": estados["PRIORIZADO"],
        "fuera_cupo": estados["FUERA_CUPO"],
        "sin_datos": estados["SIN_DATOS"],
        "sin_modelo": estados["SIN_MODELO"],
        "sin_puntuacion": estados["SIN_PUNTUACION"],
    }


def preparar_panel(
    reportes,
    experimento,
    *,
    mes=None,
    nivel="TODOS",
    estado=None,
    q="",
    pagina=1,
    estados_adicionales=None,
):
    meses = sorted({str(r["periodo"])[:7] for r in reportes})
    mes = mes or (meses[-1] if meses else "")
    estado = estado or ("PRIORIZADO" if experimento else "TODOS")
    if mes not in meses and not (not meses and mes == ""):
        raise ValueError("Selecciona un mes disponible en este lote.")
    estados_adicionales = estados_adicionales or {}
    if nivel not in {"TODOS", *NIVELES} or estado not in (
        ESTADOS | estados_adicionales
    ):
        raise ValueError("Selecciona un nivel y un estado disponibles en Monitoreo.")
    q = q.strip()
    if len(q) > 120:
        raise ValueError("La búsqueda admite hasta 120 caracteres.")
    try:
        pagina = int(pagina)
        if pagina < 1:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("La página debe ser un número entero positivo.") from None
    resultados = experimento["resultados"] if experimento else []
    scores = {s["reporte_id"]: s for s in resultados}
    ids = {str(r["ml_reporte_mensual_id"]) for r in reportes}
    if len(scores) != len(resultados) or not set(scores) <= ids:
        raise ValueError(
            "Las puntuaciones no corresponden a un corte único del lote. Abre su expediente para revisar la versión."
        )
    filas = []
    for reporte in reportes:
        r = dict(reporte)
        score = scores.get(str(r["ml_reporte_mensual_id"]))
        if not r["datos"]["apto_modelo"]:
            estado_reporte = "SIN_DATOS"
            score = None
        elif not experimento:
            estado_reporte = "SIN_MODELO"
        elif (
            score is None
            or not isinstance(score.get("score_if"), (int, float))
            or not isfinite(score["score_if"])
        ):
            estado_reporte = "SIN_PUNTUACION"
            score = None
        else:
            estado_reporte = "PRIORIZADO" if score["en_cupo"] else "FUERA_CUPO"
        r.update(puntuacion=score, estado_monitor=estado_reporte)
        filas.append(r)
    del_nivel = [r for r in filas if nivel == "TODOS" or r["nivel"] == nivel]
    evolucion = []
    for periodo in meses:
        grupo = [r for r in del_nivel if str(r["periodo"])[:7] == periodo]
        origenes = sorted(
            {
                str(r["periodo_fuente"])[:7]
                for r in filas
                if str(r["periodo"])[:7] == periodo
            }
        )
        evolucion.append({"mes": periodo, "origenes": origenes, **_conteos(grupo)})
    poblacion = [r for r in del_nivel if str(r["periodo"])[:7] == mes]
    visibles = [
        r
        for r in poblacion
        if (
            estado == "TODOS"
            or r["estado_monitor"] == estado
            or (estado in estados_adicionales and estados_adicionales[estado](r))
        )
        and (
            not q
            or q.casefold()
            in f"{r['nombre_ie']} {r['cod_mod']} {r['anexo']}".casefold()
        )
    ]
    visibles.sort(
        key=lambda r: (
            r["puntuacion"]["puesto"] if r["puntuacion"] else float("inf"),
            r["cod_mod"],
            r["anexo"],
        )
    )
    paginas = max(1, (len(visibles) + TAMANO_PAGINA - 1) // TAMANO_PAGINA)
    pagina = min(pagina, paginas)
    origen = next((e["origenes"] for e in evolucion if e["mes"] == mes), [])
    referencia = (
        experimento["manifiesto"].get("por_mes_origen", {}) if experimento else {}
    )
    return {
        "meses": meses,
        "mes_elegido": mes,
        "nivel_filtro": nivel,
        "estado_filtro": estado,
        "q": q,
        "pagina": pagina,
        "paginas": paginas,
        "visibles": len(visibles),
        "filas": visibles[(pagina - 1) * TAMANO_PAGINA : pagina * TAMANO_PAGINA],
        "resumen": _conteos(poblacion),
        "evolucion": evolucion,
        "escala": max((e["total"] for e in evolucion), default=0) or 1,
        "meses_fuente": origen,
        "referencias_mes": [
            {"mes": m, **referencia[m]} for m in origen if m in referencia
        ],
    }
