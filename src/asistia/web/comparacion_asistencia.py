"""Comparación diaria y propuesta calculada en memoria, sin persistir decisiones."""

import copy

from asistia.ajustes_asistencia import ajuste_del_dia, marca_efectiva, preparar_ajuste
from asistia.coherencia import MOTIVOS, revisar_reportes
from asistia.consolidado.remuneracion import cruce_persona
from asistia.leyendas import categoria_dia
from asistia.monitoreo.datos import cargar_fuentes, construir
from asistia.monitoreo.reglas import REGLAS, actividad

from . import ErrorDeTrabajo, lecturas
from .ajustes_asistencia import opciones


def _marca(reporte, dia):
    categoria = categoria_dia(reporte, dia)
    no_aplica = dia["estado_captura"] == "NO_APLICA"
    return {
        "codigo": "—"
        if no_aplica
        else (dia.get("codigo_interpretado") or dia.get("codigo_reportado_raw") or "∅"),
        "significado": "No correspondía asistir"
        if no_aplica
        else categoria.get("tipo_dia") or "Sin significado confirmado",
        "captura": dia["estado_captura"],
        "falta": categoria.get("es_falta"),
        "remunerado": categoria.get("es_remunerado"),
    }


def _dias(corte, tid):
    return {
        d["fecha"]: d
        for p in construir(
            corte, reglas_aplicadas=True, exigir_vigente=True, ajustes_rrhh=True
        )
        for d in p["datos"]["dias"]
        if any(f["trabajador_en_reporte_id"] == str(tid) for f in d["fuentes"])
    }


def _estado(evaluado, remuneracion):
    if not evaluado["cruce_evaluable"]:
        return "pendiente"
    if evaluado["alertas"] or remuneracion["resultado"] == "OBSERVACION":
        return "alerta"
    return "pendiente" if remuneracion["resultado"] == "PENDIENTE" else "compatible"


def comparar(conn, rid, parametros):
    reporte = lecturas.reporte(conn, rid)
    if (
        reporte["estado"] != "VALIDADO"
        or conn.execute(
            "SELECT 1 FROM reporte_asistencia WHERE reporte_asistencia_serie_id=%s AND version>%s",
            (reporte["reporte_asistencia_serie_id"], reporte["version"]),
        ).fetchone()
    ):
        raise ErrorDeTrabajo(
            "Confirma la digitalización de la versión actual antes de ajustar.", 409
        )
    if parametros.get("huella") != lecturas.huella_reporte(conn, rid):
        raise ErrorDeTrabajo(
            "El reporte cambió. Recarga para comparar los valores actuales.", 409
        )
    cruce = revisar_reportes(conn, [reporte])[str(rid)]
    catalogo = opciones(conn, reporte)
    if (
        parametros.get("huella_cruce") != cruce["huella"]
        or parametros.get("huella_opciones") != catalogo["huella"]
    ):
        raise ErrorDeTrabajo(
            "El calendario, el cruce o las reglas cambiaron. Recarga antes de ajustar.",
            409,
        )
    dia = conn.execute(
        "SELECT a.*,t.nombres_reportados_raw FROM asistencia_dia a JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id) WHERE a.asistencia_dia_id::text=%s AND t.reporte_asistencia_id=%s",
        (parametros.get("dia"), rid),
    ).fetchone()
    if not dia:
        raise ErrorDeTrabajo("Selecciona un día de este reporte.")
    if dia["fecha"].replace(day=1) != reporte["periodo"]:
        raise ErrorDeTrabajo(
            "La fecha está fuera del mes reportado. Comprueba su digitalización antes de consolidar."
        )
    valor = parametros.get("valor") or ""
    propuesto = copy.deepcopy(dia)
    guardado = ajuste_del_dia(reporte, dia)
    if valor:
        opcion = next((o for o in catalogo["opciones"] if o["id"] == valor), None)
        if not opcion:
            raise ErrorDeTrabajo("Selecciona un significado disponible para ese día.")
        if valor == "RESTAURAR":
            if not guardado:
                raise ErrorDeTrabajo("Este día no tiene un ajuste que retirar.")
            propuesto["evidencia_interpretacion"].pop("ajuste_rrhh", None)
        else:
            propuesto["evidencia_interpretacion"]["ajuste_rrhh"] = preparar_ajuste(
                reporte, dia, opcion
            )
    corte = cargar_fuentes(conn, reporte["periodo"].year, reporte_ids=[rid])
    tid = dia["trabajador_en_reporte_id"]
    dias_guardados = _dias(corte, tid)
    cruce_guardado = cruce_persona(conn, tid)
    pagos_guardados = {d["fecha"]: d for d in cruce_guardado["dias"]}
    mes = []
    for d in corte["dias"]:
        if (
            d["trabajador_en_reporte_id"] != tid
            or str(d["fecha"]) not in dias_guardados
        ):
            continue
        evaluado = dias_guardados[str(d["fecha"])]
        mes.append(
            {
                "id": str(d["asistencia_dia_id"]),
                "fecha": str(d["fecha"]),
                **_marca(reporte, marca_efectiva(reporte, d)),
                "ajustado": bool(ajuste_del_dia(reporte, d)),
                "estado": _estado(evaluado, pagos_guardados[str(d["fecha"])]),
            }
        )
    if valor:
        corte["dias"] = [
            propuesto if d["asistencia_dia_id"] == dia["asistencia_dia_id"] else d
            for d in corte["dias"]
        ]
        evaluado = _dias(corte, tid)[str(dia["fecha"])]
    else:
        evaluado = dias_guardados[str(dia["fecha"])]
    remuneracion = (
        cruce_persona(conn, tid, dia_propuesto=propuesto) if valor else cruce_guardado
    )
    resultado = next(d for d in remuneracion["dias"] if d["fecha"] == str(dia["fecha"]))
    motivos = [REGLAS[c] for c in evaluado["alertas"]]
    if evaluado["pendiente"]:
        motivos.insert(0, MOTIVOS.get(evaluado["pendiente"], evaluado["pendiente"]))
    if resultado["resultado"] in {"PENDIENTE", "OBSERVACION"}:
        motivos.append(resultado["motivo"])
    estado = _estado(evaluado, resultado)
    caso = next((c for c in cruce["casos"] if c["clave"] == evaluado["clave"]), None)
    return {
        "dia": str(dia["asistencia_dia_id"]),
        "fecha": str(dia["fecha"]),
        "persona": dia["nombres_reportados_raw"],
        "propuesta": bool(valor),
        "valor": valor,
        "calendario": {
            "codigo": evaluado["codigo_calendario"] or "∅",
            "significado": evaluado["calendario"].get("tipo_dia")
            or "Sin categoría confirmada",
            "actividad": actividad(evaluado["calendario"]),
            "remunerado": evaluado["calendario"].get("es_remunerado"),
            "estado": remuneracion["estado_calendario"] or "Sin calendario",
            "version": (cruce["calendario"] or {}).get("version"),
        },
        "declarado": _marca(reporte, dia),
        "final": {
            **_marca(reporte, marca_efectiva(reporte, propuesto)),
            "falta": resultado["es_falta"],
            "remunerado": resultado["es_remunerado"],
            "resultado": resultado["resultado"],
            "motivo": resultado["motivo"],
        },
        "guardado": {
            "marca": _marca(reporte, marca_efectiva(reporte, dia)),
            "ajustado": bool(guardado),
            "vigente": guardado["vigente"] if guardado else True,
            "autor": guardado.get("autor") if guardado else None,
            "fecha": guardado.get("fecha") if guardado else None,
        },
        "cruce": {
            "estado": estado,
            "motivos": list(dict.fromkeys(motivos)),
            "caso_resuelto": bool(caso and caso["resuelto"]),
            "decision": caso["decision"]["estado"]
            if caso and caso["decision"]
            else None,
        },
        "mes": sorted(mes, key=lambda d: d["fecha"]),
    }
