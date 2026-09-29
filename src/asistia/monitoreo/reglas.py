"""Contrato de cruces: actividad, declaración y remuneración son ejes distintos."""

from asistia.leyendas import normal

VERSION = "CRUCES_DIARIOS_3"
REGLAS = {
    "INASISTENCIA_EN_GESTION": "Inasistencia declarada en día de gestión: cotejar incidencia",
    "NO_APLICA_CON_ACTIVIDAD": "No correspondía asistir frente a actividad programada",
    "PRESENCIA_SIN_ACTIVIDAD": "Presencia declarada en día sin actividad esperada",
    "NO_LABORABLE_CON_ACTIVIDAD": "No laborable declarado en día con actividad esperada",
    "INASISTENCIA_SIN_ACTIVIDAD": "Inasistencia declarada en día sin actividad esperada",
    "REMUNERACION_CONTRADICTORIA": "Asistencia remunerada en día no remunerado",
}
BASICAS = {
    "fraccion_presencia": "Presencias entre declaraciones interpretables",
    "fraccion_inasistencia": "Inasistencias entre declaraciones interpretables",
    "fraccion_licencia_permiso": "Licencias y permisos entre declaraciones interpretables",
    "fraccion_asistencia_desconocida": "Declaraciones sin interpretación",
}
CRUCES = {
    "tasa_inasistencia_en_gestion": "Inasistencias declaradas en días de gestión",
    "tasa_no_aplica_con_actividad": "No correspondía asistir frente a actividad programada",
    "tasa_presencia_sin_actividad": "Presencias en días sin actividad esperada",
    "tasa_no_laborable_con_actividad": "No laborables declarados en días con actividad",
    "tasa_inasistencia_sin_actividad": "Inasistencias en días sin actividad esperada",
    "tasa_remuneracion_contradictoria": "Cruces de remuneración contradictorios",
    "fraccion_cruce_no_evaluable": "Cruces de actividad sin información suficiente",
}
VARIABLES = BASICAS | CRUCES


def conflicto(categoria):
    from asistia.calidad_leyendas import pendiente

    return bool(
        pendiente(categoria)
        or categoria.get("conflictos")
        or categoria.get("conflicto_catalogo")
        or categoria.get("fuente") in {"LEYENDA_EN_CONFLICTO", "CONFLICTO_CATALOGO"}
    )


def declaracion(categoria):
    """Solo significado explícito; nunca inferir presencia desde remuneración o letra."""
    if not categoria or conflicto(categoria):
        return None
    codigo = categoria.get("estado_asistencia_codigo")
    texto = normal(categoria.get("tipo_dia"))
    if codigo in {"A", "T"}:
        return "PRESENCIA"
    if codigo in {"L", "LSG", "P", "PCG"} or any(
        t in texto for t in ("LICENCIA", "PERMISO")
    ):
        return "LICENCIA_PERMISO"
    if codigo == "F" or any(
        t in texto
        for t in (
            "FERIADO",
            "NO LABORABLE",
            "DESCANSO",
            "VACACIONES",
            "SABADO",
            "DOMINGO",
        )
    ):
        return "NO_LABORABLE"
    if codigo in {"I", "J", "3T", "H"}:
        return "INASISTENCIA"
    if codigo in {"C", "U"}:
        return "OTRA_ACTIVIDAD"
    return None


def actividad(categoria):
    if conflicto(categoria):
        return None
    return {"LECTIVO": True, "GESTION": True, "NO_LECTIVO_NI_GESTION": False}.get(
        categoria.get("grupo_actividad")
    )


def evaluar(calendario, asistencia, *, impedimento=None, no_aplica=False):
    """Resultado diario de revisión, no una clasificación de falta ni pago."""
    tipo = "NO_APLICA" if no_aplica else declaracion(asistencia)
    esperado = actividad(calendario)
    cruce_evaluable = impedimento is None and esperado is not None and tipo is not None
    remuneracion_evaluable = (
        impedimento is None
        and not no_aplica
        and not conflicto(calendario)
        and not conflicto(asistencia)
        and type(calendario.get("es_remunerado")) is bool
        and type(asistencia.get("es_remunerado")) is bool
    )
    alertas = []
    if cruce_evaluable:
        if esperado and no_aplica:
            alertas.append("NO_APLICA_CON_ACTIVIDAD")
        if calendario.get("grupo_actividad") == "GESTION" and tipo == "INASISTENCIA":
            alertas.append("INASISTENCIA_EN_GESTION")
        if not esperado and tipo == "PRESENCIA":
            alertas.append("PRESENCIA_SIN_ACTIVIDAD")
        if esperado and tipo == "NO_LABORABLE":
            alertas.append("NO_LABORABLE_CON_ACTIVIDAD")
        if not esperado and tipo == "INASISTENCIA":
            alertas.append("INASISTENCIA_SIN_ACTIVIDAD")
    if (
        remuneracion_evaluable
        and not calendario["es_remunerado"]
        and asistencia["es_remunerado"]
    ):
        alertas.append("REMUNERACION_CONTRADICTORIA")
    return {
        "declaracion": tipo,
        "actividad_esperada": esperado,
        "cruce_evaluable": cruce_evaluable,
        "remuneracion_evaluable": remuneracion_evaluable,
        "alertas": alertas,
        "pendiente": impedimento
        or (
            "CATEGORIA_CALENDARIO_PENDIENTE"
            if esperado is None
            else "CATEGORIA_ASISTENCIA_PENDIENTE"
            if tipo is None
            else None
        ),
    }


def razon(numerador, denominador):
    return {
        "numerador": numerador,
        "denominador": denominador,
        "valor": numerador / denominador if denominador else None,
        "estado": "CALCULABLE" if denominador else "NO_APLICA",
    }


def agregar(dias):
    """Una oportunidad = persona/IE/rol/fecha; excluidos fuera de razones."""
    incluidos = [d for d in dias if not d.get("exclusion")]
    conocidos = [d for d in incluidos if d["declaracion"] is not None]
    evaluables = [d for d in incluidos if d["cruce_evaluable"]]
    no_actividad = [d for d in evaluables if d["actividad_esperada"] is False]
    con_actividad = [d for d in evaluables if d["actividad_esperada"] is True]
    pago = [d for d in incluidos if d["remuneracion_evaluable"]]
    gestion = [
        d
        for d in evaluables
        if d.get("calendario", {}).get("grupo_actividad") == "GESTION"
    ]
    componentes = {
        "tasa_inasistencia_en_gestion": razon(
            sum("INASISTENCIA_EN_GESTION" in d["alertas"] for d in gestion),
            len(gestion),
        ),
        "tasa_no_aplica_con_actividad": razon(
            sum("NO_APLICA_CON_ACTIVIDAD" in d["alertas"] for d in con_actividad),
            len(con_actividad),
        ),
        "fraccion_presencia": razon(
            sum(d["declaracion"] == "PRESENCIA" for d in conocidos), len(conocidos)
        ),
        "fraccion_inasistencia": razon(
            sum(d["declaracion"] == "INASISTENCIA" for d in conocidos), len(conocidos)
        ),
        "fraccion_licencia_permiso": razon(
            sum(d["declaracion"] == "LICENCIA_PERMISO" for d in conocidos),
            len(conocidos),
        ),
        "fraccion_asistencia_desconocida": razon(
            len(incluidos) - len(conocidos), len(incluidos)
        ),
        "tasa_presencia_sin_actividad": razon(
            sum("PRESENCIA_SIN_ACTIVIDAD" in d["alertas"] for d in no_actividad),
            len(no_actividad),
        ),
        "tasa_no_laborable_con_actividad": razon(
            sum("NO_LABORABLE_CON_ACTIVIDAD" in d["alertas"] for d in con_actividad),
            len(con_actividad),
        ),
        "tasa_inasistencia_sin_actividad": razon(
            sum("INASISTENCIA_SIN_ACTIVIDAD" in d["alertas"] for d in no_actividad),
            len(no_actividad),
        ),
        "tasa_remuneracion_contradictoria": razon(
            sum("REMUNERACION_CONTRADICTORIA" in d["alertas"] for d in pago), len(pago)
        ),
        "fraccion_cruce_no_evaluable": razon(
            len(incluidos) - len(evaluables), len(incluidos)
        ),
    }
    apto = len(evaluables) >= 5 and len(evaluables) >= 0.5 * len(incluidos)
    return {
        "componentes": componentes,
        "features": {f: c["valor"] for f, c in componentes.items()},
        "apto_modelo": apto,
        "motivo_exclusion_modelo": None
        if apto
        else "Se requieren al menos 5 cruces de actividad evaluables y 50 % de las oportunidades incluidas.",
        "oportunidades": len(dias),
        "incluidas": len(incluidos),
        "excluidas": len(dias) - len(incluidos),
        "cruces_evaluables": len(evaluables),
        "dias_con_alerta": sum(bool(d["alertas"]) for d in incluidos),
        "alertas_por_regla": {
            r: sum(r in d["alertas"] for d in incluidos) for r in REGLAS
        },
    }
