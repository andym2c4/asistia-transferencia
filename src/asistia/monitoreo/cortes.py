"""Publicación inmutable e idempotente de cortes, modelos y expedientes diarios."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from asistia.categorias import validar_motivo
from asistia.experimental.datos import huella
from asistia.experimental.salidas import seguro

from .datos import cargar_fuentes, construir
from .modelo import VERSION, entrenar
from .reglas import REGLAS, VARIABLES


def serializable(objeto):
    return json.loads(
        json.dumps(objeto, default=str, ensure_ascii=False, allow_nan=False)
    )


def codigo():
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }


def crear(conn, anio, *, autor, motivo):
    motivo = validar_motivo(motivo)
    # La captura completa usa una instantánea MVCC independiente. No confirma el
    # trabajo del llamador y tampoco retiene bloqueos operativos mientras entrena.
    with psycopg.connect(
        conn.info.dsn, password=conn.info.password, row_factory=dict_row
    ) as lectura:
        lectura.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        fuentes = cargar_fuentes(lectura, anio)
    perfiles = construir(fuentes, reglas_aplicadas=True, exigir_vigente=True)
    if not perfiles:
        raise ValueError(
            "No hay reportes diarios activos para ese año. Carga o revisa las fuentes antes de crear el corte."
        )
    configuracion = {
        "version": VERSION,
        "codigo": codigo(),
        "fuentes_sha256": huella(fuentes),
    }
    hash_corte = huella(configuracion)
    existente = conn.execute(
        "SELECT corte_id FROM monitoreo_diario_corte WHERE huella=%s", (hash_corte,)
    ).fetchone()
    if existente:
        return existente["corte_id"], False
    cid = uuid5(NAMESPACE_URL, "asistia:monitoreo-diario:" + hash_corte)
    for p in perfiles:
        p["perfil_id"] = uuid5(cid, f"{p['institucion_educativa_id']}:{p['periodo']}")
    modelo, evaluacion, resultados = entrenar(perfiles)
    manifiesto = serializable(
        {
            "configuracion_corte": configuracion,
            "evaluacion": evaluacion,
            "catalogo": fuentes["catalogo"],
            "equivalencias": fuentes["equivalencias"],
            "naturaleza": "DERIVADO_DOCUMENTOS_DIARIOS",
            "anio": anio,
            "poblacion": "Instituciones y meses con al menos un reporte diario activo del año; no cobertura UGEL validada.",
            "grano": "persona+institucion+rol+fecha; agregado institucion+mes",
            "politica_catalogo": "Reglas aplicadas a las versiones de los documentos; correcciones diarias y excepciones conservadas. Calendario vigente requerido para evaluar cruces. La resolución operativa no borra señales ni se usa como etiqueta de entrenamiento.",
            "umbral_cobertura": {
                "minimo_cruces": 5,
                "fraccion_minima": 0.5,
                "estado": "CRITERIO_TECNICO_PROPUESTO",
            },
            "resumen": {
                "perfiles": len(perfiles),
                "instituciones": len({p["institucion_educativa_id"] for p in perfiles}),
                **{
                    k: sum(p["datos"][k] for p in perfiles)
                    for k in (
                        "apto_modelo",
                        "oportunidades",
                        "incluidas",
                        "excluidas",
                        "cruces_evaluables",
                        "dias_con_alerta",
                        "observaciones_unidas",
                    )
                },
            },
            "reglas": REGLAS,
            "variables": VARIABLES,
        }
    )
    with conn.transaction():
        creado = conn.execute(
            """INSERT INTO monitoreo_diario_corte(corte_id,huella,anio,manifiesto,modelo,resultados,autor,motivo)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(huella) DO NOTHING RETURNING corte_id""",
            (
                cid,
                hash_corte,
                anio,
                Jsonb(manifiesto),
                modelo,
                Jsonb(resultados),
                autor,
                motivo,
            ),
        ).fetchone()
        if not creado:
            return conn.execute(
                "SELECT corte_id FROM monitoreo_diario_corte WHERE huella=%s",
                (hash_corte,),
            ).fetchone()["corte_id"], False
        for p in perfiles:
            conn.execute(
                """INSERT INTO monitoreo_diario_perfil(perfil_id,corte_id,institucion_educativa_id,periodo,identidad,datos,fuentes)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (
                    p["perfil_id"],
                    cid,
                    p["institucion_educativa_id"],
                    p["periodo"],
                    Jsonb(serializable(p["identidad"])),
                    Jsonb(serializable(p["datos"])),
                    Jsonb(serializable(p["fuentes"])),
                ),
            )
        archivo = expediente(manifiesto, serializable(perfiles), resultados, modelo)
        conn.execute(
            "UPDATE monitoreo_diario_corte SET paquete=%s WHERE corte_id=%s",
            (archivo, cid),
        )
    return cid, True


def csv_perfiles(perfiles, resultados):
    scores = {r["reporte_id"]: r for r in resultados}
    filas = []
    for p in perfiles:
        d = p["datos"]
        fila = {
            "perfil_id": str(p["perfil_id"]),
            **p["identidad"],
            "periodo": str(p["periodo"]),
            **{
                k: d[k]
                for k in (
                    "apto_modelo",
                    "oportunidades",
                    "incluidas",
                    "excluidas",
                    "cruces_evaluables",
                    "dias_con_alerta",
                )
            },
        }
        for f, c in d["componentes"].items():
            fila.update(
                {
                    f + "_" + k: c[k]
                    for k in ("numerador", "denominador", "valor", "estado")
                }
            )
        score = scores.get(str(p["perfil_id"]), {})
        fila.update(
            {k: score.get(k) for k in ("score_if", "puesto", "en_cupo", "particion")}
        )
        filas.append(fila)
    buffer = io.StringIO(newline="")
    if filas:
        writer = csv.DictWriter(buffer, fieldnames=list(filas[0]))
        writer.writeheader()
        writer.writerows({k: seguro(v) for k, v in f.items()} for f in filas)
    return ("\ufeff" + buffer.getvalue()).encode()


def expediente(manifiesto, perfiles, resultados, modelo):
    contenido = {
        "manifiesto.json": json.dumps(
            manifiesto, ensure_ascii=False, indent=2
        ).encode(),
        "perfiles_y_fuentes.json": json.dumps(
            perfiles, ensure_ascii=False, indent=2
        ).encode(),
        "resultados.json": json.dumps(
            resultados, ensure_ascii=False, indent=2
        ).encode(),
        "perfiles.csv": csv_perfiles(perfiles, resultados),
        "LEEME.md": (
            "# ASISTIA — monitoreo de cruces diarios\n\n"
            "Datos derivados de documentos reales; no acreditan presencia física ni una decisión de pago.\n"
            "Categorías y reglas conservadas por versión. Alertas deterministas separadas del ranking IF.\n"
            "Cada razón conserva numerador y denominador; sin denominador no se informa cero.\n"
            "La evaluación contiene perturbaciones artificiales. M07 y utilidad RRHH NO_MEDIDO.\n"
            "Contiene localizadores privados de fuentes: usar la versión seudonimizada para exposición pública.\n"
            "Solo cargar el modelo interno con su hash y entorno verificados. Instalar con uv sync --frozen.\n"
            "Inferencia: asistia.monitoreo.modelo.inferir(bytes_modelo, perfiles_aptos).\n"
        ).encode(),
    }
    if modelo:
        contenido["modelo.joblib"] = modelo
    root = Path(__file__).resolve().parents[3]
    for p in (
        root / "pyproject.toml",
        root / "uv.lock",
        *Path(__file__).parent.glob("*.py"),
    ):
        contenido[("codigo/" if p.suffix == ".py" else "") + p.name] = p.read_bytes()
    contenido["SHA256SUMS.json"] = json.dumps(
        {k: hashlib.sha256(v).hexdigest() for k, v in contenido.items()}, indent=2
    ).encode()
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for nombre, valor in contenido.items():
            info = zipfile.ZipInfo(nombre, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, valor)
    return salida.getvalue()


def perfiles_corte(conn, cid, *, detalle=False):
    datos = "datos" if detalle else "datos - 'dias' AS datos"
    return conn.execute(
        f"SELECT perfil_id,corte_id,institucion_educativa_id,periodo,identidad,{datos} "
        "FROM monitoreo_diario_perfil WHERE corte_id=%s ORDER BY periodo,institucion_educativa_id",
        (cid,),
    ).fetchall()
