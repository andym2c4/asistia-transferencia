"""CLI de asistia: entrada declarada en pyproject (`asistia.cli:cli`), ausente hasta esta version.
Envuelve el ciclo NEXUS -> calendario -> asistencia -> consolidado ya implementado en
asistia.importar/asistia.consolidado sin modificarlos. Las migraciones tienen su propio
entrypoint (migrations/scripts/aplicar.py) y no se envuelven aqui.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import click

from asistia.consolidado.estimacion import DECLARACION_VIGENTE, estimar
from asistia.consolidado.generar import exportar_consolidado_xlsx, generar_consolidado
from asistia.consolidado.resolucion import (
    ESTADOS_RESOLUCION,
    UNIVERSO_DECLARADO,
    estado_resolucion,
    instituciones_sin_nivel_canonico,
    resumen_ugel,
)
from asistia.consolidado.revision import (
    evaluar_revision,
    listar_alertas_con_fuente,
    resolver_alerta,
)
from asistia.consolidado.universo_esperado import calcular_poblacion_esperada
from asistia.db import conectar
from asistia.importar.asistencia import importar_asistencia
from asistia.importar.calendario import importar_calendario
from asistia.importar.nexus import importar_nexus
from asistia.niveles import NIVELES
from asistia.originales import (
    CONSERVADO,
    INTACTO,
    conservar_original,
    verificar_originales,
)


@click.group("web")
def web():
    """Acceso y servidor web local para RRHH."""


@web.command("crear-usuario")
@click.option("--nombre", required=True)
@click.option("--email", help="Correo de acceso; opcional si se indica --usuario.")
@click.option("--usuario", help="Nombre de acceso sin arroba (3 a 64 caracteres).")
@click.password_option(confirmation_prompt=True)
def crear_usuario_web(nombre, email, usuario, password):
    """Crea un operador nuevo. Requiere migraciones aplicadas; no reemplaza cuentas."""
    from asistia.web.auth import crear_usuario

    try:
        with conectar() as conn:
            crear_usuario(conn, nombre, email, password, usuario=usuario)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo("Operador creado. La contraseña no se guarda en texto plano.")


@web.command("crear-clave")
@click.option(
    "--archivo",
    type=click.Path(path_type=Path),
    default=Path("data/web/secret.key"),
    show_default=True,
)
def crear_clave_web(archivo):
    """Conserva una clave aleatoria local; nunca sobrescribe una existente."""
    import secrets

    archivo.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with archivo.open("x") as f:
            archivo.chmod(0o600)
            f.write(secrets.token_urlsafe(48))
    except FileExistsError:
        raise click.ClickException(
            "La clave ya existe. Se conservó sin cambios."
        ) from None
    click.echo(f"Clave local conservada en {archivo}. No la compartas ni la versiones.")


@web.command("servir")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option(
    "--puerto", default=8000, type=click.IntRange(1, 65535), show_default=True
)
def servir_web(host, puerto):
    """Sirve la aplicación con Waitress; no activa depuración ni aplica migraciones."""
    from waitress import serve

    from asistia.web import create_app

    try:
        app = create_app()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(f"ASISTIA disponible en http://{host}:{puerto}")
    serve(
        app, host=host, port=puerto, threads=4, max_request_body_size=32 * 1024 * 1024
    )


@click.group()
def cli() -> None:
    """Ciclo NEXUS -> calendario -> asistencia -> consolidado sobre ASISTIA_DATABASE_URL."""


cli.add_command(web)

from asistia.cierre.cli import cierre

cli.add_command(cierre)

from asistia.experimental.cli import experimental

cli.add_command(experimental)


@cli.group("importar")
def importar() -> None:
    """Importa un archivo de origen a la base conectada."""


@importar.command("nexus")
@click.argument("archivo", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--fecha-corte", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
def importar_nexus_cmd(archivo: Path, fecha_corte) -> None:
    with conectar() as conn:
        resultado = importar_nexus(conn, archivo, fecha_corte.date())
    click.echo(resultado)
    if resultado.errores:
        raise SystemExit(1)


@importar.command("calendario")
@click.argument("archivo", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--familia-formato", default="UGEL_LUYA_CALENDARIO_2026", show_default=True
)
def importar_calendario_cmd(archivo: Path, familia_formato: str) -> None:
    with conectar() as conn:
        resultado = importar_calendario(conn, archivo, familia_formato)
    click.echo(resultado)
    if resultado.error:
        raise SystemExit(1)


@importar.command("asistencia")
@click.argument("archivo", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def importar_asistencia_cmd(archivo: Path) -> None:
    with conectar() as conn:
        resultado = importar_asistencia(conn, archivo)
    click.echo(resultado)
    if resultado.error:
        raise SystemExit(1)


@cli.group("consolidado")
def consolidado() -> None:
    """Genera o exporta el consolidado por nivel/periodo."""


@consolidado.command("generar")
@click.option("--periodo", required=True, type=click.DateTime(formats=["%Y-%m"]))
@click.option("--nivel", "nivel_modalidad", required=True)
def generar_consolidado_cmd(periodo, nivel_modalidad: str) -> None:
    with conectar() as conn:
        resultado = generar_consolidado(conn, periodo.date(), nivel_modalidad)
    click.echo(resultado)
    if resultado.error:
        raise SystemExit(1)


@consolidado.command("exportar")
@click.argument("consolidado_dre_id")
@click.argument("salida", type=click.Path(dir_okay=False, path_type=Path))
def exportar_consolidado_cmd(consolidado_dre_id: str, salida: Path) -> None:
    with conectar() as conn:
        ruta = exportar_consolidado_xlsx(conn, consolidado_dre_id, salida)
    click.echo(f"exportado: {ruta}")


@consolidado.command("poblacion-esperada")
@click.option("--periodo", required=True, type=click.DateTime(formats=["%Y-%m"]))
@click.option("--nivel", "nivel_modalidad", required=True)
def poblacion_esperada_cmd(periodo, nivel_modalidad: str) -> None:
    with conectar() as conn:
        resultado = calcular_poblacion_esperada(conn, periodo.date(), nivel_modalidad)
    click.echo(
        f"instituciones_evaluadas={resultado.instituciones_evaluadas} "
        f"sin_calendario={len(resultado.instituciones_sin_calendario)} "
        f"vinculos_esperados={resultado.vinculos_esperados} "
        f"con_reporte={resultado.vinculos_con_reporte} "
        f"sin_reporte={len(resultado.vinculos_sin_reporte)}"
    )
    for v in resultado.vinculos_sin_reporte:
        click.echo(f"  sin_reporte: {v}")
    if resultado.error:
        raise SystemExit(1)


@consolidado.command("resolucion")
@click.option("--periodo", required=True, type=click.DateTime(formats=["%Y-%m"]))
@click.option(
    "--nivel",
    "niveles",
    multiple=True,
    help="Nivel canonico (INICIAL, PRIMARIA, ...). Repetible; por defecto todos.",
)
@click.option(
    "--csv",
    "ruta_csv",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Escribe el detalle por institucion en CSV.",
)
@click.option(
    "--json",
    "ruta_json",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Escribe el resumen y la estimacion declarada en JSON.",
)
def resolucion_cmd(periodo, niveles, ruta_csv, ruta_json) -> None:
    """Estado de resolucion por institucion y estimacion declarada de esfuerzo.

    El desglose es el de M04 sobre el universo operativo NEXUS; no es la tasa de cobertura de
    M04, que exige un manifiesto validado contra el padron. La estimacion es ESTIMADO_DECLARADO,
    nunca una medicion de M01.
    """
    objetivo = list(niveles) or list(NIVELES)
    with conectar() as conn:
        resumenes = [
            estado_resolucion(conn, periodo.date(), nivel) for nivel in objetivo
        ]
        fuera = instituciones_sin_nivel_canonico(conn)

    salida = {
        "periodo": periodo.date().isoformat(),
        "universo": UNIVERSO_DECLARADO,
        "instituciones_sin_nivel_canonico": len(fuera),
        "niveles": [],
    }
    for resumen in resumenes:
        e = estimar(resumen)
        if not resumen.conciliado():
            raise SystemExit(f"desglose no concilia en {resumen.nivel}")
        click.echo(
            f"{resumen.nivel:11} universo={resumen.total:4} "
            + " ".join(f"{k.lower()}={v}" for k, v in resumen.por_estado.items())
            + f" | {e.estado_calculo}"
            + (
                f" reduccion_estimada={e.reduccion_pct}% {e.reduccion_pct_banda}"
                if e.reduccion_pct is not None
                else ""
            )
        )
        salida["niveles"].append(
            {
                "nivel": resumen.nivel,
                "universo_unidades": resumen.total,
                "por_estado": resumen.por_estado,
                "filas_sin_identidad": resumen.filas_sin_identidad,
                "alertas_pendientes": resumen.alertas_pendientes,
                "estimacion": {
                    "etiqueta": e.etiqueta,
                    "estado_calculo": e.estado_calculo,
                    "esfuerzo_relativo": e.esfuerzo_relativo,
                    "reduccion_pct": e.reduccion_pct,
                    "reduccion_pct_banda": e.reduccion_pct_banda,
                    "supuestos_pesos": {
                        est: e.supuestos.peso(est) for est in ESTADOS_RESOLUCION
                    },
                    "limitaciones": list(e.supuestos.limitaciones),
                    "nota": e.nota,
                },
            }
        )

    # Grano de toda la UGEL: unica forma correcta de aplicar una declaracion por UGEL-mes.
    with conectar() as conn:
        agregado = resumen_ugel(conn, periodo.date())
    eu = estimar(agregado, declaracion=DECLARACION_VIGENTE)
    click.echo(
        f"{'UGEL':11} universo={agregado.total:4} "
        + " ".join(f"{k.lower()}={v}" for k, v in agregado.por_estado.items())
        + f" | {eu.estado_calculo} reduccion_estimada={eu.reduccion_pct}% "
        + f"{eu.reduccion_pct_banda}"
    )
    if eu.tiempo_manual_min:
        click.echo(
            f"            manual declarado={eu.tiempo_manual_min[0]}-{eu.tiempo_manual_min[1]} min "
            f"| asistido estimado={eu.tiempo_asistido_min[0]:.0f}-{eu.tiempo_asistido_min[1]:.0f} min "
            f"({eu.declaracion.fuente}, {eu.declaracion.fecha})"
        )
    salida["ugel"] = {
        "universo_unidades": agregado.total,
        "por_estado": agregado.por_estado,
        "estimacion": {
            "etiqueta": eu.etiqueta,
            "estado_calculo": eu.estado_calculo,
            "esfuerzo_relativo": eu.esfuerzo_relativo,
            "reduccion_pct": eu.reduccion_pct,
            "reduccion_pct_banda": eu.reduccion_pct_banda,
            "tiempo_manual_min": eu.tiempo_manual_min,
            "tiempo_asistido_min": eu.tiempo_asistido_min,
            "declaracion": {
                "minutos_bajo": eu.declaracion.minutos_bajo,
                "minutos_alto": eu.declaracion.minutos_alto,
                "unidad": eu.declaracion.unidad,
                "fuente": eu.declaracion.fuente,
                "fecha": eu.declaracion.fecha,
            },
            "limitaciones": list(eu.supuestos.limitaciones),
            "nota": eu.nota,
        },
    }

    click.echo(
        f"instituciones sin nivel canonico (fuera de todo universo): {len(fuera)}"
    )

    if ruta_csv:
        with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "periodo",
                    "nivel",
                    "cod_mod",
                    "anexo",
                    "nombre_ie",
                    "nivel_modalidad_crudo",
                    "estado",
                    "reportes",
                    "reportes_revisados",
                    "version_maxima",
                    "filas",
                    "sin_identidad",
                    "alertas_pendientes",
                    "alertas_criticas",
                    "calendario_estado",
                    "calendario_dias_sin_determinar",
                ]
            )
            for resumen in resumenes:
                for i in resumen.instituciones:
                    w.writerow(
                        [
                            resumen.periodo.isoformat(),
                            resumen.nivel,
                            i.cod_mod,
                            i.anexo,
                            i.nombre_ie,
                            i.nivel_modalidad,
                            i.estado,
                            i.reportes,
                            i.reportes_revisados,
                            i.version_maxima,
                            i.filas,
                            i.sin_identidad,
                            i.alertas_pendientes,
                            i.alertas_criticas,
                            i.calendario_estado,
                            i.calendario_dias_sin_determinar,
                        ]
                    )
        click.echo(f"csv: {ruta_csv}")

    if ruta_json:
        ruta_json.write_text(
            json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        click.echo(f"json: {ruta_json}")


@cli.group("originales")
def originales() -> None:
    """Integridad de los archivos originales referenciados por el sistema (DT06)."""


@originales.command("verificar")
@click.option(
    "--almacen",
    type=click.Path(path_type=Path),
    default=Path("data/web"),
    show_default=True,
    help="Raiz del almacen por contenido donde buscar copias conservadas.",
)
@click.option(
    "--detalle",
    is_flag=True,
    help="Lista cada objeto, no solo los que tienen problema.",
)
def verificar_originales_cmd(almacen: Path, detalle: bool) -> None:
    """Comprueba que cada original registrado siga siendo recuperable.

    Un hash guardado prueba que el contenido se identifico al importar; no prueba que el archivo
    siga ahi. Si la ruta registrada ya no sirve, se busca la copia conservada por contenido antes de
    dar el original por perdido.
    """
    with conectar() as conn:
        resultado = verificar_originales(conn, almacen)

    click.echo(
        f"objetos={len(resultado.objetos)} "
        + " ".join(f"{k.lower()}={v}" for k, v in resultado.por_estado().items())
    )
    for o in resultado.objetos if detalle else resultado.problemas:
        marca = " " if o.recuperable else "!"
        extra = f"  (copia: {o.ruta_conservada})" if o.ruta_conservada else ""
        click.echo(f" {marca} {o.estado:10} {o.ruta_objeto}{extra}")
    if resultado.problemas:
        click.echo(
            f"\n{len(resultado.problemas)} original(es) no recuperables. AUSENTE: ni la ruta ni el "
            "almacen tienen el contenido; hay que reponer el archivo o declarar la perdida. "
            "ALTERADO es peor: la ruta abre pero su contenido ya no es el que se importo."
        )
        raise SystemExit(1)


@originales.command("conservar")
@click.option(
    "--almacen",
    type=click.Path(path_type=Path),
    default=Path("data/web"),
    show_default=True,
    help="Raiz del almacen por contenido (<almacen>/originales/<sha256>/).",
)
@click.option(
    "--confirmar",
    is_flag=True,
    help="Sin esta bandera solo se informa que se haria; no se copia nada.",
)
def conservar_originales_cmd(almacen: Path, confirmar: bool) -> None:
    """Copia al almacen los originales que hoy solo existen en su ubicacion de origen.

    No escribe en la base de datos: `objeto_archivo` es inmutable por diseno (trigger
    `fn_objeto_archivo_freeze`), asi que `ruta_objeto` conserva siempre la procedencia real. Lo que
    cambia es que, tras conservar, `verificar` puede recuperar el contenido por hash aunque el
    archivo de origen se mueva o se borre.

    Solo actua sobre los INTACTOS: de un ALTERADO o un AUSENTE no hay contenido fiable que copiar.
    """
    with conectar() as conn:
        resultado = verificar_originales(conn, almacen)

    pendientes = [o for o in resultado.objetos if o.estado == INTACTO]
    ya = sum(1 for o in resultado.objetos if o.estado == CONSERVADO)
    click.echo(
        f"intactos={len(pendientes)} ya_conservados={ya} "
        f"no_recuperables={len(resultado.problemas)}"
    )
    if not confirmar:
        for o in pendientes[:10]:
            click.echo(f"   se copiaria: {o.ruta_objeto}")
        if len(pendientes) > 10:
            click.echo(f"   ... y {len(pendientes) - 10} mas")
        click.echo("\nSimulacion. Repite con --confirmar para copiar.")
        return

    copiados = fallidos = 0
    for o in pendientes:
        try:
            conservar_original(Path(o.ruta_objeto), almacen, o.sha256)
            copiados += 1
        except (OSError, ValueError) as exc:
            fallidos += 1
            click.echo(f" ! no se pudo conservar {o.ruta_objeto}: {exc}")
    click.echo(
        f"conservados: {copiados}" + (f"  fallidos: {fallidos}" if fallidos else "")
    )


@cli.group("revision")
def revision() -> None:
    """Revision local del consolidado: alertas con fuente/autor/motivo, sin asociarla al ENVIADO."""


@revision.command("listar")
@click.argument("consolidado_dre_id")
def listar_revision_cmd(consolidado_dre_id: str) -> None:
    """Si es revisable (sin discrepancias ERROR pendientes). Para ver TODAS las alertas
    (incluidas las ADVERTENCIA de vinculos automaticos, con su fuente/autor/motivo) usa
    'revision detalle'."""
    with conectar() as conn:
        resultado = evaluar_revision(conn, consolidado_dre_id)
    click.echo(
        f"revisable={resultado.revisable} pendientes_criticos={len(resultado.pendientes_criticos)}"
    )
    for a in resultado.pendientes_criticos:
        click.echo(f"  {a}")
    if not resultado.revisable:
        raise SystemExit(1)


@revision.command("detalle")
@click.argument("consolidado_dre_id")
def detalle_revision_cmd(consolidado_dre_id: str) -> None:
    """Todas las alertas de este consolidado, con la fuente/autor/motivo del vinculo automatico
    que las origino cuando existe (no solo las criticas que 'revision listar' bloquea)."""
    with conectar() as conn, conn.cursor() as cur:
        alertas = listar_alertas_con_fuente(cur, consolidado_dre_id)
    for a in alertas:
        click.echo(f"{a}")


@revision.command("resolver")
@click.argument("validacion_reporte_id")
@click.option("--usuario", "resuelta_por", required=True, type=int)
@click.option("--motivo", "motivo_resolucion", default=None)
def resolver_revision_cmd(
    validacion_reporte_id: str, resuelta_por: int, motivo_resolucion: str | None
) -> None:
    with conectar() as conn:
        resolver_alerta(conn, validacion_reporte_id, resuelta_por, motivo_resolucion)
    click.echo(f"resuelta: {validacion_reporte_id}")


if __name__ == "__main__":
    cli()
