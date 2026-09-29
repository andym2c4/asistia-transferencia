"""Vista mensual y asignación explícita: una edición produce un borrador trazable."""

import calendar
import uuid
from datetime import UTC, date, datetime

from psycopg.types.json import Jsonb

from asistia.leyendas import CLASIFICACION_SIN_ASIGNAR, categoria_dia

from . import ErrorDeTrabajo
from .lecturas import uno
from .leyendas import huella_calendario
from .revision import json_datos

MESES = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)
GRUPOS = {
    "LECTIVO": "Lectivo",
    "GESTION": "Gestión",
    "NO_LECTIVO_NI_GESTION": "No lectivo ni de gestión",
}
SIN_ASIGNAR = "__SIN_ASIGNAR__"


def opciones_dias(cv):
    return [
        {"codigo": SIN_ASIGNAR, "sin_asignar": True, **CLASIFICACION_SIN_ASIGNAR}
    ] + [
        {"codigo": codigo, **c}
        for codigo, c in sorted(cv["clasificacion_codigos"].items())
        if codigo != SIN_ASIGNAR
        and c.get("tipo_dia")
        and c.get("grupo_actividad") in GRUPOS
        and not c.get("revision_leyenda")
        and not c.get("conflictos")
        and not c.get("conflicto_catalogo")
    ]


def meses_calendario(cv, dias):
    por_fecha = {d["fecha"]: d for d in dias}
    disponibles = {c["codigo"] for c in opciones_dias(cv)}
    meses = []
    for mes, nombre in enumerate(MESES, 1):
        semanas = []
        for semana in calendar.Calendar(firstweekday=6).monthdatescalendar(
            cv["anio"], mes
        ):
            celdas = []
            for fecha in semana:
                if fecha.month != mes:
                    celdas.append(None)
                    continue
                d = por_fecha.get(fecha)
                c = categoria_dia(cv, d) if d else {}
                codigo = (
                    (d.get("codigo_interpretado") or d.get("codigo_reportado_raw"))
                    if d
                    else None
                )
                # Una excepción diaria puede conservar una regla anterior a la leyenda actual.
                inicial = (
                    codigo
                    if codigo in disponibles
                    and d["estado_captura"] == "REGISTRADO"
                    and c == cv["clasificacion_codigos"].get(codigo)
                    and d.get("grupo_recibido") == c.get("grupo_actividad")
                    else ""
                )
                if d and d["estado_captura"] == "SIN_ASIGNAR":
                    inicial = SIN_ASIGNAR
                celdas.append(
                    {
                        "fecha": fecha,
                        "dia": d,
                        "clasificacion": c,
                        "inicial": inicial,
                        "grupo": d.get("grupo_recibido") or c.get("grupo_actividad")
                        if d and d["estado_captura"] == "REGISTRADO"
                        else "",
                        "pendiente": not d or d["estado_captura"] != "REGISTRADO",
                    }
                )
            semanas.append(celdas)
        meses.append({"numero": mes, "nombre": nombre, "semanas": semanas})
    return meses


def guardar_dias(conn, cvid, formulario, motivo, huella, operacion, uid):
    cv = uno(
        conn,
        "SELECT cv.*,cl.anio FROM calendarizacion_version cv JOIN calendarizacion_local cl USING(calendarizacion_local_id) WHERE calendarizacion_version_id=%s FOR UPDATE OF cv,cl",
        (cvid,),
    )
    objetivo = {}
    for campo in formulario:
        if not campo.startswith("dia_"):
            continue
        valores = formulario.getlist(campo)
        if len(valores) != 1:
            raise ErrorDeTrabajo("Se recibió más de una categoría para la misma fecha.")
        codigo = valores[0]
        if not codigo:
            continue  # Conservar el estado recibido, incluso vacío o ilegible.
        texto = campo[4:]
        try:
            fecha = date.fromisoformat(texto)
        except ValueError:
            raise ErrorDeTrabajo("Hay una fecha de calendario no válida.") from None
        if fecha.isoformat() != texto or fecha.year != cv["anio"]:
            raise ErrorDeTrabajo(
                "Solo puedes asignar fechas del año de este calendario."
            )
        objetivo[texto] = codigo
    if not motivo or len(motivo) > 4000:
        raise ErrorDeTrabajo("Escribe el motivo y sustento de la asignación.")
    previo = conn.execute(
        "SELECT calendarizacion_version_id,procedencia_extraccion->'revision_dias' AS r FROM calendarizacion_version "
        "WHERE procedencia_extraccion->'revision_dias'->>'operacion'=%s "
        "AND procedencia_extraccion->'revision_dias'->>'resultado'=calendarizacion_version_id::text",
        (str(operacion),),
    ).fetchone()
    if previo:
        r = previo["r"]
        if (
            r["padre"],
            r["objetivo"],
            r["motivo"],
            r["autor"],
            r.get("huella_reglas"),
        ) != (
            str(cvid),
            objetivo,
            motivo,
            uid,
            formulario.get("huella_reglas"),
        ):
            raise ErrorDeTrabajo(
                "Ese envío ya corresponde a otra asignación de días.", 409
            )
        return previo["calendarizacion_version_id"]
    posterior = conn.execute(
        "SELECT 1 FROM calendarizacion_version WHERE calendarizacion_local_id=%s AND version>%s",
        (cv["calendarizacion_local_id"], cv["version"]),
    ).fetchone()
    if posterior or huella != huella_calendario(conn, cvid):
        raise ErrorDeTrabajo(
            "Hay cambios posteriores en el calendario. Abre la versión actual en otra pestaña y compara antes de guardar.",
            409,
        )
    if cv["estado"] in {"HISTORICA", "RECHAZADA"}:
        raise ErrorDeTrabajo(
            "Esta versión solo permite consulta. Abre la versión actual.", 409
        )
    reglas_anteriores = cv["clasificacion_codigos"]
    if formulario.get("huella_reglas"):
        from .calendario_revision import propuesta

        revision = propuesta(conn, cvid)
        if revision["huella"] != formulario["huella_reglas"]:
            raise ErrorDeTrabajo(
                "Las reglas de código cambiaron. Recarga y compara antes de guardar los días.",
                409,
            )
        cv = {**cv, "clasificacion_codigos": revision["categorias"]}
    opciones = {c["codigo"]: c for c in opciones_dias(cv)}
    if any(codigo not in opciones for codigo in objetivo.values()):
        raise ErrorDeTrabajo(
            "Selecciona Sin asignar o una categoría definida y con actividad determinada en la leyenda de este calendario."
        )
    dias = {
        str(d["fecha"]): d
        for d in conn.execute(
            "SELECT * FROM dia_calendarizacion WHERE calendarizacion_version_id=%s ORDER BY fecha",
            (cvid,),
        ).fetchall()
    }
    tipos = {
        t["codigo_interno"]: t["tipo_dia_id"]
        for t in conn.execute("SELECT * FROM catalogo_tipo_dia WHERE activo").fetchall()
    }
    cambios = {}
    for fecha, codigo in objetivo.items():
        d = dias.get(fecha)
        sin_asignar = codigo == SIN_ASIGNAR
        c = (
            CLASIFICACION_SIN_ASIGNAR
            if sin_asignar
            else cv["clasificacion_codigos"][codigo]
        )
        estado = "SIN_ASIGNAR" if sin_asignar else "REGISTRADO"
        tipo = None if sin_asignar else tipos[c["grupo_actividad"]]
        interpretado = None if sin_asignar else codigo
        if (
            d
            and d["estado_captura"] == estado
            and (
                d["codigo_interpretado"]
                if sin_asignar
                else d["codigo_interpretado"] or d["codigo_reportado_raw"]
            )
            == interpretado
            and categoria_dia(cv, d) == c
            and d["tipo_dia_id"] == tipo
        ):
            continue
        cambios[fecha] = {
            "codigo": codigo,
            "anterior": json_datos(d),
            "clasificacion": c,
        }
    if not cambios:
        raise ErrorDeTrabajo(
            "No hay cambios de categoría para guardar. Asigna al menos un día."
        )
    nuevo = uuid.uuid4()
    instante = datetime.now(UTC).isoformat()
    revision = {
        "padre": str(cvid),
        "resultado": str(nuevo),
        "autor": uid,
        "motivo": motivo,
        "operacion": str(operacion),
        "fecha": instante,
        "objetivo": objetivo,
        "cambios": cambios,
        "huella_reglas": formulario.get("huella_reglas"),
        "reglas_anteriores": reglas_anteriores,
        "reglas_aplicadas": cv["clasificacion_codigos"],
    }
    meta = {**cv["procedencia_extraccion"], "revision_dias": revision}
    conn.execute(
        """INSERT INTO calendarizacion_version(calendarizacion_version_id,calendarizacion_local_id,version,version_padre_id,
        documento_recibido_id,fuente_derivada_id,origen,estado,motivo_version,procedencia_extraccion,clasificacion_codigos,
        total_lectivo_reportado_raw,total_gestion_reportado_raw,total_no_lectivo_reportado_raw,horas_lectivas_reportadas_raw,creado_por)
        SELECT %s,calendarizacion_local_id,version+1,calendarizacion_version_id,documento_recibido_id,fuente_derivada_id,
        'CORRECCION_UGEL','BORRADOR',%s,%s,clasificacion_codigos,total_lectivo_reportado_raw,total_gestion_reportado_raw,
        total_no_lectivo_reportado_raw,horas_lectivas_reportadas_raw,%s FROM calendarizacion_version WHERE calendarizacion_version_id=%s""",
        (nuevo, "Asignación de días: " + motivo, Jsonb(meta), uid, cvid),
    )
    conn.execute(
        """INSERT INTO dia_calendarizacion(calendarizacion_version_id,fecha,tipo_dia_id,codigo_reportado_raw,estado_captura,
        hoja_origen,celda_origen,observacion,confianza,modelo_extraccion,version_extraccion,codigo_interpretado,evidencia_interpretacion)
        SELECT %s,fecha,tipo_dia_id,codigo_reportado_raw,estado_captura,hoja_origen,celda_origen,observacion,confianza,
        modelo_extraccion,version_extraccion,codigo_interpretado,evidencia_interpretacion FROM dia_calendarizacion WHERE calendarizacion_version_id=%s""",
        (nuevo, cvid),
    )
    if formulario.get("huella_reglas"):
        from asistia.leyendas import aplicar_leyenda_calendario

        aplicar_leyenda_calendario(
            conn, nuevo, cv["clasificacion_codigos"], usar_catalogo=False
        )
    for fecha, cambio in cambios.items():
        anterior = dias.get(fecha)
        c = cambio["clasificacion"]
        evidencia = {
            **(anterior["evidencia_interpretacion"] if anterior else {}),
            "clasificacion_aceptada": c,
            "revision_dia": {
                "autor": uid,
                "motivo": motivo,
                "fecha": instante,
                "operacion": str(operacion),
                "padre": str(cvid),
                "codigo": cambio["codigo"],
                "anterior": cambio["anterior"],
            },
        }
        conn.execute(
            """INSERT INTO dia_calendarizacion(calendarizacion_version_id,fecha,tipo_dia_id,estado_captura,codigo_interpretado,evidencia_interpretacion)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(calendarizacion_version_id,fecha) DO UPDATE SET
            tipo_dia_id=EXCLUDED.tipo_dia_id,estado_captura=EXCLUDED.estado_captura,codigo_interpretado=EXCLUDED.codigo_interpretado,
            evidencia_interpretacion=EXCLUDED.evidencia_interpretacion""",
            (
                nuevo,
                fecha,
                tipos.get(c["grupo_actividad"]),
                "SIN_ASIGNAR" if cambio["codigo"] == SIN_ASIGNAR else "REGISTRADO",
                None if cambio["codigo"] == SIN_ASIGNAR else cambio["codigo"],
                Jsonb(evidencia),
            ),
        )
    return nuevo
