"""Categorías compartidas y copia explícita de reglas sobre leyendas locales."""

from __future__ import annotations

import copy

from psycopg.types.json import Jsonb

from asistia.leyendas import interpretar_descripcion, normal

DOMINIOS = {"asistencia": "Asistencia", "calendario": "Calendarios"}
VIGENTES = """SELECT c.*,v.* FROM leyenda_categoria c JOIN LATERAL
    (SELECT * FROM leyenda_categoria_version v WHERE v.categoria_id=c.categoria_id
     ORDER BY version DESC LIMIT 1) v ON true"""


def catalogo(conn, dominio=None):
    return conn.execute(
        VIGENTES + (" WHERE c.dominio=%s" if dominio else "") + " ORDER BY c.nombre",
        (dominio,) if dominio else (),
    ).fetchall()


def categoria(conn, cid):
    r = conn.execute(VIGENTES + " WHERE c.categoria_id=%s", (cid,)).fetchone()
    if not r:
        raise ValueError("La categoría ya no está disponible. Vuelve al catálogo.")
    return r


def equivalencias(conn, dominio):
    return conn.execute(
        "SELECT * FROM leyenda_equivalencia WHERE dominio=%s ORDER BY descripcion",
        (dominio,),
    ).fetchall()


def fuente_original(c):
    return copy.deepcopy(c.get("clasificacion_fuente", c))


def incompatibilidad(c, regla):
    from asistia.calidad_leyendas import pendiente

    if pendiente(c):
        return "La alineación de la leyenda está pendiente. Revisa los códigos en el documento."
    fuente = fuente_original(c)
    if fuente.get("conflictos") or fuente.get("fuente") == "LEYENDA_EN_CONFLICTO":
        return "El código tiene significados contradictorios en la fuente."
    pago = fuente.get("es_remunerado")
    if fuente.get("fuente") == "REVISION_WEB":
        # La inclusión explícita permite sustituir una decisión local, pero no
        # una indicación inequívoca de goce en el significado conservado.
        pago = interpretar_descripcion(fuente.get("tipo_dia"), regla["dominio"])[
            "es_remunerado"
        ]
    if (
        pago is not None
        and regla["es_remunerado"] is not None
        and pago != regla["es_remunerado"]
    ):
        return "La regla general contradice la remuneración indicada en la fuente."
    return None


def componer(c, regla, equivalencia):
    """Conservar original y versión aplicada; no leer dinámicamente la regla al pagar."""
    from asistia.calidad_leyendas import pendiente

    if pendiente(c):
        return copy.deepcopy(c)
    original = fuente_original(c)
    nuevo = copy.deepcopy(original)
    for campo in ("es_remunerado", "es_falta", "grupo_actividad"):
        nuevo[campo] = regla[campo] if regla[campo] is not None else original.get(campo)
    compat = interpretar_descripcion(regla["nombre"], regla["dominio"])
    if regla["dominio"] == "asistencia":
        estado = compat["estado_asistencia_codigo"]
        if estado != "OTRO_REPORTADO":
            nuevo["estado_asistencia_codigo"] = estado
        if "LICENCIA" in normal(regla["nombre"]):
            nuevo["estado_asistencia_codigo"] = {
                True: "L",
                False: "LSG",
                None: "OTRO_REPORTADO",
            }[nuevo["es_remunerado"]]
        elif "PERMISO" in normal(regla["nombre"]):
            nuevo["estado_asistencia_codigo"] = {
                True: "PCG",
                False: "P",
                None: "OTRO_REPORTADO",
            }[nuevo["es_remunerado"]]
    nuevo.update(
        fuente="CATALOGO_GENERAL",
        clasificacion_fuente=original,
        categoria_general={
            "id": regla["categoria_id"],
            "nombre": regla["nombre"],
            "dominio": regla["dominio"],
            "version_id": str(regla["categoria_version_id"]),
            "version": regla["version"],
            "equivalencia": equivalencia,
            "motivo": regla["motivo"],
            "autor": regla["autor"],
            "fecha": regla["creado_en"].isoformat(),
        },
    )
    conflicto = incompatibilidad(c, regla)
    if conflicto:
        nuevo.update(
            fuente="CONFLICTO_CATALOGO",
            conflicto_catalogo=conflicto,
            es_remunerado=None,
        )
    return nuevo


def resolver_entrada(conn, dominio, categorias):
    """Solo nuevas interpretaciones de fuente; conservar copias aplicadas y excepciones."""
    reglas = {r["categoria_id"]: r for r in catalogo(conn, dominio)}
    enlaces = {
        e["significado"]: e["categoria_id"] for e in equivalencias(conn, dominio)
    }
    salida = copy.deepcopy(categorias)
    for codigo, c in salida.items():
        if c.get("categoria_general") or c.get("fuente") not in {
            "LEYENDA_DOCUMENTAL",
            "ANOTACION_COMPARTIDA",
        }:
            continue
        significado = normal(c.get("tipo_dia"))
        cid = enlaces.get(significado)
        if cid in reglas and not c.get("conflictos"):
            salida[codigo] = componer(c, reglas[cid], significado)
    return salida


def repetida(conn, op, accion, solicitud, uid):
    previo = conn.execute(
        "SELECT * FROM leyenda_operacion WHERE operacion_id=%s", (op,)
    ).fetchone()
    if previo:
        if (previo["accion"], previo["solicitud"], previo["autor"]) != (
            accion,
            solicitud,
            uid,
        ):
            raise ValueError(
                "Este envío corresponde a otra operación. Vuelve a abrir el formulario."
            )
        return previo["resultado"]
    return None


def registrar(conn, op, accion, solicitud, resultado, uid, motivo):
    conn.execute(
        "INSERT INTO leyenda_operacion(operacion_id,accion,solicitud,resultado,autor,motivo) VALUES(%s,%s,%s,%s,%s,%s)",
        (op, accion, Jsonb(solicitud), Jsonb(resultado), uid, motivo),
    )


def validar_motivo(motivo):
    if not isinstance(motivo, str) or not 5 <= len(motivo.strip()) <= 2000:
        raise ValueError("Describe el sustento o motivo (entre 5 y 2000 caracteres).")
    return motivo.strip()


def regla_formulario(form, dominio):
    if dominio not in DOMINIOS:
        raise ValueError("Selecciona asistencia o calendarios.")
    valores = {"": None, "SI": True, "NO": False}
    pago, falta = form.get("remuneracion", ""), form.get("es_falta", "")
    grupo = form.get("grupo_actividad", "")
    if (
        pago not in valores
        or falta not in valores
        or grupo not in {"", "LECTIVO", "GESTION", "NO_LECTIVO_NI_GESTION"}
    ):
        raise ValueError("Revisa las opciones de remuneración y clasificación.")
    return {
        "es_remunerado": valores[pago],
        "es_falta": valores[falta] if dominio == "asistencia" else None,
        "grupo_actividad": grupo or None if dominio == "calendario" else None,
    }


def guardar_categoria(conn, form, uid, op, cid=None):
    motivo = validar_motivo(form.get("motivo", ""))
    actual = categoria(conn, cid) if cid else None
    dominio = actual["dominio"] if actual else form.get("dominio")
    nombre = actual["nombre"] if actual else form.get("nombre", "").strip()
    if not 1 <= len(nombre) <= 500:
        raise ValueError("Indica un nombre de categoría (hasta 500 caracteres).")
    regla = regla_formulario(form, dominio)
    solicitud = {
        "id": cid,
        "nombre": nombre,
        "dominio": dominio,
        "regla": regla,
        "motivo": motivo,
        "version": form.get("version", ""),
    }
    previo = repetida(conn, op, "GUARDAR_CATEGORIA", solicitud, uid)
    if previo:
        return previo["categoria_id"]
    if cid:
        conn.execute(
            "SELECT 1 FROM leyenda_categoria WHERE categoria_id=%s FOR UPDATE", (cid,)
        )
        actual = categoria(conn, cid)
        if str(actual["version"]) != str(form.get("version")):
            raise ValueError(
                "La regla cambió mientras la revisabas. Recarga y compara la versión actual."
            )
        version = actual["version"] + 1
    else:
        if conn.execute(
            "SELECT 1 FROM leyenda_categoria WHERE dominio=%s AND clave=%s",
            (dominio, normal(nombre)),
        ).fetchone():
            raise ValueError(
                "Ya existe una categoría con ese nombre. Abre su regla desde el catálogo."
            )
        cid = conn.execute(
            "INSERT INTO leyenda_categoria(dominio,nombre,clave) VALUES(%s,%s,%s) RETURNING categoria_id",
            (dominio, nombre, normal(nombre)),
        ).fetchone()["categoria_id"]
        version = 1
    conn.execute(
        "INSERT INTO leyenda_categoria_version(categoria_id,version,es_remunerado,es_falta,grupo_actividad,motivo,autor) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (
            cid,
            version,
            regla["es_remunerado"],
            regla["es_falta"],
            regla["grupo_actividad"],
            motivo,
            uid,
        ),
    )
    registrar(
        conn,
        op,
        "GUARDAR_CATEGORIA",
        solicitud,
        {"categoria_id": cid, "version": version},
        uid,
        motivo,
    )
    return cid


def asociar(conn, dominio, descripcion, cid, anterior, motivo, uid, op):
    motivo = validar_motivo(motivo)
    if (
        dominio not in DOMINIOS
        or not isinstance(descripcion, str)
        or not 1 <= len(descripcion.strip()) <= 500
    ):
        raise ValueError(
            "Indica el significado completo de la leyenda (hasta 500 caracteres)."
        )
    significado = normal(descripcion)
    solicitud = {
        "dominio": dominio,
        "descripcion": descripcion,
        "categoria_id": cid,
        "anterior": anterior,
        "motivo": motivo,
    }
    previo = repetida(conn, op, "ASOCIAR", solicitud, uid)
    if previo:
        return
    cat = categoria(conn, cid)
    if cat["dominio"] != dominio:
        raise ValueError(
            "La categoría y la leyenda deben pertenecer al mismo catálogo."
        )
    old = conn.execute(
        "SELECT categoria_id FROM leyenda_equivalencia WHERE dominio=%s AND significado=%s FOR UPDATE",
        (dominio, significado),
    ).fetchone()
    if str(old["categoria_id"] if old else "") != str(anterior):
        raise ValueError("La equivalencia cambió. Recarga antes de asociarla de nuevo.")
    conn.execute(
        "INSERT INTO leyenda_equivalencia(dominio,significado,descripcion,categoria_id) VALUES(%s,%s,%s,%s) ON CONFLICT(dominio,significado) DO UPDATE SET categoria_id=excluded.categoria_id,descripcion=excluded.descripcion",
        (dominio, significado, descripcion.strip(), cid),
    )
    registrar(
        conn,
        op,
        "ASOCIAR",
        solicitud,
        {"categoria_id": cid, "significado": significado},
        uid,
        motivo,
    )


def retirar_equivalencia(conn, dominio, significado, cid, motivo, uid, op):
    motivo = validar_motivo(motivo)
    solicitud = {
        "dominio": dominio,
        "significado": significado,
        "categoria_id": cid,
        "motivo": motivo,
    }
    if repetida(conn, op, "RETIRAR_EQUIVALENCIA", solicitud, uid):
        return
    actual = conn.execute(
        "SELECT * FROM leyenda_equivalencia WHERE dominio=%s AND significado=%s FOR UPDATE",
        (dominio, significado),
    ).fetchone()
    if not actual or actual["categoria_id"] != cid:
        raise ValueError("La equivalencia cambió. Recarga antes de retirarla.")
    conn.execute(
        "DELETE FROM leyenda_equivalencia WHERE dominio=%s AND significado=%s",
        (dominio, significado),
    )
    registrar(
        conn,
        op,
        "RETIRAR_EQUIVALENCIA",
        solicitud,
        {"categoria_id": cid, "equivalencia_anterior": actual},
        uid,
        motivo,
    )
