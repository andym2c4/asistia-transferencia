"""Carga y reproducción de experimentos mensuales sin alterar asistencia real."""

import json
from pathlib import Path

import click

from asistia.db import conectar

from .datos import leer_corpus, publicar, redefinir
from .modelo import ejecutar


@click.group("experimental")
def experimental():
    """Reportes mensuales sintéticos e Isolation Forest."""


@experimental.command("importar-dre")
@click.argument(
    "carpeta", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--inicio-destino",
    default=None,
    help="Primer mes de destino AAAA-MM; conserva las distancias entre meses.",
)
@click.option("--motivo", required=True)
def importar(carpeta, inicio_destino, motivo):
    try:
        with conectar() as conn:
            reportes, manifiesto = leer_corpus(conn, carpeta)
            lote, nuevo = publicar(
                conn, reportes, manifiesto, inicio=inicio_destino, motivo=motivo
            )
        click.echo(
            json.dumps(
                {
                    "lote_id": str(lote),
                    "nuevo": nuevo,
                    "reportes": len(reportes),
                    "exclusiones": manifiesto["exclusiones_por_motivo"],
                }
            )
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None


@experimental.command("redefinir-periodos")
@click.argument("lote_id", type=click.UUID)
@click.option("--inicio-destino", required=True)
@click.option("--motivo", required=True)
def periodos(lote_id, inicio_destino, motivo):
    try:
        with conectar() as conn:
            lote, nuevo = redefinir(
                conn, lote_id, inicio_destino, autor="CLI", motivo=motivo
            )
        click.echo(json.dumps({"lote_id": str(lote), "nuevo": nuevo}))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None


@experimental.command("entrenar")
@click.argument("lote_id", type=click.UUID)
def entrenar(lote_id):
    try:
        with conectar() as conn:
            eid, nuevo = ejecutar(conn, lote_id)
        click.echo(json.dumps({"experimento_id": str(eid), "nuevo": nuevo}))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None


@experimental.command("exportar")
@click.argument("experimento_id", type=click.UUID)
@click.option("--salida", required=True, type=click.Path(path_type=Path))
def exportar(experimento_id, salida):
    from .salidas import paquete_zip

    with conectar() as conn:
        contenido = paquete_zip(conn, experimento_id)
    salida.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    salida.write_bytes(contenido)
    salida.chmod(0o600)
    click.echo(str(salida))
