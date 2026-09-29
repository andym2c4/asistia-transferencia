"""Complemento puntual CEN_POB: clave textual exacta, solo vacíos, sin sobrescrituras."""

import csv
import hashlib
import io
import re
import uuid
from collections import Counter

from asistia.web.instituciones_edicion import leer, registrar


def analizar(contenido):
    reader = csv.DictReader(io.StringIO(contenido.decode("utf-8-sig"), newline=""))
    if not {"COD_MOD", "ANEXO", "CEN_POB"}.issubset(reader.fieldnames or []):
        raise ValueError("El padrón requiere COD_MOD, ANEXO y CEN_POB.")
    filas = {}
    for numero, row in enumerate(reader, 2):
        clave = ((row["COD_MOD"] or "").strip(), (row["ANEXO"] or "").strip())
        if not re.fullmatch(r"[0-9]{7}", clave[0]) or not re.fullmatch(
            r"[0-9]{1,4}", clave[1]
        ):
            raise ValueError(
                f"Identidad inválida en la fila {numero}; no se completará el padrón."
            )
        if clave in filas:
            raise ValueError(
                f"Identidad duplicada en la fila {numero}; revisa el padrón antes de aplicarlo."
            )
        valor = (row["CEN_POB"] or "").strip()
        if len(valor) > 500:
            raise ValueError(f"Centro poblado demasiado largo en la fila {numero}.")
        filas[clave] = {"valor": valor, "recibido": row["CEN_POB"], "fila": numero}
    return filas, hashlib.sha256(contenido).hexdigest()


def cotejar(conn, filas):
    instituciones = conn.execute(
        "SELECT * FROM institucion_educativa ORDER BY institucion_educativa_id"
    ).fetchall()
    conteos = Counter()
    propuestos = []
    claves = set()
    for ie in instituciones:
        clave = (ie["cod_mod"], ie["anexo"])
        claves.add(clave)
        fuente = filas.get(clave)
        if fuente is None:
            conteos["sin_correspondencia"] += 1
        elif not fuente["valor"]:
            conteos["fuente_vacia"] += 1
        elif not (ie["centro_poblado"] or "").strip():
            conteos["por_completar"] += 1
            propuestos.append((ie, fuente))
        elif ie["centro_poblado"] == fuente["valor"]:
            conteos["ya_coinciden"] += 1
        else:
            conteos["conflictos_conservados"] += 1
    return {
        "instituciones": len(instituciones),
        "filas_fuente": len(filas),
        "fuente_sin_institucion": len(filas.keys() - claves),
        **{
            k: conteos[k]
            for k in (
                "sin_correspondencia",
                "fuente_vacia",
                "por_completar",
                "ya_coinciden",
                "conflictos_conservados",
            )
        },
    }, propuestos


def completar(conn, filas, sha, fuente):
    # Compatible con el lock del recorrido web y la captura de respaldo.
    conn.execute("SELECT pg_advisory_xact_lock(26091206)")
    conn.execute("LOCK TABLE institucion_educativa IN SHARE ROW EXCLUSIVE MODE")
    resumen, propuestos = cotejar(conn, filas)
    for anterior, fila in propuestos:
        iid = anterior["institucion_educativa_id"]
        op = uuid.uuid5(uuid.NAMESPACE_URL, f"asistia:centro-poblado:{sha}:{iid}")
        if conn.execute(
            "SELECT 1 FROM institucion_cambio WHERE operacion_id=%s", (op,)
        ).fetchone():
            # Una edición posterior vació el campo: reejecutar no revierte esa decisión.
            continue
        conn.execute(
            "UPDATE institucion_educativa SET centro_poblado=%s WHERE institucion_educativa_id=%s",
            (fila["valor"], iid),
        )
        evidencia = {
            **fuente,
            "sha256": sha,
            "fila": fila["fila"],
            "columna": "CEN_POB",
            "valor_recibido": fila["recibido"],
            "cod_mod": anterior["cod_mod"],
            "anexo": anterior["anexo"],
        }
        registrar(
            conn,
            op,
            iid,
            "COMPLETAR_PADRON",
            {"sha256": sha, "campo": "CEN_POB"},
            anterior,
            leer(conn, iid),
            None,
            "Actualización de padrón solicitada por el usuario · RF-F05",
            "Completar centro poblado desde CEN_POB por coincidencia exacta de COD_MOD + ANEXO; solo campos vacíos.",
            evidencia,
        )
    return resumen
