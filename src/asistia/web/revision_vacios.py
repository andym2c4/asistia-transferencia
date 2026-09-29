"""Clasificación explícita de vacíos seleccionados, en una sola transacción."""

from uuid import UUID

from psycopg.types.json import Jsonb

from . import ErrorDeTrabajo
from .revision import registrar, verificar_edicion, ya_registrada


def clasificar_vacios(conn, rid, seleccion, motivo, huella, operacion, uid):
    try:
        ids = sorted({UUID(str(valor)) for valor in seleccion})
    except (ValueError, TypeError, AttributeError):
        raise ErrorDeTrabajo("La selección contiene un día no válido.") from None
    if not ids:
        raise ErrorDeTrabajo("Selecciona al menos un vacío para revisar.")
    objetivo = {"dias": [str(d) for d in ids], "captura": "NO_APLICA"}
    accion = "CLASIFICAR_VACIOS"
    if ya_registrada(conn, operacion, rid, accion, motivo, objetivo):
        return len(ids)
    verificar_edicion(conn, rid, huella)
    anteriores = conn.execute(
        "SELECT a.* FROM asistencia_dia a JOIN trabajador_en_reporte t USING(trabajador_en_reporte_id) "
        "WHERE a.asistencia_dia_id=ANY(%s) AND t.reporte_asistencia_id=%s "
        "ORDER BY a.asistencia_dia_id FOR UPDATE OF a",
        (ids, rid),
    ).fetchall()
    if len(anteriores) != len(ids) or any(
        d["estado_captura"] != "VACIO" for d in anteriores
    ):
        raise ErrorDeTrabajo(
            "Solo se pueden seleccionar vacíos de este reporte. Recarga y revisa la selección; no se guardó ningún cambio.",
            409,
        )
    evidencia = {
        "dato_no_determinado": True,
        "no_correspondia_asistir": True,
        "fuente": "REVISION_VACIOS_WEB_1",
        "autor": uid,
        "motivo": motivo,
        "operacion": str(operacion),
    }
    posteriores = conn.execute(
        "UPDATE asistencia_dia SET estado_captura='NO_APLICA',estado_asistencia_id=NULL,"
        "codigo_interpretado=NULL,evidencia_interpretacion=%s,hecho_asistencia_dia_id=NULL,"
        "observacion=%s,validado=true,version_extraccion='RRHH_WEB_1' "
        "WHERE asistencia_dia_id=ANY(%s) RETURNING *",
        (Jsonb(evidencia), motivo, ids),
    ).fetchall()
    # No inferir pago, falta, ni cambiar el calendario o la expectativa derivada de él.
    # El valor recibido/localizador quedan intactos; el evento conserva ambos estados.
    conn.execute(
        "UPDATE reporte_asistencia SET estado='EN_VALIDACION',validado_por=NULL,validado_en=NULL "
        "WHERE reporte_asistencia_id=%s",
        (rid,),
    )
    registrar(
        conn,
        operacion,
        rid,
        accion,
        motivo,
        {"dias": anteriores},
        {"dias": posteriores, "objetivo": objetivo},
        uid,
    )
    return len(ids)
