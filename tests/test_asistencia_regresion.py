"""P02: reproduccion de asistencia contra docs/casos/TACTAMAL_JULIO_2026.md, mas el caso de
identidad no resuelta que el plan exige. `importar_asistencia` no crea instituciones -- depende de
que NEXUS ya las haya creado (hallazgo de esta sesion, ver docs/runbooks/PUNTO_DE_EJECUCION.md):
por eso el ciclo se importa en su orden real, NEXUS antes de asistencia.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import openpyxl

from asistia.importar.asistencia import importar_asistencia
from asistia.importar.nexus import importar_nexus

from .fixtures_excel import construir_anexo3_minimo

REPO_ROOT = Path(__file__).resolve().parent.parent
RUTA_ASISTENCIA_TACTAMAL = (
    REPO_ROOT
    / "data/raw/data_brindada_por_ugel/ASISTENCIAS JULIO 2026/ASISTENCIA SECUNDARIA JULIO"
    / "ASISTENCIA JULIO IEPySM N  18091 TACTAMAL (ANEXOS 3y4) UGEL LUYA.xlsx"
)
RUTA_NEXUS_JUNIO = (
    REPO_ROOT / "data/raw/data_brindada_por_ugel/nexus/nexus 2026-06-01.xlsx"
)
RUTA_NEXUS_ABRIL = (
    REPO_ROOT / "data/raw/data_brindada_por_ugel/nexus/NEXUS LUYA AL 01-04-2026.xls"
)


def _conteos_por_codigo(conn, reporte_asistencia_id) -> dict[str | None, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ad.codigo_reportado_raw AS codigo, count(*) AS n
            FROM asistencia_dia ad
            JOIN trabajador_en_reporte ter ON ter.trabajador_en_reporte_id = ad.trabajador_en_reporte_id
            WHERE ter.reporte_asistencia_id = %s
            GROUP BY ad.codigo_reportado_raw
            """,
            (reporte_asistencia_id,),
        )
        return {fila["codigo"]: fila["n"] for fila in cur.fetchall()}


def test_importar_asistencia_tactamal_localizador(conn):
    """docs/casos/TACTAMAL_JULIO_2026.md §5 (primaria, tabla "Total": A=90, L=5, F=20, vacio=40) y
    §6 (secundaria, persona S10 con una I el 20 de julio)."""
    importar_nexus(conn, RUTA_NEXUS_JUNIO, date(2026, 6, 1))
    importar_nexus(conn, RUTA_NEXUS_ABRIL, date(2026, 4, 1))
    resultado = importar_asistencia(conn, RUTA_ASISTENCIA_TACTAMAL)

    assert resultado.error is None
    assert resultado.errores_fila == []
    assert resultado.total_trabajadores == 12
    assert resultado.resueltos == 12
    assert resultado.pendientes == 0
    assert set(resultado.reportes_asistencia_id) == {"PRIMARIA", "SECUNDARIA"}

    primaria = _conteos_por_codigo(conn, resultado.reportes_asistencia_id["PRIMARIA"])
    assert primaria.get("A") == 90
    assert primaria.get("L") == 5
    assert primaria.get("F") == 20
    assert primaria.get(None) == 40  # VACIO, sin codigo

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS n FROM asistencia_dia ad
            JOIN trabajador_en_reporte ter ON ter.trabajador_en_reporte_id = ad.trabajador_en_reporte_id
            WHERE ter.reporte_asistencia_id = %s AND ad.codigo_reportado_raw = 'I' AND ad.fecha = %s
            """,
            (resultado.reportes_asistencia_id["SECUNDARIA"], date(2026, 7, 20)),
        )
        assert cur.fetchone()["n"] == 1, (
            "S10 debe tener exactamente una I el 20 de julio (§6)"
        )


def test_importar_asistencia_repetido_no_duplica_hechos(conn):
    """Repetir la importacion del mismo archivo no debe duplicar trabajador_en_reporte ni
    asistencia_dia -- ambos tienen indices unicos por (reporte_asistencia_id, fila) y
    (trabajador_en_reporte_id, fecha) respectivamente, pero cada corrida crea un
    reporte_asistencia nuevo (version), asi que el conteo total se duplica una vez por version,
    no de forma descontrolada."""
    importar_nexus(conn, RUTA_NEXUS_JUNIO, date(2026, 6, 1))
    importar_nexus(conn, RUTA_NEXUS_ABRIL, date(2026, 4, 1))
    # Los dos cortes NEXUS completos (no solo Tactamal) ya crean cientos de `trabajador` propios de
    # toda la UGEL Luya -- la comparacion valida es contra ese total antes/despues de asistencia,
    # no contra un numero absoluto de 12.
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM trabajador")
        antes_de_asistencia = cur.fetchone()["n"]

    importar_asistencia(conn, RUTA_ASISTENCIA_TACTAMAL)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM trabajador")
        assert cur.fetchone()["n"] == antes_de_asistencia, (
            "las 12 personas de Tactamal ya existian por NEXUS (alertas_vinculo_automatico=1); "
            "la primera importacion de asistencia no debe crear trabajadores nuevos"
        )

    importar_asistencia(conn, RUTA_ASISTENCIA_TACTAMAL)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM reporte_asistencia")
        assert cur.fetchone()["n"] == 4  # 2 niveles x 2 versiones
        cur.execute("SELECT count(*) AS n FROM trabajador")
        assert cur.fetchone()["n"] == antes_de_asistencia, (
            "repetir la importacion de asistencia no debe duplicar trabajadores"
        )


def test_importar_asistencia_identidad_no_resuelta(conn, tmp_path):
    """Construido (fixtures_excel): una fila sin DNI reconocible no debe perderse en silencio --
    queda en trabajador_en_reporte con estado_match='SIN_MATCH' y sus marcas diarias se preservan,
    en vez de excluirse del reporte."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO local_educativo (codlocal_escale, distrito) VALUES ('900002', 'LUYA') "
            "RETURNING local_educativo_id"
        )
        local_id = cur.fetchone()["local_educativo_id"]
        cur.execute(
            "INSERT INTO institucion_educativa "
            "(local_educativo_id, cod_mod, nombre_ie, nivel_modalidad, distrito) "
            "VALUES (%s, '9999002', '9999002', 'PRIMARIA', 'LUYA')",
            (local_id,),
        )
    conn.commit()

    archivo = construir_anexo3_minimo(
        tmp_path / "anexo3_identidad.xlsx",
        institucion_raw="9999002",
        nivel_raw="PRIMARIA",
        personas=[
            {
                "dni": None,
                "nombres": "SIN DNI, PRUEBA",
                "cargo": "DOCENTE",
                "marcas": {},
            },
            {
                "dni": "12345678",
                "nombres": "CON DNI, PRUEBA",
                "cargo": "DOCENTE",
                "marcas": {1: "A"},
            },
        ],
    )

    resultado = importar_asistencia(conn, archivo)

    assert resultado.error is None
    assert resultado.total_trabajadores == 2
    assert resultado.resueltos == 1
    assert resultado.pendientes == 1

    with conn.cursor() as cur:
        cur.execute(
            "SELECT estado_match, trabajador_id, nombres_reportados_raw FROM trabajador_en_reporte "
            "WHERE nombres_reportados_raw = 'SIN DNI, PRUEBA'"
        )
        fila = cur.fetchone()
        assert fila is not None, "la persona sin DNI no debe desaparecer del reporte"
        assert fila["estado_match"] == "SIN_MATCH"
        assert fila["trabajador_id"] is None


def _preparar_institucion_9999003(conn, cod_mod="9999003"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO local_educativo (codlocal_escale, distrito) VALUES (%s, 'LUYA') "
            "RETURNING local_educativo_id",
            (cod_mod,),
        )
        local_id = cur.fetchone()["local_educativo_id"]
        cur.execute(
            "INSERT INTO institucion_educativa "
            "(local_educativo_id, cod_mod, nombre_ie, nivel_modalidad, distrito) "
            "VALUES (%s, %s, %s, 'PRIMARIA', 'LUYA')",
            (local_id, cod_mod, cod_mod),
        )
    conn.commit()


def _anexo3_fila_gap(
    ruta, institucion_raw, fila_gap_valores, personas, *, con_fila_gap
):
    """ANEXO 3 minimo con control exacto de la fila entre los numeros de dia (fila_cols+1) y la
    primera persona. `con_fila_gap=True` escribe `fila_gap_valores` en esa fila (fila_cols+2) y las
    personas empiezan una fila despues (caso real mayoritario). `con_fila_gap=False` no escribe
    nada aparte -- la primera persona empieza directamente en fila_cols+2, sin fila intermedia
    (caso real sin separador, feedback RRHH 2026-09-12)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ANEXO 3"
    ws["A1"], ws["C1"] = "I.E:", institucion_raw
    ws["A2"], ws["C2"] = "PERIODO(MES/AÑO):", date(2026, 7, 1)
    ws["A3"], ws["C3"] = "NIVEL/MODALIDAD EDUCATIVA:", "PRIMARIA"
    ws["A4"], ws["C4"] = "TURNO:", "MAÑANA"
    fila_cols = 9
    for col, valor in enumerate(["N°", "DNI", "APELLIDOS Y NOMBRES", "CARGO"], start=1):
        ws.cell(fila_cols, col, valor)
    fila_dias = fila_cols + 1
    for i in range(5):
        ws.cell(fila_dias, 5 + i, i + 1)
    fila_gap = fila_cols + 2
    if con_fila_gap:
        for i, valor in enumerate(fila_gap_valores):
            ws.cell(fila_gap, 5 + i, valor)
        fila_datos = fila_gap + 1
    else:
        fila_datos = fila_gap
    for idx, persona in enumerate(personas):
        fila = fila_datos + idx
        ws.cell(fila, 1, idx + 1)
        ws.cell(fila, 2, persona["dni"])
        ws.cell(fila, 3, persona["nombres"])
        ws.cell(fila, 4, persona["cargo"])
        for dia, codigo in persona.get("marcas", {}).items():
            ws.cell(fila, 5 + (dia - 1), codigo)
    ws.cell(fila_datos + len(personas) + 1, 1, "LEYENDA: A=asistencia")
    wb.save(ruta)
    return ruta


def test_importar_asistencia_reconoce_fila_de_iniciales_de_dia_semana(conn, tmp_path):
    """Caso real mayoritario (242 de 252 ANEXO 3 revisados en data/raw, 2026-09-12): entre la fila
    de numeros de dia y la primera persona hay una fila con iniciales del dia de la semana
    (L,M,X,J,V,S,D). Debe seguir saltandose sin perder a la persona siguiente."""
    _preparar_institucion_9999003(conn, "9999003")
    archivo = _anexo3_fila_gap(
        tmp_path / "anexo3_con_iniciales.xlsx",
        "9999003",
        ["L", "M", "M", "J", "V"],
        [
            {
                "dni": "10000001",
                "nombres": "CON FILA DE INICIALES",
                "cargo": "DOCENTE",
                "marcas": {1: "A", 2: "A", 3: "F", 4: "A", 5: "A"},
            }
        ],
        con_fila_gap=True,
    )
    resultado = importar_asistencia(conn, archivo)
    assert resultado.error is None
    assert resultado.total_trabajadores == 1
    assert resultado.resueltos == 1
    conteos = _conteos_por_codigo(
        conn, next(iter(resultado.reportes_asistencia_id.values()))
    )
    assert conteos.get("A") == 4 and conteos.get("F") == 1


def test_importar_asistencia_sin_fila_de_iniciales_no_pierde_la_primera_persona(
    conn, tmp_path
):
    """Hallazgo del corpus real (ASISTENCIA MES JULIO I.E. 18237.2026.xlsx, hoja "PARTE MENSUAL DE
    ASISTENCIA"): algunos formatos no traen fila de iniciales de dia -- la primera persona viene
    inmediatamente despues de los numeros de dia. Antes de esta correccion, un salto fijo de fila
    perdia siempre a esa primera persona; ahora se detecta por contenido (ver
    `_fila_es_iniciales_dia_semana`) y no se salta cuando esa fila no son iniciales de dia."""
    _preparar_institucion_9999003(conn, "9999004")
    archivo = _anexo3_fila_gap(
        tmp_path / "anexo3_sin_iniciales.xlsx",
        "9999004",
        [],  # nada se escribe aqui: la fila_gap se rellena como parte de "personas", no de esto
        [
            {
                "dni": "10000002",
                "nombres": "PRIMERA PERSONA SIN SEPARADOR",
                "cargo": "DOCENTE",
                "marcas": {1: "A", 2: "A", 3: "A", 4: "A", 5: "A"},
            },
            {
                "dni": "10000003",
                "nombres": "SEGUNDA PERSONA",
                "cargo": "DOCENTE",
                "marcas": {1: "A", 2: "F", 3: "A", 4: "A", 5: "A"},
            },
        ],
        con_fila_gap=False,
    )
    resultado = importar_asistencia(conn, archivo)
    assert resultado.error is None
    assert resultado.total_trabajadores == 2, (
        "no debe perderse la primera persona cuando no hay fila de iniciales de dia"
    )
    assert resultado.resueltos == 2
    with conn.cursor() as cur:
        cur.execute(
            "SELECT nombres_reportados_raw FROM trabajador_en_reporte "
            "WHERE nombres_reportados_raw LIKE 'PRIMERA PERSONA%'"
        )
        assert cur.fetchone() is not None
