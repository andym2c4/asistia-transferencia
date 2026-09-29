"""Rectificación operativa explícita; conserva la digitalización y sus originales."""

from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from asistia import categorias
from asistia.ajustes_asistencia import ajuste_del_dia, preparar_ajuste
from asistia.coherencia import revisar_reportes
from asistia.experimental.datos import huella
from asistia.leyendas import categoria_dia, interpretar_descripcion, normal
from asistia.monitoreo.reglas import conflicto, declaracion

from . import ErrorDeTrabajo
from .lecturas import _instantes_canonicos
from .revision import json_datos, registrar, verificar_edicion, ya_registrada


def opciones(conn, reporte):
    resultado = []
    for regla in categorias.catalogo(conn, "asistencia"):
        categoria = categorias.componer(
            interpretar_descripcion(regla["nombre"], "asistencia"),
            regla,
            normal(regla["nombre"]),
        )
        resultado.append(
            {
                "id": f"global:{regla['categoria_id']}",
                "codigo": categoria.get("estado_asistencia_codigo"),
                "categoria": categoria,
                "origen": "Regla global",
            }
        )
    for codigo, categoria in reporte["clasificacion_codigos"].items():
        resultado.append(
            {
                "id": f"local:{codigo}",
                "codigo": codigo,
                "categoria": categoria,
                "origen": "Regla del reporte",
            }
        )
    disponibles = []
    for opcion in resultado:
        c = opcion["categoria"]
        if (
            conflicto(c)
            or not declaracion(c)
            or type(c.get("es_falta")) is not bool
            or type(c.get("es_remunerado")) is not bool
        ):
            continue
        estado = conn.execute(
            "SELECT estado_asistencia_id FROM catalogo_estado_asistencia WHERE codigo=%s AND activo",
            (c.get("estado_asistencia_codigo"),),
        ).fetchone()
        if not estado:
            continue
        opcion.update(estado)
        opcion["etiqueta"] = c["tipo_dia"]
        opcion["detalle"] = (
            f"{opcion['origen']} · {'Cuenta como falta' if c['es_falta'] else 'No cuenta como falta'} · {'Remunerado' if c['es_remunerado'] else 'No remunerado'}"
        )
        disponibles.append(opcion)
    disponibles.extend(
        [
            {
                "id": "NO_APLICA",
                "etiqueta": "No correspondía asistir",
                "detalle": "Sin falta; se coteja con la actividad del calendario",
                "codigo": None,
                "categoria": {
                    "tipo_dia": "No correspondía asistir",
                    "es_falta": False,
                    "es_remunerado": None,
                },
                "estado_asistencia_id": None,
            },
            {
                "id": "RESTAURAR",
                "etiqueta": "Volver a lo declarado",
                "detalle": "Retira el ajuste y vuelve a comprobar la declaración original",
            },
        ]
    )
    return {
        "opciones": disponibles,
        "huella": huella(_instantes_canonicos(json_datos(disponibles))),
    }


def guardar(conn, rid, formulario, motivo, operacion, uid):
    objetivo = {
        k: formulario.get(k)
        for k in ("dia", "valor", "huella_opciones", "huella_cruce")
    }
    objetivo["autor"] = uid
    if ya_registrada(conn, operacion, rid, "AJUSTAR_ASISTENCIA_RRHH", motivo, objetivo):
        return
    reporte = verificar_edicion(conn, rid, formulario.get("huella"))
    if reporte["estado"] != "VALIDADO":
        raise ErrorDeTrabajo(
            "Confirma primero la digitalización del reporte; este ajuste conserva su lectura.",
            409,
        )
    cruce = revisar_reportes(conn, [reporte])[str(rid)]
    if formulario.get("huella_cruce") != cruce["huella"]:
        raise ErrorDeTrabajo("El cruce cambió. Recarga y vuelve a cotejar el día.", 409)
    contexto = opciones(conn, reporte)
    if formulario.get("huella_opciones") != contexto["huella"]:
        raise ErrorDeTrabajo(
            "Las reglas disponibles cambiaron. Recarga antes de ajustar.", 409
        )
    opcion = next(
        (o for o in contexto["opciones"] if o["id"] == formulario.get("valor")), None
    )
    if not opcion:
        raise ErrorDeTrabajo("Selecciona el significado que comprobaste para ese día.")
    dia = conn.execute(
        "SELECT a.* FROM asistencia_dia a JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id) WHERE a.asistencia_dia_id::text=%s AND t.reporte_asistencia_id=%s FOR UPDATE OF a",
        (formulario.get("dia"), rid),
    ).fetchone()
    if not dia:
        raise ErrorDeTrabajo("Selecciona un día de este reporte.")
    previo = ajuste_del_dia(reporte, dia)
    evidencia = dict(dia["evidencia_interpretacion"])
    ajuste = None
    if opcion["id"] == "RESTAURAR":
        if not previo:
            raise ErrorDeTrabajo(
                "Este día ya conserva lo declarado; no tiene un ajuste que retirar."
            )
        evidencia.pop("ajuste_rrhh", None)
    else:
        autor = conn.execute(
            "SELECT nombre FROM usuario WHERE usuario_id=%s", (uid,)
        ).fetchone()["nombre"]
        ajuste = {
            **preparar_ajuste(reporte, dia, opcion),
            "operacion": str(operacion),
            "autor": autor,
            "usuario_id": uid,
            "fecha": datetime.now(UTC).isoformat(),
            "motivo": motivo,
        }
        evidencia["ajuste_rrhh"] = ajuste
    conn.execute(
        "UPDATE asistencia_dia SET evidencia_interpretacion=%s WHERE asistencia_dia_id=%s",
        (Jsonb(json_datos(evidencia)), dia["asistencia_dia_id"]),
    )
    registrar(
        conn,
        operacion,
        rid,
        "AJUSTAR_ASISTENCIA_RRHH",
        motivo,
        {
            "dia": dia,
            "categoria_declarada": categoria_dia(reporte, dia),
            "ajuste": previo,
        },
        {
            "objetivo": objetivo,
            "fecha": dia["fecha"],
            "ajuste": ajuste,
            "etiqueta": opcion["etiqueta"],
        },
        uid,
        persona=dia["trabajador_en_reporte_id"],
    )
