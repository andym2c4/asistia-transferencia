"""P03: valida las claves del cohorte de evaluación `linea_base_h1` (no depende de la base de
datos). Comprueba integridad de hashes, unicidad de `unidad_id`, y que ningún valor de
`referencia.csv` se presente como revisado por RRHH sin revisor -- exactamente la separación entre
análisis del desarrollador y revisión de negocio que P03 exige mantener visible.
"""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COHORTE_DIR = REPO_ROOT / "data" / "evaluacion" / "linea_base_h1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _leer_csv(nombre: str) -> list[dict[str, str]]:
    with open(COHORTE_DIR / nombre, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_manifiesto_claves_y_hashes():
    filas = _leer_csv("manifiesto.csv")
    assert filas, "el manifiesto no puede estar vacio"

    ids = [f["unidad_id"] for f in filas]
    assert len(ids) == len(set(ids)), "unidad_id debe ser unico en el manifiesto"

    for fila in filas:
        for campo in (
            "cohorte",
            "unidad_id",
            "tipo_unidad",
            "sha256",
            "localizador",
            "grupo",
            "ruta_relativa",
        ):
            assert fila[campo], f"{fila['unidad_id']}: {campo} no puede estar vacio"
        assert _SHA256_RE.match(fila["sha256"]), (
            f"{fila['unidad_id']}: sha256 con formato invalido"
        )
        assert fila["grupo"] in {"desarrollo", "evaluacion_reservada"}

        ruta = REPO_ROOT / fila["ruta_relativa"]
        assert ruta.is_file(), f"{fila['unidad_id']}: {ruta} no existe"
        hash_real = hashlib.sha256(ruta.read_bytes()).hexdigest()
        assert hash_real == fila["sha256"], (
            f"{fila['unidad_id']}: el archivo cambio desde que se congelo el manifiesto "
            f"(esperado {fila['sha256']}, real {hash_real})"
        )


def test_muestra_reservada_no_abierta():
    """Las filas evaluacion_reservada deben seguir sin institucion/periodo/nivel asignado -- si
    alguien las completo, dejaron de estar reservadas y este cambio debe ser deliberado, no un
    efecto secundario de tocarlas durante desarrollo."""
    filas = _leer_csv("manifiesto.csv")
    reservadas = [f for f in filas if f["grupo"] == "evaluacion_reservada"]
    assert len(reservadas) == 5
    for fila in reservadas:
        assert fila["ie_alias"] == "NO_ABIERTO"
        assert fila["periodo"] == "NO_ABIERTO"
        assert fila["nivel"] == "NO_ABIERTO"


def test_referencia_no_inventa_revision_de_rrhh():
    manifiesto_ids = {f["unidad_id"] for f in _leer_csv("manifiesto.csv")}
    filas = _leer_csv("referencia.csv")
    assert filas

    for fila in filas:
        unidad_id = fila["unidad_id"]
        es_regla_o_salida = unidad_id.startswith(("REGLA-", "SALIDA-"))
        assert es_regla_o_salida or unidad_id in manifiesto_ids, (
            f"referencia.csv referencia una unidad_id desconocida: {unidad_id}"
        )

        confirmada_por_rrhh = fila["decision"].startswith("REGLA_CONFIRMADA_RRHH")
        if confirmada_por_rrhh:
            assert fila["revisor_alias"], (
                f"{unidad_id}: marcado confirmado sin revisor_alias"
            )
            assert fila["revisado_en"], (
                f"{unidad_id}: marcado confirmado sin fecha de revision"
            )
        else:
            assert fila["decision"].startswith("ANALISIS_DESARROLLADOR"), (
                f"{unidad_id}: decision fuera del vocabulario esperado: {fila['decision']!r}"
            )
            assert not fila["revisor_alias"], (
                f"{unidad_id}: es analisis del desarrollador pero tiene revisor_alias -- "
                "no debe leerse como revision de negocio"
            )


def test_capturas_de_tiempo_con_esquema_pero_sin_medicion():
    """tiempos.csv/intervenciones.csv/evaluacion_alertas.csv existen con las columnas del
    protocolo; pueden tener cero filas (ninguna sesion de observacion con RRHH ha ocurrido aun) o
    mas cuando esa captura empiece -- este test solo protege el esquema, no el conteo."""
    esperado = {
        "tiempos.csv": [
            "cohorte",
            "tarea_id",
            "modo",
            "operador_alias",
            "etapa",
            "tipo_intervalo",
            "inicio_con_zona",
            "fin_con_zona",
            "motivo_pausa",
            "orden",
            "ayuda_tecnica",
            "resultado_tarea",
        ],
        "intervenciones.csv": [
            "evento_id",
            "tarea_id",
            "unidad_id",
            "tipo_intervencion",
            "campo",
            "origen_automatico_manual",
            "operador_alias",
            "instante",
            "motivo",
        ],
        "evaluacion_alertas.csv": [
            "clave_senal",
            "regla_metodo",
            "version",
            "unidad_id",
            "etiqueta",
            "evidencia",
            "evaluador_alias",
            "fecha",
        ],
    }
    for nombre, columnas in esperado.items():
        with open(COHORTE_DIR / nombre, encoding="utf-8", newline="") as f:
            lector = csv.reader(f)
            encabezado = next(lector)
        assert encabezado == columnas, (
            f"{nombre}: encabezado no coincide con el protocolo"
        )


def test_metricas_todas_no_medido_en_este_cohorte():
    filas = _leer_csv("metricas.csv")
    ids = {f["metrica_id"] for f in filas}
    assert ids == {"M01", "M02", "M03", "M04", "M05", "M06", "M07", "M07-R", "M08"}
    for fila in filas:
        assert fila["estado"] == "NO_MEDIDO", (
            f"{fila['metrica_id']}: se esperaba NO_MEDIDO"
        )
        assert fila["evidencia"], f"{fila['metrica_id']}: falta motivo en evidencia"
