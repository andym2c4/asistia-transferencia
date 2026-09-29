"""Resolución de institución por nombre/nivel del encabezado, compartida entre los importadores de
calendario y asistencia (asistia.identificacion). Caso real 2026-09-12 (usuario probando la app
contra `db`): NEXUS guarda el nombre sin tilde ("RAMON CASTILLA"), el documento de calendario trae
el nombre con tilde ("RAMÓN CASTILLA") -- antes de esta prueba, ese archivo real quedaba en ERROR
"no se pudo identificar la institución" pese a ser evidentemente la misma institución.
"""

from __future__ import annotations

from datetime import date

from asistia.identificacion import resolver_institucion
from asistia.importar.nexus import importar_nexus

from .fixtures_excel import construir_nexus_minimo


def _nexus_ramon_castilla(tmp_path):
    return construir_nexus_minimo(
        [
            {
                "CODMOD I.E.": "0262279",
                "CODIGO DE PLAZA": "PZ-01",
                "TIPO DE TRABAJADOR": "DOCENTE",
                "SITUACION LABORAL": "NOMBRADO",
                "TIPO DE REGISTRO": "ORGANICA",
                "DOCUMENTO DE IDENTIDAD": "10000001",
                "APELLIDO PATERNO": "Ejemplo",
                "APELLIDO MATERNO": "Ficticio",
                "NOMBRES": "Persona Uno",
                "FECHA DE INICIO": date(2026, 1, 1),
                "CODIGO LOCAL": "262279",
                "DISTRITO": "LUYA",
                "NIVEL EDUCATIVO": "Secundaria",
                "NOMBRE DE LA INSTITUCION EDUCATIVA": "RAMON CASTILLA",
                "CARGO": "DOCENTE",
            }
        ],
        tmp_path / "nexus.xlsx",
    )


def test_resolver_institucion_ignora_tildes_del_documento(conn, tmp_path):
    resultado = importar_nexus(conn, _nexus_ramon_castilla(tmp_path), date(2026, 6, 1))
    assert resultado.errores == []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT institucion_educativa_id FROM institucion_educativa WHERE nombre_ie='RAMON CASTILLA'"
        )
        esperado = cur.fetchone()["institucion_educativa_id"]
        # El documento real trae el nombre con tilde y el nivel calificado ("Secundaria o
        # Avanzado"); NEXUS lo guarda sin tilde y solo como "Secundaria".
        resuelto = resolver_institucion(cur, "RAMÓN CASTILLA", "Secundaria o Avanzado")
    assert resuelto == esperado


def test_resolver_institucion_exacto_sigue_funcionando(conn, tmp_path):
    importar_nexus(conn, _nexus_ramon_castilla(tmp_path), date(2026, 6, 1))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT institucion_educativa_id FROM institucion_educativa WHERE nombre_ie='RAMON CASTILLA'"
        )
        esperado = cur.fetchone()["institucion_educativa_id"]
        resuelto = resolver_institucion(cur, "RAMON CASTILLA", "Secundaria")
    assert resuelto == esperado


def test_resolver_institucion_sin_coincidencia_devuelve_none(conn, tmp_path):
    importar_nexus(conn, _nexus_ramon_castilla(tmp_path), date(2026, 6, 1))
    with conn.cursor() as cur:
        resuelto = resolver_institucion(
            cur, "OTRA INSTITUCION QUE NO EXISTE", "Secundaria"
        )
    assert resuelto is None
