"""Fuentes complementarias: pruebas con datos ficticios, sin llamadas externas."""

from datetime import date

import openpyxl
import pytest

from asistia.cierre.ingesta import padron_dre, validar_bloque
from asistia.importar.asistencia import _normalizar_dni
from asistia.importar.nexus import importar_nexus

from .fixtures_excel import construir_nexus_minimo


def test_identidad_asistencia_no_trunca():
    assert _normalizar_dni("123456789") is None
    assert _normalizar_dni("00000000") is None
    assert _normalizar_dni(1234567.0) == "01234567"


def test_ocr_rechaza_dias_omitidos():
    with pytest.raises(ValueError, match="incompletas"):
        validar_bloque(
            {
                "tipo": "asistencia",
                "anio": 2026,
                "mes": 7,
                "personas": [{"nombre": "PERSONA FICTICIA", "marcas": ["A"] * 30}],
            }
        )


def test_ocr_calendario_no_duplica_mes():
    with pytest.raises(ValueError, match="repetido"):
        validar_bloque(
            {
                "tipo": "calendario",
                "anio": 2026,
                "meses": [{"mes": 7, "codigos": ["A"] * 31}] * 2,
            }
        )


def test_ocr_permite_vacio_sin_presencia():
    validar_bloque(
        {
            "tipo": "asistencia",
            "anio": 2026,
            "mes": 7,
            "personas": [{"nombre": "PERSONA FICTICIA", "marcas": [None] * 31}],
        }
    )


def test_nexus_completa_y_conserva_nivel_ausente(conn, tmp_path):
    def fila(codigo, nivel, dni, plaza):
        return {
            "CODMOD I.E.": codigo,
            "NOMBRE DE LA INSTITUCION EDUCATIVA": codigo,
            "NIVEL EDUCATIVO": nivel,
            "CODIGO LOCAL": "999001",
            "DOCUMENTO DE IDENTIDAD": dni,
            "APELLIDO PATERNO": "PRUEBA",
            "NOMBRES": "FICTICIO",
            "CODIGO DE PLAZA": plaza,
            "TIPO DE TRABAJADOR": "DOCENTE",
            "SITUACION LABORAL": "NOMBRADO",
            "TIPO DE REGISTRO": "ORGANICA",
        }

    viejo = construir_nexus_minimo(
        [
            fila("9990001", "PRIMARIA", "99900001", "999000000001"),
            fila("9990002", "SECUNDARIA", "99900002", "999000000002"),
        ],
        tmp_path / "viejo.xlsx",
    )
    nuevo = construir_nexus_minimo(
        [fila("9990001", "PRIMARIA", "99900001", "999000000001")],
        tmp_path / "nuevo.xlsx",
    )
    importar_nexus(conn, viejo, date(2025, 1, 23))
    resultado = importar_nexus(conn, nuevo, date(2026, 6, 1))
    assert (
        conn.execute("SELECT count(*) AS n FROM institucion_educativa").fetchone()["n"]
        == 2
    )
    carga = conn.execute(
        "SELECT estado_integridad,filas_registradas FROM nexus_carga WHERE nexus_carga_id=%s",
        (resultado.nexus_carga_id,),
    ).fetchone()
    assert carga == {"estado_integridad": "COMPLETA", "filas_registradas": 1}


def test_dre_solo_padron_sin_asistencia_y_repeticion(conn, tmp_path):
    ruta = tmp_path / "PRIMARIA.xlsx"
    w = openpyxl.Workbook()
    s = w.active
    s.title = "MARZO"
    s.append(["MES DE MARZO - 2026"])
    s.append(["N°", "INSTITUCIÓN EDUCATIVA", "APELLIDOS Y NOMBRES", "CARGO"])
    s.append([1, "99999", "PRUEBA FICTICIA UNO", "DOCENTE"])
    w.save(ruta)
    item = conn.execute(
        "INSERT INTO cierre_documento(sha256,tipo,ruta) VALUES(%s,'DRE',%s) RETURNING *",
        ("a" * 64, str(ruta)),
    ).fetchone()
    for _ in range(2):
        datos = padron_dre(conn, item)
        assert datos["filas"] == 1 and datos["asistencia_generada"] == 0
    assert (
        conn.execute("SELECT count(*) AS n FROM padron_evidencia").fetchone()["n"] == 1
    )
    assert conn.execute("SELECT count(*) AS n FROM asistencia_dia").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM trabajador").fetchone()["n"] == 0
