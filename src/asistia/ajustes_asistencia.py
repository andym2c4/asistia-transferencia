"""Ajustes operativos de RRHH, separados de la lectura y de las señales documentales."""

import copy

from asistia.experimental.datos import huella
from asistia.leyendas import categoria_dia


def huella_declarada(reporte, dia):
    return huella(
        {
            "dia": str(dia.get("asistencia_dia_id")),
            "fecha": str(dia.get("fecha")),
            "recibido": dia.get("codigo_reportado_raw"),
            "interpretado": dia.get("codigo_interpretado"),
            "captura": dia.get("estado_captura"),
            "categoria": categoria_dia(reporte, dia),
        }
    )


def ajuste_del_dia(reporte, dia):
    ajuste = dia.get("evidencia_interpretacion", {}).get("ajuste_rrhh")
    if not ajuste:
        return None
    return {
        **ajuste,
        "vigente": ajuste["huella_declarada"] == huella_declarada(reporte, dia),
    }


def preparar_ajuste(reporte, dia, opcion):
    """Mismo valor operativo para la vista previa y el guardado con auditoría."""
    return {
        "contrato": "AJUSTE_ASISTENCIA_RRHH_1",
        "opcion": opcion["id"],
        "etiqueta": opcion["etiqueta"],
        "categoria": opcion["categoria"],
        "codigo_interpretado": opcion["codigo"],
        "estado_asistencia_id": opcion["estado_asistencia_id"],
        "estado_captura": "NO_APLICA" if opcion["id"] == "NO_APLICA" else "REGISTRADO",
        "huella_declarada": huella_declarada(reporte, dia),
    }


def marca_efectiva(reporte, dia):
    """Proyección para cruce operativo/consolidado; nunca modifica la fila recibida."""
    ajuste = ajuste_del_dia(reporte, dia)
    if not ajuste or not ajuste["vigente"]:
        return dia
    nuevo = copy.deepcopy(dia)
    nuevo.update(
        {
            k: ajuste[k]
            for k in ("codigo_interpretado", "estado_captura", "estado_asistencia_id")
        }
    )
    nuevo["evidencia_interpretacion"] = {
        **nuevo.get("evidencia_interpretacion", {}),
        "clasificacion_aceptada": ajuste["categoria"],
        "dato_no_determinado": False,
    }
    return nuevo
