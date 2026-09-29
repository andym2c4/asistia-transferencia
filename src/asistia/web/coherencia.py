"""Decisión auditada sobre señales; jamás reescribe la declaración firmada."""

from asistia.coherencia import DECISIONES, revisar_reportes

from . import ErrorDeTrabajo
from .revision import registrar, verificar_edicion, ya_registrada


def decidir(conn, rid, formulario, motivo, operacion, uid):
    claves = sorted(set(formulario.getlist("casos")))
    decision = formulario.get("decision")
    if not claves or decision not in DECISIONES:
        raise ErrorDeTrabajo("Selecciona los casos y la decisión que corresponde.")
    objetivo = {
        "casos": claves,
        "decision": decision,
        "huella": formulario.get("huella_cruce"),
        "autor": uid,
    }
    if ya_registrada(conn, operacion, rid, "REVISAR_COHERENCIA", motivo, objetivo):
        return
    r = verificar_edicion(conn, rid, formulario.get("huella"))
    if r["estado"] in {"HISTORICA", "RECHAZADO"}:
        raise ErrorDeTrabajo(
            "Esta versión es solo de consulta. Abre el reporte actual.", 409
        )
    cruce = revisar_reportes(conn, [r])[str(rid)]
    if cruce["huella"] != formulario.get("huella_cruce"):
        raise ErrorDeTrabajo(
            "El calendario, el reporte o la revisión del cruce cambiaron. Recarga y coteja las fuentes antes de guardar.",
            409,
        )
    por_id = {c["id"]: c for c in cruce["casos"]}
    if any(c not in por_id for c in claves):
        raise ErrorDeTrabajo("La selección contiene un caso ajeno a este cruce.", 409)
    registrar(
        conn,
        operacion,
        rid,
        "REVISAR_COHERENCIA",
        motivo,
        {"calendario": cruce["calendario"], "casos": [por_id[c] for c in claves]},
        {
            "objetivo": objetivo,
            "casos": claves,
            "decision": decision,
            "fechas": sorted({por_id[c]["fecha"] for c in claves}),
            "contrato": cruce["contrato"],
        },
        uid,
    )
