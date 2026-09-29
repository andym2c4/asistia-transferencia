"""Comandos del cierre; parámetros explícitos y salidas locales protegidas."""

from pathlib import Path

import click

from asistia.db import conectar

from .gemini import Gemini
from .ingesta import RAIZ, ejecutar, inventariar


@click.group("cierre")
def cierre():
    """Carga masiva, OCR y revisión técnica del corpus autorizado."""


@cierre.command("inventariar")
@click.option("--raiz", type=click.Path(exists=True, path_type=Path), default=RAIZ)
def inventario(raiz):
    with conectar() as conn:
        click.echo(inventariar(conn, raiz))


@cierre.command("importar")
@click.option(
    "--tipo",
    multiple=True,
    type=click.Choice(
        ["NEXUS", "DRE", "CALENDARIO_2026", "CALENDARIO_2025", "ASISTENCIA"]
    ),
)
@click.option("--solo-nativo", is_flag=True)
@click.option("--gemini-env", type=click.Path(exists=True, path_type=Path))
@click.option("--limite", type=click.IntRange(1), default=None)
def importar(tipo, solo_nativo, gemini_env, limite):
    motor = None if solo_nativo else Gemini(gemini_env)
    with conectar() as conn:
        click.echo(ejecutar(conn, list(tipo) or None, solo_nativo, motor, limite))


@cierre.command("estado")
def resumen():
    with conectar() as conn:
        for fila in conn.execute(
            "SELECT tipo,estado,count(*) AS archivos FROM cierre_documento GROUP BY tipo,estado ORDER BY tipo,estado"
        ).fetchall():
            click.echo(f"{fila['tipo']}: {fila['estado']} = {fila['archivos']}")


@cierre.command("proyectar-calendarios")
def proyectar():
    from .revision import proyectar_calendarios

    with conectar() as conn:
        click.echo({"proyectados": len(proyectar_calendarios(conn))})


@cierre.command("revisar")
@click.option("--periodo", type=click.DateTime(formats=["%Y-%m"]), default="2026-07")
def revisar(periodo):
    from .revision import revisar_instituciones

    with conectar() as conn:
        click.echo(revisar_instituciones(conn, periodo.date()))


@cierre.command("extraer")
@click.option(
    "--tipo",
    multiple=True,
    type=click.Choice(["ASISTENCIA", "CALENDARIO_2026", "CALENDARIO_2025"]),
)
@click.option("--trabajadores", type=click.IntRange(1, 5), default=3)
@click.option("--limite", type=click.IntRange(1), default=None)
def extraer(tipo, trabajadores, limite):
    from .lote_ocr import extraer_lote

    with conectar() as conn:
        extraer_lote(conn, list(tipo) or None, trabajadores, limite)


@cierre.command("salidas")
@click.option("--periodo", type=click.DateTime(formats=["%Y-%m"]), default="2026-07")
def salidas(periodo):
    from asistia.web import create_app

    from .salidas import generar_salidas

    with create_app().app_context(), conectar() as conn:
        for salida in generar_salidas(conn, periodo.date()):
            click.echo(salida)
