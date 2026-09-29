"""Generador del consolidado de asistencia por nivel y periodo (pendiente 4 confirmado, caso
Tactamal: docs/casos/TACTAMAL_JULIO_2026.md §11) -- mismo formato que los archivos reales en
data/raw/data_brindada_por_ugel/asistencia_marzo_2026_a_junio_2026 (revisado columna por columna,
incluido un archivo "FINAL" con datos reales de marzo 2026, no solo la plantilla vacia).

Las nuevas salidas clasifican faltas mediante calendario y asistencia remunerativa
(CRUCE_REMUNERACION_1). Una licencia remunerada en día remunerado no cuenta como falta.
Las reglas sin definir producen pendientes, no ceros. La antigua agrupación J/L/P/C/U e I/3T/H
se conserva como clasificación reportada para trazabilidad de antecedentes.

dias_lectivos_esperados/dias_gestion_esperados se calculan (P04, asistia.consolidado.universo_esperado)
integrando calendario aplicable + fn_vinculo_presencia_esperada; quedan NULL solo cuando la
institucion no tiene calendario importado ese anio (calendario desconocido) o el detalle no tiene
vinculo resuelto. corresponde_descuento sigue sin calcularse: es una decision humana (el esquema
exige decidido_por/en/motivo junto a ese campo), no un resultado de este calculo.

Fuera de este primer corte, declarado no silencioso:
- HORAS NO JUSTIFICADAS / HORAS JUSTIFICADAS: en los 4 archivos reales revisados esas columnas
  nunca traen un valor (siempre "-"), y no hay una fuente confiable de horas por dia en
  `asistencia_dia` -- solo texto crudo de resumen en ANEXO 4 sin normalizar. Se exportan como "-".
- LUGAR / CENTRO POBLADO: se exportan solo si `institucion_educativa` ya los tiene poblados
  (el corte de NEXUS usado no los trae para Tactamal/Cristobal Benque) -- en blanco, no inventado.

El consolidado generado siempre queda en estado BORRADOR (el esquema no permite otra cosa sin
`enviado_por`/`enviado_en`/`archivo_exportado_id`): el envio a la DRE es un paso manual de RRHH,
fuera de este sistema (pendiente 4, confirmado). El recuento de `validacion_reporte` pendientes
para el nivel/periodo se expone en el resultado y en el propio archivo exportado, para que quede
visible antes de cualquier decision -- mismo principio de transparencia que motivo el "camino
automatico" de reasignacion de institucion (docs/decisions/2026-09-10-...vinculo...md §13).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import openpyxl
import psycopg
from openpyxl.styles import Alignment, Font
from psycopg.types.json import Jsonb

from asistia.ajustes_asistencia import marca_efectiva
from asistia.consolidado.universo_esperado import aplicar_dias_esperados_a_detalle
from asistia.db import sha256_de

MESES_NOMBRE = {
    1: "ENERO",
    2: "FEBRERO",
    3: "MARZO",
    4: "ABRIL",
    5: "MAYO",
    6: "JUNIO",
    7: "JULIO",
    8: "AGOSTO",
    9: "SETIEMBRE",
    10: "OCTUBRE",
    11: "NOVIEMBRE",
    12: "DICIEMBRE",
}

# confirmado por RRHH 2026-09-10 (ver docstring del modulo)
_CLASIFICACION_FALTA = {
    "J": "JUSTIFICADA",
    "L": "JUSTIFICADA",
    "P": "JUSTIFICADA",
    "C": "JUSTIFICADA",
    "U": "JUSTIFICADA",
    "I": "INJUSTIFICADA",
    "3T": "INJUSTIFICADA",
    "H": "INJUSTIFICADA",
}


@dataclass
class ResultadoConsolidado:
    consolidado_dre_id: str | None = None
    version: int | None = None
    personas: int = 0
    reportes_incluidos: int = 0
    validaciones_pendientes: int = 0
    # trabajador_en_reporte de los reportes incluidos que no entraron al detalle por no tener
    # identidad/rol resueltos (P05: expuesto, no una exclusion silenciosa).
    personas_omitidas_sin_identidad: int = 0
    error: str | None = None


def generar_consolidado(
    conn: psycopg.Connection,
    periodo: date,
    nivel_modalidad: str,
    *,
    confirmar: bool = True,
    filas_elegidas: set[str] | None = None,
) -> ResultadoConsolidado:
    """Crea una version nueva de consolidado_dre para (periodo, nivel_modalidad), con un
    consolidado_dre_detalle por trabajador que aparece en el (los) reporte_asistencia mas reciente
    de cada institucion de ese nivel/periodo, y su desglose de dias por estado de asistencia
    (consolidado_dre_detalle_estado), tomado directamente de `asistencia_dia` -- ANEXO 3 manda
    sobre ANEXO 4, mismo criterio que el importador (ADR-019 de v1)."""
    resultado = ResultadoConsolidado()
    periodo = date(periodo.year, periodo.month, 1)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (s.reporte_asistencia_serie_id) s.reporte_asistencia_serie_id,
                   r.reporte_asistencia_id, r.institucion_educativa_id, r.clasificacion_codigos
            FROM reporte_asistencia_serie s
            JOIN reporte_asistencia r ON r.reporte_asistencia_serie_id = s.reporte_asistencia_serie_id
            WHERE s.periodo = %s AND r.estado NOT IN ('RECHAZADO','HISTORICA') AND (
                fn_nivel_canonico(s.nivel_modalidad) = fn_nivel_canonico(%s)
                OR s.nivel_modalidad = %s)
            ORDER BY s.reporte_asistencia_serie_id, r.version DESC
            """,
            (periodo, nivel_modalidad, nivel_modalidad),
        )
        reportes = cur.fetchall()
        if not reportes:
            resultado.error = f"no hay reportes de asistencia para nivel={nivel_modalidad!r} periodo={periodo}"
            return resultado

        cur.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM consolidado_dre "
            "WHERE periodo = %s AND nivel_modalidad = %s",
            (periodo, nivel_modalidad),
        )
        version = cur.fetchone()["v"]

        cur.execute(
            """
            INSERT INTO consolidado_dre (periodo, nivel_modalidad, version, estado)
            VALUES (%s, %s, %s, 'BORRADOR')
            RETURNING consolidado_dre_id
            """,
            (periodo, nivel_modalidad, version),
        )
        consolidado_dre_id = cur.fetchone()["consolidado_dre_id"]
        resultado.consolidado_dre_id = consolidado_dre_id
        resultado.version = version
        resultado.reportes_incluidos = len(reportes)

        for fila in reportes:
            reporte_asistencia_id = fila["reporte_asistencia_id"]
            institucion_educativa_id = fila["institucion_educativa_id"]

            cur.execute(
                "INSERT INTO consolidado_reporte_fuente VALUES (%s, %s)",
                (consolidado_dre_id, reporte_asistencia_id),
            )

            cur.execute(
                """
                SELECT count(*) AS n FROM trabajador_en_reporte
                WHERE reporte_asistencia_id = %s
                      AND (trabajador_id IS NULL OR rol_laboral_id IS NULL)
                """,
                (reporte_asistencia_id,),
            )
            resultado.personas_omitidas_sin_identidad += cur.fetchone()["n"]

            cur.execute(
                """
                SELECT trabajador_en_reporte_id, trabajador_id, rol_laboral_id, vinculo_trabajador_ie_id
                FROM trabajador_en_reporte
                WHERE reporte_asistencia_id = %s
                      AND trabajador_id IS NOT NULL AND rol_laboral_id IS NOT NULL
                """,
                (reporte_asistencia_id,),
            )
            personas = cur.fetchall()
            if filas_elegidas is not None:
                personas = [
                    p
                    for p in personas
                    if str(p["trabajador_en_reporte_id"]) in filas_elegidas
                ]

            for p in personas:
                # vinculo_trabajador_ie_id entra al grano (revision de modelo 2026-09-10): un
                # trabajador puede tener mas de un vinculo concurrente en la misma institucion y
                # rol (titular + encargatura, o CUADRO_DE_HORAS + POR_REEMPLAZO simultaneos --
                # casos reales confirmados en NEXUS). Sin esto, el segundo vinculo se perdia.
                cur.execute(
                    """
                    INSERT INTO consolidado_dre_detalle
                        (consolidado_dre_id, institucion_educativa_id, trabajador_id, rol_laboral_id,
                         vinculo_trabajador_ie_id)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (consolidado_dre_id, trabajador_id, institucion_educativa_id, rol_laboral_id,
                                 vinculo_trabajador_ie_id)
                    DO NOTHING
                    RETURNING consolidado_dre_detalle_id
                    """,
                    (
                        consolidado_dre_id,
                        institucion_educativa_id,
                        p["trabajador_id"],
                        p["rol_laboral_id"],
                        p["vinculo_trabajador_ie_id"],
                    ),
                )
                fila_detalle = cur.fetchone()
                if fila_detalle is None:
                    continue  # mismo trabajador+vinculo ya agregado desde otro reporte de este nivel/periodo
                detalle_id = fila_detalle["consolidado_dre_detalle_id"]
                resultado.personas += 1

                cur.execute(
                    """
                    INSERT INTO consolidado_dre_detalle_fuente
                        (consolidado_dre_detalle_id, reporte_asistencia_id, trabajador_en_reporte_id)
                    VALUES (%s, %s, %s)
                    """,
                    (detalle_id, reporte_asistencia_id, p["trabajador_en_reporte_id"]),
                )

                cur.execute(
                    "SELECT * FROM asistencia_dia WHERE trabajador_en_reporte_id=%s",
                    (p["trabajador_en_reporte_id"],),
                )
                efectivos = [marca_efectiva(fila, d) for d in cur.fetchall()]
                cantidades = Counter(
                    d["estado_asistencia_id"]
                    for d in efectivos
                    if d["estado_captura"] in {"REGISTRADO", "DERIVADO"}
                )
                for estado_id, cantidad in cantidades.items():
                    cur.execute(
                        """
                        INSERT INTO consolidado_dre_detalle_estado
                            (consolidado_dre_detalle_id, estado_asistencia_id, cantidad_dias)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (consolidado_dre_detalle_id, estado_asistencia_id) DO NOTHING
                        """,
                        (detalle_id, estado_id, cantidad),
                    )

                _congelar_clasificacion_faltas(
                    cur, detalle_id, p["trabajador_en_reporte_id"]
                )

        resultado.validaciones_pendientes = _contar_alertas_pendientes(
            cur, consolidado_dre_id
        )

    aplicar_dias_esperados_a_detalle(conn, consolidado_dre_id, periodo, confirmar=False)
    from .remuneracion import congelar_cruces

    congelar_cruces(conn, consolidado_dre_id)
    if confirmar:
        conn.commit()
    return resultado


def _congelar_clasificacion_faltas(
    cur: psycopg.Cursor, consolidado_dre_detalle_id: str, trabajador_en_reporte_id: str
) -> None:
    """Fija en fuente_calculo, al momento de generar (no de exportar), las fechas JUSTIFICADAS/
    INJUSTIFICADAS segun _CLASIFICACION_FALTA -- P05: si esa regla cambia mas adelante en el
    codigo, una version ya generada no debe cambiar de significado al re-exportarla (DT03).
    Usa `||` para fusionar, no pisar, lo que aplicar_dias_esperados_a_detalle escriba despues en la
    misma columna."""
    cur.execute(
        """
        SELECT cea.codigo, ad.fecha
        FROM asistencia_dia ad
        JOIN catalogo_estado_asistencia cea ON cea.estado_asistencia_id = ad.estado_asistencia_id
        WHERE ad.trabajador_en_reporte_id = %s AND ad.estado_captura IN ('REGISTRADO', 'DERIVADO')
        ORDER BY ad.fecha
        """,
        (trabajador_en_reporte_id,),
    )
    justificadas, injustificadas = [], []
    for fila in cur.fetchall():
        clasificacion = _CLASIFICACION_FALTA.get(fila["codigo"])
        if clasificacion == "JUSTIFICADA":
            justificadas.append(fila["fecha"].isoformat())
        elif clasificacion == "INJUSTIFICADA":
            injustificadas.append(fila["fecha"].isoformat())

    cur.execute(
        "UPDATE consolidado_dre_detalle SET fuente_calculo = fuente_calculo || %s WHERE consolidado_dre_detalle_id = %s",
        (
            Jsonb(
                {
                    "clasificacion_reportada": {
                        "justificadas": justificadas,
                        "injustificadas": injustificadas,
                    }
                }
            ),
            consolidado_dre_detalle_id,
        ),
    )


def _contar_alertas_pendientes(cur: psycopg.Cursor, consolidado_dre_id: str) -> int:
    """Mismo recuento en generacion y en exportacion (P05, corrige DT04): alertas PENDIENTE
    ligadas a los reportes efectivamente incluidos en este consolidado, no a todo el periodo/nivel
    (que incluiria versiones de reporte superadas y personas sin identidad excluidas del detalle)."""
    cur.execute(
        """
        SELECT count(DISTINCT v.validacion_reporte_id) AS n
        FROM validacion_reporte v
        JOIN consolidado_reporte_fuente f ON f.reporte_asistencia_id = v.reporte_asistencia_id
        WHERE f.consolidado_dre_id = %s AND v.estado = 'PENDIENTE'
        """,
        (consolidado_dre_id,),
    )
    return cur.fetchone()["n"]


def _formatear_fechas(fechas: list[date]) -> str:
    if not fechas:
        return "-"
    mes = MESES_NOMBRE[fechas[0].month]
    dias = ", ".join(str(f.day) for f in sorted(fechas))
    return f"{dias} DE {mes}"


def _negrita(ws, celda: str) -> None:
    ws[celda].font = Font(bold=True)


def _linea(texto: str | None) -> str:
    """Colapsa saltos de linea/espacios del texto crudo del reporte (ej. "Docente de\\n aula") a
    una sola linea legible -- mismo contenido, sin cambiar su significado."""
    if not texto:
        return "-"
    return re.sub(r"\s+", " ", texto).strip() or "-"


def _nombre_completo(p) -> str:
    """ "PATERNO MATERNO NOMBRES" (mismo estilo que el DRE real, ej. "ARISTA TEJADA JOSE LUIS").
    `apellido_materno` es '-' cuando el trabajador se creo desde un reporte que trae el nombre en
    un solo campo (ver `_obtener_o_crear_trabajador` en el importador de asistencia) -- se omite en
    vez de mostrar el guion como si fuera un apellido real."""
    partes = [p["apellido_paterno"], p["apellido_materno"], p["nombres"]]
    partes = [x for x in partes if x and x != "-"]
    return _linea(" ".join(partes))


def exportar_consolidado_xlsx(
    conn: psycopg.Connection,
    consolidado_dre_id: str,
    ruta_salida: Path,
    *,
    confirmar: bool = True,
    estado_revision: str | None = None,
    anexos_cierre: dict | None = None,
    anexo_coherencia: dict | None = None,
) -> Path:
    """Exporta un consolidado_dre ya generado al formato real encontrado en
    `data/raw/data_brindada_por_ugel/asistencia_marzo_2026_a_junio_2026` (DRE/UGEL/JEFATURA DE
    PERSONAL/CONSOLIDADO DE ASISTENCIA DEL NIVEL [X] - UGEL LUYA, tabla de personas agrupada por
    institucion con encabezado de 3 filas para FALTAS JUSTIFICADAS/INJUSTIFICADAS)."""
    # Una salida web conserva bytes y metadatos; reexportar no lee maestros ni alertas vivos.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o.ruta_objeto, o.sha256 FROM web_salida w JOIN objeto_archivo o "
            "USING (objeto_archivo_id) WHERE w.consolidado_dre_id = %s",
            (consolidado_dre_id,),
        )
        conservado = cur.fetchone()
        if conservado:
            import shutil

            original = Path(conservado["ruta_objeto"])
            if not original.is_file() or sha256_de(original) != conservado["sha256"]:
                raise ValueError(
                    "La salida conservada no está disponible o cambió su contenido"
                )
            ruta_salida.parent.mkdir(parents=True, exist_ok=True)
            if original.resolve() != ruta_salida.resolve():
                shutil.copyfile(original, ruta_salida)
            return ruta_salida

    with conn.cursor() as cur:
        cur.execute(
            "SELECT periodo, nivel_modalidad, version, estado FROM consolidado_dre "
            "WHERE consolidado_dre_id = %s",
            (consolidado_dre_id,),
        )
        cabecera = cur.fetchone()
        if cabecera is None:
            raise ValueError(f"consolidado_dre {consolidado_dre_id} no existe")

        # Mismo recuento que generar_consolidado (_contar_alertas_pendientes): no se recalcula con
        # otra logica para no repetir la divergencia que documentaba DT04.
        alertas_pendientes = _contar_alertas_pendientes(cur, consolidado_dre_id)

        cur.execute(
            """
            SELECT d.consolidado_dre_detalle_id, d.institucion_educativa_id, d.fuente_calculo,
                   ie.nombre_ie, ie.localidad, ie.centro_poblado,
                   t.apellido_paterno, t.apellido_materno, t.nombres,
                   f.trabajador_en_reporte_id, ter.cargo_reportado_raw, ter.telefono_reportado_raw
            FROM consolidado_dre_detalle d
            JOIN institucion_educativa ie ON ie.institucion_educativa_id = d.institucion_educativa_id
            JOIN trabajador t ON t.trabajador_id = d.trabajador_id
            JOIN consolidado_dre_detalle_fuente f ON f.consolidado_dre_detalle_id = d.consolidado_dre_detalle_id
            JOIN trabajador_en_reporte ter ON ter.trabajador_en_reporte_id = f.trabajador_en_reporte_id
            WHERE d.consolidado_dre_id = %s
            ORDER BY ie.nombre_ie, t.apellido_paterno, t.apellido_materno, t.nombres
            """,
            (consolidado_dre_id,),
        )
        personas = cur.fetchall()

        for p in personas:
            # Leido de fuente_calculo, congelado en generar_consolidado (_congelar_clasificacion_faltas,
            # P05) -- no se recalcula aqui contra asistencia_dia ni contra la regla vigente en el
            # codigo al momento de exportar (DT03: una version ya generada no debe cambiar de
            # significado si _CLASIFICACION_FALTA cambia despues).
            clasificacion = p["fuente_calculo"].get("clasificacion_faltas", {})
            p["_clasificacion_estado"] = clasificacion.get("estado", "ANTERIOR")
            p["_justificadas"] = [
                date.fromisoformat(f) for f in clasificacion.get("justificadas", [])
            ]
            p["_injustificadas"] = [
                date.fromisoformat(f) for f in clasificacion.get("injustificadas", [])
            ]

    periodo: date = cabecera["periodo"]
    mes_nombre = MESES_NOMBRE[periodo.month]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{mes_nombre[:10]} {periodo.year}"

    ws["A1"] = "DIRECCIÓN REGIONAL DE EDUCACIÓN DE AMAZONAS"
    ws["A2"] = "  UNIDAD DE GESTIÓN EDUCATIVA LOCAL DE LUYA"
    ws["A3"] = "        JEFATURA DE PERSONAL"
    ws["A4"] = (
        f"             CONSOLIDADO DE ASISTENCIA DEL NIVEL {cabecera['nivel_modalidad'].upper()} "
        "- UGEL LUYA "
    )
    ws["A6"] = f"MES DE {mes_nombre} - {periodo.year}"
    ws["A7"] = (
        f"ESTADO: {estado_revision or cabecera['estado']} (version {cabecera['version']}) -- "
        f"{alertas_pendientes} alerta(s) pendiente(s) de revisión"
    )
    ws["A8"] = (
        "Faltas: cruce de calendario y asistencia. Pendiente no equivale a cero; consultar el detalle diario en AsistIA."
    )
    for celda in ("A1", "A4", "A6", "A7"):
        _negrita(ws, celda)

    fila_h1 = 9
    fila_h2 = fila_h1 + 1
    fila_h3 = fila_h1 + 2
    encabezados_simples = {
        1: "N°",
        2: "INSTITUCIÓN EDUCATIVA",
        3: "LUGAR",
        4: "CENTRO POBLADO",
        5: "APELLIDOS Y NOMBRES",
        6: "CARGO",
        7: "TELEFONO",
        12: "HORAS NO JUSTIFICADAS",
        13: "HORAS JUSTIFICADAS",
        14: "DIAS",
    }
    for col, texto in encabezados_simples.items():
        ws.cell(row=fila_h1, column=col, value=texto)
        ws.merge_cells(
            start_row=fila_h1, start_column=col, end_row=fila_h3, end_column=col
        )

    ws.cell(row=fila_h1, column=8, value="FALTAS")
    ws.merge_cells(start_row=fila_h1, start_column=8, end_row=fila_h1, end_column=11)
    ws.cell(row=fila_h2, column=8, value="JUSTIFICADAS")
    ws.merge_cells(start_row=fila_h2, start_column=8, end_row=fila_h2, end_column=9)
    ws.cell(row=fila_h2, column=10, value="INJUSTIFICADAS")
    ws.merge_cells(start_row=fila_h2, start_column=10, end_row=fila_h2, end_column=11)
    ws.cell(row=fila_h3, column=8, value="N° DIAS")
    ws.cell(row=fila_h3, column=9, value="FECHAS")
    ws.cell(row=fila_h3, column=10, value="N° DIAS")
    ws.cell(row=fila_h3, column=11, value="FECHA")

    for row in ws.iter_rows(min_row=fila_h1, max_row=fila_h3, min_col=1, max_col=14):
        for c in row:
            c.font = Font(bold=True)
            c.alignment = Alignment(
                wrap_text=True, horizontal="center", vertical="center"
            )

    fila = fila_h3 + 1
    numero_institucion = 0
    institucion_anterior = None
    fila_inicio_grupo = fila
    for idx, p in enumerate(personas):
        es_nueva_institucion = p["institucion_educativa_id"] != institucion_anterior
        if es_nueva_institucion:
            if institucion_anterior is not None and fila - fila_inicio_grupo > 1:
                for col in (2, 3, 4):
                    ws.merge_cells(
                        start_row=fila_inicio_grupo,
                        start_column=col,
                        end_row=fila - 1,
                        end_column=col,
                    )
            numero_institucion += 1
            institucion_anterior = p["institucion_educativa_id"]
            fila_inicio_grupo = fila
            ws.cell(row=fila, column=1, value=numero_institucion)
            ws.cell(row=fila, column=2, value=p["nombre_ie"])
            ws.cell(row=fila, column=3, value=p["localidad"] or "-")
            ws.cell(row=fila, column=4, value=p["centro_poblado"] or "-")

        ws.cell(row=fila, column=5, value=_nombre_completo(p))
        ws.cell(row=fila, column=6, value=_linea(p["cargo_reportado_raw"]))
        ws.cell(row=fila, column=7, value=p["telefono_reportado_raw"] or "-")
        ws.cell(
            row=fila,
            column=8,
            value=(
                len(p["_justificadas"])
                if p["_clasificacion_estado"] in {"CALCULABLE", "ANTERIOR"}
                else "Pendiente"
            ),
        )
        ws.cell(row=fila, column=9, value=_formatear_fechas(p["_justificadas"]))
        ws.cell(
            row=fila,
            column=10,
            value=(
                len(p["_injustificadas"])
                if p["_clasificacion_estado"] in {"CALCULABLE", "ANTERIOR"}
                else "Pendiente"
            ),
        )
        ws.cell(row=fila, column=11, value=_formatear_fechas(p["_injustificadas"]))
        ws.cell(
            row=fila, column=12, value="-"
        )  # horas no justificadas: fuera de alcance, ver docstring
        ws.cell(row=fila, column=13, value="-")  # horas justificadas: idem
        ws.cell(
            row=fila, column=14, value="-"
        )  # DIAS: siempre en blanco en los 4 archivos reales revisados

        fila += 1

    if institucion_anterior is not None and fila - fila_inicio_grupo > 1:
        for col in (2, 3, 4):
            ws.merge_cells(
                start_row=fila_inicio_grupo,
                start_column=col,
                end_row=fila - 1,
                end_column=col,
            )

    anchos = {
        1: 5,
        2: 20,
        3: 18,
        4: 16,
        5: 32,
        6: 16,
        7: 14,
        9: 22,
        11: 22,
        12: 12,
        13: 12,
        14: 8,
    }
    for col, ancho in anchos.items():
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = ancho

    if anexo_coherencia is not None:
        from asistia.coherencia import exportar_control

        exportar_control(wb, anexo_coherencia)
        ws["A7"] = ws["A7"].value + (
            f" · {sum(c['pendientes'] for c in anexo_coherencia.values())} caso(s) de coherencia pendiente(s); consultar el detalle en AsistIA"
        )
    if anexos_cierre:
        _anexar_cierre(wb, anexos_cierre)
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    # Texto recibido de documentos no se convierte en fórmula ejecutable en la salida.
    for hoja in wb:
        for row in hoja:
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"
    from .remuneracion import exportar_cruces

    exportar_cruces(wb, personas)
    wb.save(ruta_salida)

    sha256 = sha256_de(ruta_salida)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT objeto_archivo_id FROM objeto_archivo WHERE sha256 = %s", (sha256,)
        )
        fila_obj = cur.fetchone()
        if fila_obj:
            objeto_archivo_id = fila_obj["objeto_archivo_id"]
        else:
            cur.execute(
                """
                INSERT INTO objeto_archivo (sha256, ruta_objeto, mime_type, tamano_bytes)
                VALUES (%s, %s, %s, %s)
                RETURNING objeto_archivo_id
                """,
                (
                    sha256,
                    str(ruta_salida),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ruta_salida.stat().st_size,
                ),
            )
            objeto_archivo_id = cur.fetchone()["objeto_archivo_id"]
        cur.execute(
            "UPDATE consolidado_dre SET archivo_exportado_id = %s WHERE consolidado_dre_id = %s",
            (objeto_archivo_id, consolidado_dre_id),
        )
    if confirmar:
        conn.commit()

    return ruta_salida


def _anexar_cierre(wb, cierre):
    """El alcance queda dentro de los bytes conservados, antes de calcular su huella."""
    alcance = wb.create_sheet("Alcance y supuestos")
    alcance.append(
        [
            "Revisión técnica del cierre",
            "BORRADOR: pendiente de cotejo y aprobación de RRHH",
        ]
    )
    for regla in cierre.get("reglas", []):
        alcance.append(["Regla", regla])
    alcance.append(["Filas seleccionadas", len(cierre.get("filas_elegidas", []))])
    alcance.append(["Decisiones de superposición", len(cierre.get("decisiones", []))])
    instituciones = wb.create_sheet("Revisión institucional")
    instituciones.append(
        [
            "Código modular",
            "Anexo",
            "Institución",
            "Resultado técnico",
            "Pendientes",
            "Supuestos",
        ]
    )
    for i in cierre.get("instituciones", []):
        instituciones.append(
            [
                i["cod_mod"],
                i["anexo"],
                i["nombre_ie"],
                i["resultado"],
                "; ".join(i["detalle"].get("problemas", [])),
                "; ".join(i["detalle"].get("supuestos", [])),
            ]
        )
    decisiones = wb.create_sheet("Selección de fuentes")
    decisiones.append(
        [
            "Institución",
            "Fila utilizada",
            "Filas alternativas",
            "Criterio",
            "Fechas en contradicción",
        ]
    )
    for d in cierre.get("decisiones", []):
        decisiones.append(
            [
                d["institucion_id"],
                d["elegida"],
                ", ".join(d["alternativas"]),
                d["motivo"],
                ", ".join(d["fechas_conflicto"]),
            ]
        )
    for hoja in (alcance, instituciones, decisiones):
        hoja.freeze_panes = "A2"
        hoja.auto_filter.ref = hoja.dimensions
        for cell in hoja[1]:
            cell.font = Font(bold=True)
        for col in range(1, hoja.max_column + 1):
            hoja.column_dimensions[openpyxl.utils.get_column_letter(col)].width = (
                28 if col < 4 else 65
            )
        for row in hoja:
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
