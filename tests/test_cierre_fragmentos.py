"""Recuperación de tablas sin inventar cabeceras ni duplicar personas."""

import hashlib
import json

from pypdf import PdfWriter

from asistia.cierre import extraccion
from asistia.cierre.gemini import comprimir_filas
from asistia.cierre.ingesta import aplicar_extraccion, asignar_contexto

from .test_cierre_integracion import base_ficticia


def test_compresion_reversible_conserva_huecos_y_coordenadas():
    filas = [
        [1, [[2, "A"]]],
        [2, [[2, "A"]]],
        [3, [[2, "A"]]],
        [5, [[2, "A"]]],
        [6, [[2, "B"]]],
    ]
    compacto = comprimir_filas(filas)
    reconstruido = []
    for entrada in compacto:
        if isinstance(entrada, dict):
            reconstruido.extend(
                [
                    [n, entrada["valores"]]
                    for n in range(entrada["desde"], entrada["hasta"] + 1)
                ]
            )
        else:
            reconstruido.append(entrada)
    assert reconstruido == filas
    assert len(compacto) == 3


def test_cache_valido_alternativo_evitar_repetir_ocr_invalido(tmp_path):
    class Motor:
        modelos = ("invalido", "valido")

        def destino_cache(self, ruta, contexto, modelo):
            return tmp_path / (modelo + ".json")

        def extraer(self, *args):
            raise AssertionError(
                "No debe gastar otra llamada si existe una lectura válida."
            )

    (tmp_path / "invalido.json").write_text(
        json.dumps(
            {"datos": {"bloques": [{"tipo": "calendario", "anio": 2026, "meses": []}]}}
        )
    )
    valido = {"datos": {"bloques": [{"tipo": "solo_resumen"}]}, "modelo": "valido"}
    (tmp_path / "valido.json").write_text(json.dumps(valido))
    assert (
        extraccion.extraer_documento(
            Motor(),
            {"tipo": "CALENDARIO_2026", "ruta": str(tmp_path / "original.pdf")},
            "Prueba",
        )
        == valido
    )


def test_fragmentos_pagina_original_cabecera_unica_y_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(extraccion, "SALIDA", tmp_path)
    ruta = tmp_path / "ficticio.pdf"
    pdf = PdfWriter()
    for _ in range(2):
        pdf.add_blank_page(width=100, height=100)
    pdf.write(ruta)

    class Motor:
        llamadas = 0

        def extraer(self, fragmento, contexto):
            self.llamadas += 1
            bloque = {
                "tipo": "asistencia",
                "pagina": 1,
                "anio": 2026,
                "mes": 7,
                "institucion": "FICTICIA" if self.llamadas == 1 else None,
                "personas": [
                    {
                        "nombre": f"Persona ficticia {self.llamadas}",
                        "marcas": [None] * 31,
                    }
                ],
            }
            return {
                "sha256": hashlib.sha256(fragmento.read_bytes()).hexdigest(),
                "modelo": "FICTICIO",
                "version": "prueba",
                "datos": {"bloques": [bloque]},
            }

    motor = Motor()
    item = {"ruta": str(ruta), "tipo": "ASISTENCIA"}
    r = extraccion.extraer_paginas(motor, item, "Prueba")
    assert [b["pagina"] for b in r["datos"]["bloques"]] == [1, 2]
    assert r["datos"]["bloques"][1]["institucion"] == "FICTICIA"
    assert r["datos"]["bloques"][1]["supuestos"]
    assert r["sha256"] == hashlib.sha256(ruta.read_bytes()).hexdigest()
    assert extraccion.extraer_paginas(motor, item, "Prueba") == r
    assert motor.llamadas == 2
    assert extraccion.extraer_documento(motor, item, "Prueba") == r
    assert motor.llamadas == 2


def test_nivel_de_especialidad_y_pagina_repetida_no_duplican(conn, tmp_path):
    iid, item = base_ficticia(conn, tmp_path)
    b = {
        "tipo": "asistencia",
        "institucion": "9999001 IE WEB FICTICIA",
        "nivel": "PRIMARIA",
        "anio": 2026,
        "mes": 7,
        "pagina": 1,
        "personas": [
            {
                "fila": 1,
                "dni": "99990001",
                "nombre": "Ejemplo Ficticio Persona Uno",
                "marcas": ["A"] * 31,
            },
            {
                "fila": 2,
                "dni": "99990002",
                "nombre": "Ejemplo Ficticio Persona Dos",
                "nivel": "SECUNDARIA",
                "marcas": ["A"] * 31,
            },
        ],
    }
    extraido = {
        "datos": {"bloques": [b, {**b, "pagina": 2}]},
        "modelo": "FICTICIO",
        "version": "prueba",
        "sha256": item["sha256"],
    }
    r = aplicar_extraccion(conn, item, extraido)
    assert aplicar_extraccion(conn, item, extraido) == r
    assert (
        conn.execute("SELECT count(*) n FROM reporte_asistencia").fetchone()["n"] == 1
    )
    assert not r["pendientes"]
    assert (
        conn.execute("SELECT count(*) n FROM trabajador_en_reporte").fetchone()["n"]
        == 2
    )
    reporte = conn.execute(
        "SELECT institucion_educativa_id,procedencia_extraccion FROM reporte_asistencia"
    ).fetchone()
    assert reporte["institucion_educativa_id"] == iid
    assert len(reporte["procedencia_extraccion"]["filas_repetidas"]) == 2
    assert any(
        "especialidad profesional" in s
        for s in reporte["procedencia_extraccion"]["supuestos"]
    )


def test_contexto_institucion_guarda_motivo_y_historial(conn, tmp_path):
    from psycopg.types.json import Jsonb

    iid, item = base_ficticia(conn, tmp_path)
    conn.execute(
        "UPDATE cierre_documento SET resultado=%s WHERE cierre_documento_id=%s",
        (
            Jsonb(
                {
                    "pendientes": [
                        {
                            "bloque": 1,
                            "nivel": "PRIMARIA",
                            "motivo": "institucion_ambigua",
                        }
                    ]
                }
            ),
            item["cierre_documento_id"],
        ),
    )
    asignar_contexto(
        conn,
        item["cierre_documento_id"],
        1,
        iid,
        "PRIMARIA",
        "Documento ficticio cotejado con padrón.",
        "AGENTE_TECNICO",
    )
    r = conn.execute("SELECT contexto_tecnico,estado FROM cierre_documento").fetchone()
    assert r["estado"] == "PENDIENTE"
    contexto = r["contexto_tecnico"]
    assert contexto["historial"][0] == contexto["instituciones"]["1:PRIMARIA"]
    assert "AGENTE_TECNICO" in json.dumps(contexto)


def test_2025_solo_se_excluye_si_servicio_identificado_ya_tiene_2026(
    conn, tmp_path, monkeypatch
):
    from asistia.cierre import ingesta

    monkeypatch.setattr(ingesta, "SALIDA", tmp_path)
    iid, item = base_ficticia(conn, tmp_path)
    conn.execute(
        "UPDATE institucion_educativa SET nombre_ie='18101' WHERE institucion_educativa_id=%s",
        (iid,),
    )
    cl = conn.execute(
        "INSERT INTO calendarizacion_local(institucion_educativa_id,anio) VALUES(%s,2026) RETURNING calendarizacion_local_id",
        (iid,),
    ).fetchone()["calendarizacion_local_id"]
    conn.execute(
        "INSERT INTO calendarizacion_version(calendarizacion_local_id,version,origen,motivo_version) VALUES(%s,1,'IMPORTACION_IE','Ficticio')",
        (cl,),
    )
    conn.execute(
        "UPDATE cierre_documento SET tipo='CALENDARIO_2025',ruta=%s WHERE cierre_documento_id=%s",
        (
            str(tmp_path / "PRIMARIA" / "18101 - FICTICIA.pdf"),
            item["cierre_documento_id"],
        ),
    )
    conn.execute(
        "INSERT INTO cierre_documento(sha256,tipo,ruta) VALUES(%s,'CALENDARIO_2025',%s)",
        ("a" * 64, str(tmp_path / "PRIMARIA" / "desconocida.pdf")),
    )
    decisiones = ingesta.preseleccionar_calendarios_2025(conn)
    assert len(decisiones) == 1
    assert decisiones[0]["institucion_id"] == iid
    estados = conn.execute(
        "SELECT estado FROM cierre_documento ORDER BY ruta"
    ).fetchall()
    assert {r["estado"] for r in estados} == {"NO_NECESARIO", "PENDIENTE"}
    assert (
        conn.execute(
            "SELECT count(*) n FROM calendarizacion_local WHERE anio=2025"
        ).fetchone()["n"]
        == 0
    )


def test_leyenda_por_fuente_conserva_raw_y_version_anterior(conn, tmp_path):
    from asistia.cierre.ajustes import ajustar_calendario

    iid, _item = base_ficticia(conn, tmp_path)
    cl = conn.execute(
        "INSERT INTO calendarizacion_local(institucion_educativa_id,anio) VALUES(%s,2026) RETURNING calendarizacion_local_id",
        (iid,),
    ).fetchone()["calendarizacion_local_id"]
    cv = conn.execute(
        "INSERT INTO calendarizacion_version(calendarizacion_local_id,version,origen,motivo_version) VALUES(%s,1,'IMPORTACION_IE','Ficticio') RETURNING calendarizacion_version_id",
        (cl,),
    ).fetchone()["calendarizacion_version_id"]
    conn.execute(
        "INSERT INTO dia_calendarizacion(calendarizacion_version_id,fecha,codigo_reportado_raw,estado_captura) VALUES(%s,'2026-07-01','A','CODIGO_DESCONOCIDO')",
        (cv,),
    )
    nuevo = ajustar_calendario(
        conn, cv, {"A": "LECTIVO"}, "Leyenda ficticia hoja 1 celda A40."
    )
    assert (
        ajustar_calendario(
            conn, cv, {"A": "LECTIVO"}, "Leyenda ficticia hoja 1 celda A40."
        )
        == nuevo
    )
    dias = conn.execute(
        "SELECT calendarizacion_version_id,codigo_reportado_raw,estado_captura FROM dia_calendarizacion"
    ).fetchall()
    assert len(dias) == 2 and all(d["codigo_reportado_raw"] == "A" for d in dias)
    assert {d["estado_captura"] for d in dias} == {"CODIGO_DESCONOCIDO", "REGISTRADO"}
    assert (
        conn.execute(
            "SELECT estado FROM calendarizacion_version WHERE calendarizacion_version_id=%s",
            (nuevo,),
        ).fetchone()["estado"]
        == "BORRADOR"
    )


def test_periodo_corregido_por_bloque_conserva_encabezado_extraido():
    from asistia.cierre.ingesta import bloques_normalizados

    item = {
        "tipo": "ASISTENCIA",
        "ruta": "ficticio.pdf",
        "contexto_tecnico": {
            "periodos": {
                "2": {
                    "anio": 2026,
                    "mes": 7,
                    "motivo": "Oficio y grilla ficticios coinciden con 2026.",
                    "autor": "AGENTE_TECNICO",
                }
            }
        },
    }
    original = {
        "datos": {
            "bloques": [
                {"tipo": "otro"},
                {"tipo": "asistencia", "anio": 2024, "mes": 7, "personas": []},
            ]
        }
    }
    bloques = bloques_normalizados(item, original)
    assert bloques[1]["anio"] == 2026 and bloques[1]["periodo_extraido"]["anio"] == 2024
    assert original["datos"]["bloques"][1]["anio"] == 2024
    assert "2024" in bloques[1]["supuestos"][0]


def test_reporte_rechazado_se_conserva_y_no_entra_en_salida(conn, tmp_path):
    from datetime import date

    from asistia.consolidado.generar import generar_consolidado
    from asistia.web.lecturas import reportes_mes

    _, item = base_ficticia(conn, tmp_path)
    bloque = {
        "tipo": "asistencia",
        "institucion": "9999001 IE WEB FICTICIA",
        "nivel": "PRIMARIA",
        "anio": 2026,
        "mes": 7,
        "personas": [
            {
                "dni": "99990001",
                "nombre": "Persona Ficticia",
                "cargo": "DOCENTE",
                "marcas": ["A"] * 31,
            }
        ],
    }
    r = aplicar_extraccion(
        conn,
        item,
        {
            "datos": {"bloques": [bloque]},
            "modelo": "FICTICIO",
            "version": "prueba",
            "sha256": item["sha256"],
        },
    )
    conn.execute(
        "UPDATE reporte_asistencia SET estado='RECHAZADO' WHERE reporte_asistencia_id=%s",
        (r["ids"][0],),
    )
    assert reportes_mes(conn, date(2026, 7, 1), "PRIMARIA") == []
    assert generar_consolidado(conn, date(2026, 7, 1), "PRIMARIA").error
    assert conn.execute("SELECT count(*) n FROM asistencia_dia").fetchone()["n"] == 31


def test_rectificacion_de_periodo_no_colisiona_con_bloque_original(conn, tmp_path):
    _, item = base_ficticia(conn, tmp_path)
    b = {
        "tipo": "asistencia",
        "institucion": "9999001 IE WEB FICTICIA",
        "nivel": "PRIMARIA",
        "anio": 2024,
        "mes": 7,
        "pagina": 2,
        "personas": [
            {
                "dni": "99990001",
                "nombre": "Persona Ficticia",
                "cargo": "DOCENTE",
                "marcas": ["A"] * 31,
            }
        ],
    }
    extraido = {
        "datos": {"bloques": [b]},
        "modelo": "FICTICIO",
        "version": "prueba",
        "sha256": item["sha256"],
    }
    viejo = aplicar_extraccion(conn, item, extraido)["ids"][0]
    item["contexto_tecnico"] = {
        "periodos": {
            "1": {
                "anio": 2026,
                "mes": 7,
                "motivo": "Oficio ficticio del período correcto.",
            }
        }
    }
    nuevo = aplicar_extraccion(conn, item, extraido)["ids"][0]
    assert nuevo != viejo
    assert aplicar_extraccion(conn, item, extraido)["ids"][0] == nuevo
    fechas = conn.execute(
        "SELECT periodo,version FROM reporte_asistencia ORDER BY periodo"
    ).fetchall()
    assert [r["periodo"].year for r in fechas] == [2024, 2026]
    assert fechas[1]["version"] > fechas[0]["version"]


def test_cargo_abreviado_no_omite_docente_nuevo_y_ambiguedad_se_conserva(
    conn, tmp_path
):
    from asistia.importar.asistencia import rol_por_texto

    _, item = base_ficticia(conn, tmp_path)
    b = {
        "tipo": "asistencia",
        "institucion": "9999001 IE WEB FICTICIA",
        "nivel": "PRIMARIA",
        "anio": 2026,
        "mes": 7,
        "personas": [
            {
                "dni": "99889977",
                "nombre": "Persona Nueva Ficticia",
                "cargo": "Prof./Horas",
                "marcas": ["A"] * 31,
            }
        ],
    }
    aplicar_extraccion(
        conn,
        item,
        {
            "datos": {"bloques": [b]},
            "modelo": "FICTICIO",
            "version": "prueba",
            "sha256": item["sha256"],
        },
    )
    t = conn.execute(
        "SELECT t.estado_match,c.codigo,v.tipo_registro FROM trabajador_en_reporte t JOIN catalogo_rol_laboral c USING(rol_laboral_id) LEFT JOIN vinculo_trabajador_ie v USING(vinculo_trabajador_ie_id)"
    ).fetchone()
    assert t == {
        "estado_match": "RESUELTO",
        "codigo": "DOCENTE",
        "tipo_registro": None,
    }
    assert rol_por_texto("P/A", "Contratada") is None
    assert rol_por_texto("Psicólogo", "CAS") == "CAS"
    assert rol_por_texto("Doctor") is None


def test_pdf_impresion_excesiva_no_consume_llamadas(tmp_path):
    import pytest

    ruta = tmp_path / "impresion_accidental.pdf"
    pdf = PdfWriter()
    for _ in range(201):
        pdf.add_blank_page(width=100, height=100)
    pdf.write(ruta)

    class Motor:
        def extraer(self, *args):
            raise AssertionError("No solicitar OCR de cientos de páginas sin revisar.")

    with pytest.raises(ValueError, match="supera 200 páginas"):
        extraccion.extraer_documento(Motor(), {"ruta": str(ruta)}, "Prueba")


def test_calendario_dias_excel_decimales_exactos(conn, tmp_path):
    from zipfile import ZipFile

    import openpyxl

    from asistia.importar.calendario import importar_calendario

    base_ficticia(conn, tmp_path)
    archivo = tmp_path / "calendario_ficticio.xlsx"
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "CAL-2026"
    hoja["A1"] = "CALENDARIZACIÓN DEL AÑO ESCOLAR 2026"
    hoja["A4"] = "NOMBRE DE LA IE:"
    hoja["B4"] = "9999001 IE WEB FICTICIA"
    hoja["A6"] = "NIVEL O CICLO:"
    hoja["B6"] = "PRIMARIA"
    hoja["A11"] = "JULIO"
    hoja["D11"], hoja["D12"] = 1, "L"
    hoja["E11"], hoja["E12"] = 2, "G"
    hoja["F11"], hoja["F12"] = 2.5, "L"
    libro.save(archivo)
    with ZipFile(archivo) as z:
        partes = {n: z.read(n) for n in z.namelist()}
    partes["xl/worksheets/sheet1.xml"] = (
        partes["xl/worksheets/sheet1.xml"]
        .replace(b"<v>1</v>", b"<v>1.0</v>")
        .replace(b"<v>2</v>", b"<v>2.0</v>")
    )
    with ZipFile(archivo, "w") as z:
        for nombre, contenido in partes.items():
            z.writestr(nombre, contenido)
    assert isinstance(openpyxl.load_workbook(archivo).active["D11"].value, float)
    resultado = importar_calendario(conn, archivo)
    assert resultado.error is None
    dias = conn.execute(
        "SELECT fecha,codigo_reportado_raw,celda_origen FROM dia_calendarizacion WHERE calendarizacion_version_id=%s ORDER BY fecha",
        (resultado.calendarizacion_version_id,),
    ).fetchall()
    assert [
        (str(d["fecha"]), d["codigo_reportado_raw"], d["celda_origen"]) for d in dias
    ] == [("2026-07-01", "L", "D12"), ("2026-07-02", "G", "E12")]


def test_word_vectorial_conserva_original_y_localizador_pdf(tmp_path, monkeypatch):
    from zipfile import ZipFile

    from asistia.cierre import conversion

    word = tmp_path / "ficticio.docx"
    with ZipFile(word, "w") as z:
        z.writestr("word/media/tabla.emf", b"EMF ficticio")
    pdf = tmp_path / "representacion.pdf"
    escritor = PdfWriter()
    escritor.add_blank_page(width=100, height=100)
    escritor.write(pdf)
    original = hashlib.sha256(word.read_bytes()).hexdigest()
    meta = {
        "sha256_original": original,
        "nota": "Páginas de representación local; original conservado.",
    }
    monkeypatch.setattr(conversion, "representar_docx", lambda ruta: (pdf, meta))

    class Motor:
        def extraer(self, ruta, contexto):
            assert ruta == pdf
            assert meta["nota"] in contexto
            return {
                "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                "datos": {"bloques": [{"tipo": "solo_resumen", "pagina": 1}]},
            }

    resultado = extraccion.extraer_documento(
        Motor(), {"ruta": str(word), "tipo": "CALENDARIO_2026"}, "Prueba"
    )
    assert resultado["sha256"] == original
    assert resultado["conversion"] == meta
    assert resultado["datos"]["bloques"][0]["pagina"] == 1
    assert meta["nota"] in resultado["datos"]["bloques"][0]["supuestos"]


def test_parcial_no_rellena_dias_y_preserva_bloque_original(conn, tmp_path):
    from asistia.cierre.ingesta import estado_extraccion
    from asistia.cierre.parciales import recuperar_parcial

    iid, item = base_ficticia(conn, tmp_path)
    item["contexto_tecnico"] = {
        "instituciones": {
            "2:PRIMARIA": {"institucion_id": iid, "motivo": "Cotejo ficticio de prueba"}
        }
    }
    b = {
        "tipo": "asistencia",
        "anio": 2026,
        "mes": 7,
        "nivel": "PRIMARIA",
        "institucion": "Nombre sin coincidencia",
        "pagina": 2,
        "personas": [
            {
                "dni": "99990001",
                "nombre": "Ejemplo Ficticio Persona Uno",
                "cargo": "DOCENTE",
                "marcas": ["A"] * 31,
            },
            {
                "dni": "99990002",
                "nombre": "Ejemplo Ficticio Persona Dos",
                "cargo": "DOCENTE",
                "marcas": ["A"] * 30,
            },
        ],
    }
    r = {
        "datos": {"bloques": [{"tipo": "calendario", "anio": 2026, "meses": []}, b]},
        "sha256": item["sha256"],
        "modelo": "FICTICIO",
        "version": "prueba",
    }
    parcial = recuperar_parcial(item, r)
    assert len(parcial["datos"]["bloques"]) == 1
    assert parcial["datos"]["bloques"][0]["indice_bloque_original"] == 2
    assert len(parcial["datos_no_aplicados"][1]["datos"]["marcas"]) == 30
    aplicada = aplicar_extraccion(conn, item, parcial)
    assert estado_extraccion(aplicada) == "PARCIAL"
    assert len(aplicada["ids"]) == 1
    assert conn.execute("SELECT count(*) n FROM asistencia_dia").fetchone()["n"] == 31
    assert len(r["datos"]["bloques"][1]["personas"]) == 2


def test_mes_incompleto_no_impide_otro_mes_pero_queda_pendiente():
    from asistia.cierre.parciales import recuperar_parcial

    r = {
        "datos": {
            "bloques": [
                {
                    "tipo": "calendario",
                    "anio": 2026,
                    "meses": [
                        {"mes": 6, "codigos": ["L"] * 31},
                        {"mes": 7, "codigos": ["L"] * 31},
                    ],
                }
            ]
        }
    }
    p = recuperar_parcial({"tipo": "CALENDARIO_2026", "ruta": "ficticio.pdf"}, r)
    assert [m["mes"] for m in p["datos"]["bloques"][0]["meses"]] == [7]
    assert p["pendientes_extraccion"][0]["mes"] == 6
    assert len(p["datos_no_aplicados"][0]["datos"]["codigos"]) == 31
