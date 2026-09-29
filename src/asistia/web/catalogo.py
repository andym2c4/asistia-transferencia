"""Administración general: definir, asociar, revisar alcance y aplicar con historial."""

from __future__ import annotations

import hashlib
import json
import uuid

from flask import current_app, flash, g, redirect, render_template, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from psycopg.types.json import Jsonb

from asistia import categorias as cat
from asistia.cierre.ajustes import ajustar_calendario
from asistia.leyendas import (
    aplicar_leyenda_asistencia,
    aplicar_leyenda_calendario,
    normal,
)
from asistia.niveles import NIVELES

from . import ErrorDeTrabajo
from .db import base, escritura
from .lecturas import huella_reporte
from .leyendas import huella_calendario
from .revision import registrar, verificar_edicion


def documentos(conn, dominio, anio=None, periodo="", nivel=""):
    if dominio == "asistencia":
        rows = conn.execute("""SELECT r.reporte_asistencia_id AS id,r.clasificacion_codigos,r.estado,
          r.periodo::text AS periodo,r.institucion_educativa_id,ie.nombre_ie,ie.cod_mod,ie.anexo,
          fn_nivel_canonico(s.nivel_modalidad) AS nivel
          FROM reporte_asistencia r JOIN reporte_asistencia_serie s USING(reporte_asistencia_serie_id)
          JOIN institucion_educativa ie ON ie.institucion_educativa_id=r.institucion_educativa_id
          WHERE NOT EXISTS(SELECT 1 FROM reporte_asistencia p WHERE p.reporte_asistencia_serie_id=r.reporte_asistencia_serie_id AND p.version>r.version)
          AND r.estado NOT IN ('RECHAZADO','HISTORICA') ORDER BY ie.nombre_ie,r.periodo,r.reporte_asistencia_id""").fetchall()
    else:
        rows = conn.execute("""SELECT v.calendarizacion_version_id AS id,v.clasificacion_codigos,v.estado,
          c.anio::text AS periodo,c.institucion_educativa_id,ie.nombre_ie,ie.cod_mod,ie.anexo,
          fn_nivel_canonico(ie.nivel_modalidad) AS nivel
          FROM calendarizacion_version v JOIN calendarizacion_local c USING(calendarizacion_local_id)
          JOIN institucion_educativa ie USING(institucion_educativa_id)
          WHERE NOT EXISTS(SELECT 1 FROM calendarizacion_version p WHERE p.calendarizacion_local_id=v.calendarizacion_local_id AND p.version>v.version)
          ORDER BY ie.nombre_ie,c.anio,v.calendarizacion_version_id""").fetchall()
    return [
        dict(r, id=str(r["id"]))
        for r in rows
        if (not anio or r["periodo"][:4] == str(anio))
        and (not periodo or r["periodo"][:7] == periodo)
        and (not nivel or r["nivel"] == nivel)
    ]


def inventario(conn, dominio, consulta="", estado=""):
    grupos = {}
    docs = documentos(conn, dominio)
    enlaces = {e["significado"]: e for e in cat.equivalencias(conn, dominio)}
    generales = {c["categoria_id"]: c for c in cat.catalogo(conn, dominio)}
    sin_leyenda = []
    for d in docs:
        if not d["clasificacion_codigos"]:
            sin_leyenda.append(d)
        for codigo, c in d["clasificacion_codigos"].items():
            nombre = c.get("tipo_dia", "")
            clave = normal(nombre)
            if not clave:
                continue
            grupo = grupos.setdefault(
                clave,
                {
                    "significado": clave,
                    "descripcion": nombre,
                    "codigos": set(),
                    "documentos": {},
                    "conflicto": False,
                    "locales": 0,
                },
            )
            grupo["codigos"].add(codigo)
            grupo["documentos"][d["id"]] = d
            grupo["conflicto"] |= bool(c.get("conflictos") or c.get("revision_leyenda"))
            grupo["locales"] += c.get("fuente") == "REVISION_WEB"
    for clave, e in enlaces.items():
        grupos.setdefault(
            clave,
            {
                "significado": clave,
                "descripcion": e["descripcion"],
                "codigos": set(),
                "documentos": {},
                "conflicto": False,
                "locales": 0,
            },
        )
    salida = []
    for clave, grupo in sorted(grupos.items()):
        e = enlaces.get(clave)
        grupo["categoria"] = generales.get(e["categoria_id"]) if e else None
        grupo["codigos"] = sorted(grupo["codigos"])
        grupo["documentos"] = list(grupo["documentos"].values())
        if consulta and normal(consulta) not in normal(
            grupo["descripcion"]
            + " "
            + " ".join(grupo["codigos"])
            + " "
            + (grupo["categoria"]["nombre"] if grupo["categoria"] else "")
        ):
            continue
        if estado == "sin_asociar" and grupo["categoria"]:
            continue
        salida.append(grupo)
    return salida, sin_leyenda, len(docs)


def hash_equivalencias(conn, regla):
    filas = [
        e
        for e in cat.equivalencias(conn, regla["dominio"])
        if e["categoria_id"] == regla["categoria_id"]
    ]
    return hashlib.sha256(json.dumps(filas, sort_keys=True).encode()).hexdigest(), {
        e["significado"] for e in filas
    }


def preparar_alcance(conn, cid, filtros, uid):
    regla = cat.categoria(conn, cid)
    eq_hash, significados = hash_equivalencias(conn, regla)
    docs = documentos(
        conn,
        regla["dominio"],
        filtros["anio"],
        filtros["periodo"] if regla["dominio"] == "asistencia" else "",
        filtros["nivel"],
    )
    elegibles, excluidos = [], []
    for d in docs:
        cambios = []
        for codigo, c in d["clasificacion_codigos"].items():
            significado = normal(c.get("tipo_dia"))
            if significado not in significados:
                continue
            razon = cat.incompatibilidad(c, regla)
            if c.get("fuente") == "REVISION_WEB" and not filtros["excepciones"]:
                razon = "Revisión local conservada; puedes incluirla expresamente al preparar otro alcance."
            if c.get("categoria_general", {}).get("version_id") == str(
                regla["categoria_version_id"]
            ):
                razon = "Ya utiliza esta versión de la regla."
            if razon:
                excluidos.append({"documento": d, "codigo": codigo, "razon": razon})
            else:
                cambios.append(
                    {
                        "codigo": codigo,
                        "anterior": c,
                        "posterior": cat.componer(c, regla, significado),
                    }
                )
        if cambios:
            h = (
                huella_reporte(conn, d["id"])
                if regla["dominio"] == "asistencia"
                else huella_calendario(conn, d["id"])
            )
            elegibles.append({"documento": d, "cambios": cambios, "huella": h})
    if len(elegibles) > 500:
        raise ValueError(
            "Hay más de 500 documentos. Filtra por mes o nivel para revisar un alcance manejable."
        )
    payload = {
        "categoria_id": cid,
        "version_id": str(regla["categoria_version_id"]),
        "equivalencias": eq_hash,
        "usuario": uid,
        "operacion": str(uuid.uuid4()),
        "documentos": {
            e["documento"]["id"]: {
                "huella": e["huella"],
                "codigos": [c["codigo"] for c in e["cambios"]],
            }
            for e in elegibles
        },
        "filtros": filtros,
    }
    return regla, elegibles, excluidos, payload


def aplicar(conn, payload, seleccion, motivo, uid):
    """Una transacción para toda la selección; reintentar devuelve el mismo resultado."""
    with conn.transaction():
        return _aplicar(conn, payload, seleccion, motivo, uid)


def _aplicar(conn, payload, seleccion, motivo, uid):
    motivo = cat.validar_motivo(motivo)
    if payload["usuario"] != uid:
        raise ValueError("Esta vista previa corresponde a otra sesión de operador.")
    seleccion = sorted(set(seleccion))
    if not seleccion or set(seleccion) - payload["documentos"].keys():
        raise ValueError(
            "Selecciona al menos un documento incluido en esta vista previa."
        )
    solicitud = {"alcance": payload, "seleccion": seleccion, "motivo": motivo}
    op = uuid.UUID(payload["operacion"])
    anterior = cat.repetida(conn, op, "APLICAR", solicitud, uid)
    if anterior:
        return anterior
    cid = payload["categoria_id"]
    conn.execute(
        "SELECT 1 FROM leyenda_categoria WHERE categoria_id=%s FOR UPDATE", (cid,)
    )
    regla = cat.categoria(conn, cid)
    eh, significados = hash_equivalencias(conn, regla)
    if (
        str(regla["categoria_version_id"]) != payload["version_id"]
        or eh != payload["equivalencias"]
    ):
        raise ValueError(
            "La regla o sus equivalencias cambiaron. Prepara una nueva vista previa."
        )
    hechos, cambios = {}, []
    # Verificar todos antes de modificar el primero.
    for did in seleccion:
        esperado = payload["documentos"][did]
        if regla["dominio"] == "asistencia":
            d = verificar_edicion(conn, did, esperado["huella"])
        else:
            d = conn.execute(
                "SELECT * FROM calendarizacion_version WHERE calendarizacion_version_id=%s FOR UPDATE",
                (did,),
            ).fetchone()
            if not d:
                raise ValueError("El calendario ya no está disponible.")
            conn.execute(
                "SELECT 1 FROM calendarizacion_local WHERE calendarizacion_local_id=%s FOR UPDATE",
                (d["calendarizacion_local_id"],),
            )
            posterior = conn.execute(
                "SELECT 1 FROM calendarizacion_version WHERE calendarizacion_local_id=%s AND version>%s",
                (d["calendarizacion_local_id"], d["version"]),
            ).fetchone()
            if posterior or huella_calendario(conn, did) != esperado["huella"]:
                raise ValueError(
                    "Un calendario cambió. No se aplicó el lote; prepara otra vista previa."
                )
        nuevas = dict(d["clasificacion_codigos"])
        for codigo in esperado["codigos"]:
            actual = nuevas[codigo]
            significado = normal(actual.get("tipo_dia"))
            if significado not in significados or cat.incompatibilidad(actual, regla):
                raise ValueError(
                    "La fuente o la equivalencia cambió. Vuelve a revisar el alcance."
                )
            if (
                actual.get("fuente") == "REVISION_WEB"
                and not payload["filtros"]["excepciones"]
            ):
                raise ValueError(
                    "El alcance no autoriza reemplazar revisiones locales."
                )
            nuevas[codigo] = cat.componer(actual, regla, significado)
        hechos[did] = (d, nuevas)
    for did, (d, nuevas) in hechos.items():
        if regla["dominio"] == "asistencia":
            aplicar_leyenda_asistencia(conn, did, nuevas, usar_catalogo=False)
            conn.execute(
                "UPDATE reporte_asistencia SET estado='EN_VALIDACION',validado_por=NULL,validado_en=NULL WHERE reporte_asistencia_id=%s",
                (did,),
            )
            registrar(
                conn,
                uuid.uuid5(op, did),
                did,
                "CLASIFICAR_LEYENDA",
                motivo,
                {"clasificacion_codigos": d["clasificacion_codigos"]},
                {
                    "clasificacion_codigos": nuevas,
                    "categoria_general": cid,
                    "operacion_general": str(op),
                },
                uid,
            )
            nuevo = did
        else:
            nuevo = ajustar_calendario(
                conn,
                did,
                {},
                f"Categoría general {regla['nombre']}: {motivo} · operación {op}",
            )
            aplicar_leyenda_calendario(conn, nuevo, nuevas, usar_catalogo=False)
            conn.execute(
                "UPDATE calendarizacion_version SET procedencia_extraccion=procedencia_extraccion || %s WHERE calendarizacion_version_id=%s",
                (
                    Jsonb(
                        {
                            "categoria_general_aplicada": {
                                "categoria_id": cid,
                                "version": regla["version"],
                                "operacion": str(op),
                                "autor": uid,
                                "motivo": motivo,
                                "padre": did,
                            }
                        }
                    ),
                    nuevo,
                ),
            )
        cambios.append(
            {
                "origen": did,
                "resultado": str(nuevo),
                "codigos": payload["documentos"][did]["codigos"],
            }
        )
    resultado = {
        "categoria_id": cid,
        "dominio": regla["dominio"],
        "documentos": len(cambios),
        "cambios": cambios,
    }
    cat.registrar(conn, op, "APLICAR", solicitud, resultado, uid, motivo)
    return resultado


def firmador():
    return URLSafeTimedSerializer(
        current_app.secret_key, salt="asistia-categorias-alcance-v1"
    )


def filtros_request():
    import re

    anio = request.args.get("anio", "2026")
    periodo = request.args.get("mes", "")
    nivel = request.args.get("nivel", "")
    if (
        not anio.isdigit()
        or not 1900 <= int(anio) <= 2100
        or (periodo and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", periodo))
        or (nivel and nivel not in NIVELES)
    ):
        raise ValueError("Revisa el año, mes y nivel del alcance.")
    if periodo and periodo[:4] != anio:
        raise ValueError("El mes debe pertenecer al año seleccionado.")
    return {
        "anio": int(anio),
        "periodo": periodo,
        "nivel": nivel,
        "excepciones": request.args.get("excepciones") == "SI",
    }


def registrar_rutas(pages):
    from .routes import operacion

    @pages.get("/categorias")
    def categorias():
        dominio = request.args.get("dominio", "asistencia")
        if dominio not in cat.DOMINIOS:
            raise ErrorDeTrabajo("Selecciona asistencia o calendarios.")
        q, estado = request.args.get("q", "")[:500], request.args.get("estado", "")
        grupos, sin_leyenda, total = inventario(base(), dominio, q, estado)
        return render_template(
            "categorias.html",
            dominio=dominio,
            dominios=cat.DOMINIOS,
            categorias=cat.catalogo(base(), dominio),
            grupos=grupos,
            sin_leyenda=sin_leyenda,
            total=total,
            q=q,
            estado=estado,
        )

    @pages.get("/categorias/<int:cid>")
    def categoria_general(cid):
        try:
            regla = cat.categoria(base(), cid)
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc), 404) from None
        historial = (
            base()
            .execute(
                "SELECT v.*,u.nombre AS nombre_autor FROM leyenda_categoria_version v LEFT JOIN usuario u ON u.usuario_id=v.autor WHERE categoria_id=%s ORDER BY version DESC",
                (cid,),
            )
            .fetchall()
        )
        eq = [
            e
            for e in cat.equivalencias(base(), regla["dominio"])
            if e["categoria_id"] == cid
        ]
        grupos, _, _ = inventario(base(), regla["dominio"])
        usos = [
            g
            for g in grupos
            if g["categoria"] and g["categoria"]["categoria_id"] == cid
        ]
        aplicados = []
        for d in documentos(base(), regla["dominio"]):
            valores = [
                {"codigo": codigo, "version": c["categoria_general"]["version"]}
                for codigo, c in d["clasificacion_codigos"].items()
                if c.get("categoria_general", {}).get("id") == cid
            ]
            if valores:
                aplicados.append({"documento": d, "codigos": valores})
        eventos = (
            base()
            .execute(
                "SELECT o.*,u.nombre AS nombre_autor FROM leyenda_operacion o JOIN usuario u ON u.usuario_id=o.autor WHERE resultado->>'categoria_id'=%s ORDER BY creado_en DESC LIMIT 50",
                (str(cid),),
            )
            .fetchall()
        )
        return render_template(
            "categoria_general.html",
            regla=regla,
            historial=historial,
            equivalencias=eq,
            usos=usos,
            aplicados=aplicados,
            eventos=eventos,
        )

    @pages.post("/categorias/guardar")
    @pages.post("/categorias/<int:cid>/guardar")
    def categoria_guardar(cid=None):
        try:
            with escritura() as conn:
                cid = cat.guardar_categoria(
                    conn, request.form, g.usuario["usuario_id"], operacion(), cid
                )
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc)) from None
        flash(
            "Regla guardada. Los documentos existentes conservan su clasificación hasta revisar y aplicar el alcance."
        )
        return redirect(url_for("pages.categoria_general", cid=cid), code=303)

    @pages.post("/categorias/asociar")
    def categoria_asociar():
        try:
            cid = int(request.form.get("categoria_id", ""))
            with escritura() as conn:
                cat.asociar(
                    conn,
                    request.form.get("dominio"),
                    request.form.get("descripcion", ""),
                    cid,
                    request.form.get("anterior", ""),
                    request.form.get("motivo", ""),
                    g.usuario["usuario_id"],
                    operacion(),
                )
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc)) from None
        flash(
            "Equivalencia guardada para nuevas importaciones. Revisa el alcance para aplicarla a documentos existentes."
        )
        return redirect(url_for("pages.categoria_general", cid=cid), code=303)

    @pages.post("/categorias/<int:cid>/retirar-equivalencia")
    def categoria_retirar(cid):
        try:
            with escritura() as conn:
                regla = cat.categoria(conn, cid)
                cat.retirar_equivalencia(
                    conn,
                    regla["dominio"],
                    request.form.get("significado", ""),
                    cid,
                    request.form.get("motivo", ""),
                    g.usuario["usuario_id"],
                    operacion(),
                )
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc), 409) from None
        flash(
            "Equivalencia retirada para nuevas importaciones. Los documentos ya clasificados conservan su versión aplicada."
        )
        return redirect(url_for("pages.categoria_general", cid=cid), code=303)

    @pages.get("/categorias/<int:cid>/alcance")
    def categoria_alcance(cid):
        try:
            filtros = filtros_request()
            conn = base()
            conn.rollback()
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            regla, elegibles, excluidos, payload = preparar_alcance(
                conn, cid, filtros, g.usuario["usuario_id"]
            )
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc)) from None
        return render_template(
            "categoria_alcance.html",
            regla=regla,
            elegibles=elegibles,
            excluidos=excluidos,
            filtros=filtros,
            token=firmador().dumps(payload),
        )

    @pages.post("/categorias/aplicar")
    def categoria_aplicar():
        try:
            payload = firmador().loads(request.form.get("alcance", ""), max_age=3600)
            with escritura() as conn:
                resultado = aplicar(
                    conn,
                    payload,
                    request.form.getlist("seleccion"),
                    request.form.get("motivo", ""),
                    g.usuario["usuario_id"],
                )
        except (SignatureExpired, BadSignature):
            raise ErrorDeTrabajo(
                "La vista previa venció o no es válida. Abre la categoría y prepara otro alcance.",
                409,
            ) from None
        except ValueError as exc:
            raise ErrorDeTrabajo(str(exc), 409) from None
        flash(
            f"Clasificación aplicada a {resultado['documentos']} documentos. Conservan revisión pendiente; las salidas anteriores no cambiaron."
        )
        return redirect(
            url_for("pages.categoria_aplicacion", op=payload["operacion"]), code=303
        )

    @pages.get("/categorias/aplicaciones/<uuid:op>")
    def categoria_aplicacion(op):
        evento = (
            base()
            .execute(
                "SELECT * FROM leyenda_operacion WHERE operacion_id=%s AND accion='APLICAR'",
                (op,),
            )
            .fetchone()
        )
        if not evento:
            raise ErrorDeTrabajo("No se encuentra esa aplicación del catálogo.", 404)
        return render_template(
            "categoria_aplicacion.html",
            evento=evento,
            regla=cat.categoria(base(), evento["resultado"]["categoria_id"]),
        )
