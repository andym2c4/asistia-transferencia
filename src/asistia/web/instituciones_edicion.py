"""Mantenimiento explícito del servicio educativo: identidad textual y cambios trazables."""

import hashlib
import json
import re
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from . import ErrorDeTrabajo
from .lecturas import uno

CAMPOS = {
    "cod_mod": ("Código modular", 7, True),
    "anexo": ("Anexo", 4, True),
    "nombre_ie": ("Nombre de la institución", 500, True),
    "nivel_modalidad": ("Nivel / modalidad", 80, True),
    "departamento": ("Departamento", 60, True),
    "provincia": ("Provincia", 60, True),
    "distrito": ("Distrito", 60, True),
    "centro_poblado": ("Centro poblado", 500, False),
    "direccion": ("Dirección", 1000, False),
}


def leer(conn, iid):
    return uno(
        conn,
        "SELECT * FROM institucion_educativa WHERE institucion_educativa_id=%s",
        (iid,),
    )


def huella(ie):
    def estable(valor):
        return (
            valor.astimezone(UTC).isoformat()
            if isinstance(valor, datetime)
            else str(valor)
        )

    return hashlib.sha256(
        json.dumps(ie, sort_keys=True, default=estable).encode()
    ).hexdigest()


def validar(form):
    datos = {}
    for campo, (etiqueta, limite, obligatorio) in CAMPOS.items():
        valor = form.get(campo, "").strip()
        if obligatorio and not valor:
            raise ErrorDeTrabajo(f"Completa {etiqueta.lower()}.")
        if len(valor) > limite:
            raise ErrorDeTrabajo(f"{etiqueta} admite hasta {limite} caracteres.")
        datos[campo] = valor or None
    if not re.fullmatch(r"[0-9]{7}", datos["cod_mod"]):
        raise ErrorDeTrabajo(
            "El código modular debe tener exactamente 7 dígitos, incluidos los ceros iniciales."
        )
    if not re.fullmatch(r"[0-9]{1,4}", datos["anexo"]):
        raise ErrorDeTrabajo("El anexo debe tener entre 1 y 4 dígitos.")
    return datos


def registrar(
    conn,
    op,
    iid,
    accion,
    solicitud,
    anterior,
    nuevo,
    autor,
    responsable,
    motivo,
    fuente=None,
):
    # Serializa fechas sin perder sus valores en las instantáneas conservadas.
    def jsonb(valor):
        return Jsonb(json.loads(json.dumps(valor, default=str)))

    conn.execute(
        """INSERT INTO institucion_cambio
        (operacion_id,institucion_educativa_id,accion,solicitud,valores_anteriores,valores_nuevos,autor,responsable,motivo,fuente)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            op,
            iid,
            accion,
            jsonb(solicitud),
            jsonb(anterior) if anterior else None,
            jsonb(nuevo),
            autor,
            responsable,
            motivo,
            jsonb(fuente or {}),
        ),
    )


def guardar(conn, *, iid, form, op, autor, responsable):
    datos = validar(form)
    motivo = form.get("motivo", "").strip()
    if not motivo or len(motivo) > 4000:
        raise ErrorDeTrabajo(
            "Indica el motivo y la fuente del registro o de la corrección (hasta 4000 caracteres)."
        )
    solicitud = {
        "iid": iid,
        "datos": datos,
        "motivo": motivo,
        "huella": form.get("huella", ""),
    }
    previo = conn.execute(
        "SELECT * FROM institucion_cambio WHERE operacion_id=%s", (op,)
    ).fetchone()
    if previo:
        if previo["solicitud"] != solicitud or previo["autor"] != autor:
            raise ErrorDeTrabajo(
                "Esta solicitud ya se utilizó con otros datos. Abre un formulario nuevo.",
                409,
            )
        return previo["institucion_educativa_id"]
    anterior = None
    if iid is not None:
        anterior = uno(
            conn,
            "SELECT * FROM institucion_educativa WHERE institucion_educativa_id=%s FOR UPDATE",
            (iid,),
        )
        if form.get("huella") != huella(anterior):
            raise ErrorDeTrabajo(
                "La institución cambió desde que abriste el formulario. Tus datos siguen aquí. Abre la versión actual en otra pestaña y compara antes de volver a editar.",
                409,
            )
    duplicado = conn.execute(
        "SELECT institucion_educativa_id FROM institucion_educativa WHERE cod_mod=%s AND anexo=%s AND (%s::bigint IS NULL OR institucion_educativa_id<>%s)",
        (datos["cod_mod"], datos["anexo"], iid, iid),
    ).fetchone()
    if duplicado:
        raise ErrorDeTrabajo(
            "Ya existe una institución con ese código modular y anexo. Abre su ficha para editarla.",
            409,
        )
    if anterior is None:
        # Referencia sin identificación de local físico: no agrupar por nombre/ubicación.
        local = conn.execute(
            "INSERT INTO local_educativo DEFAULT VALUES RETURNING local_educativo_id"
        ).fetchone()["local_educativo_id"]
        iid = conn.execute(
            """INSERT INTO institucion_educativa
            (local_educativo_id,cod_mod,anexo,nombre_ie,nivel_modalidad,departamento,provincia,distrito,centro_poblado,direccion,
             forma_atencion,caracteristica_docente,gestion,gestion_dependencia,area_censal,turno,region,estado)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'','','','','','','','') RETURNING institucion_educativa_id""",
            (local, *(datos[k] for k in CAMPOS)),
        ).fetchone()["institucion_educativa_id"]
    else:
        # Solo se actualizan los campos mostrados; fuentes, asociaciones y demás atributos se conservan.
        conn.execute(
            """UPDATE institucion_educativa SET cod_mod=%s,anexo=%s,nombre_ie=%s,nivel_modalidad=%s,
            departamento=%s,provincia=%s,distrito=%s,centro_poblado=%s,direccion=%s WHERE institucion_educativa_id=%s""",
            (*(datos[k] for k in CAMPOS), iid),
        )
    registrar(
        conn,
        op,
        iid,
        "CREAR" if anterior is None else "EDITAR",
        solicitud,
        anterior,
        leer(conn, iid),
        autor,
        responsable,
        motivo,
    )
    return iid


def historial(conn, iid):
    cambios = conn.execute(
        "SELECT * FROM institucion_cambio WHERE institucion_educativa_id=%s ORDER BY creado_en DESC,operacion_id",
        (iid,),
    ).fetchall()
    for cambio in cambios:
        antes = cambio["valores_anteriores"] or {}
        despues = cambio["valores_nuevos"]
        cambio["detalle"] = [
            {"campo": CAMPOS[k][0], "antes": antes.get(k), "despues": despues.get(k)}
            for k in CAMPOS
            if antes.get(k) != despues.get(k)
        ]
    return cambios
