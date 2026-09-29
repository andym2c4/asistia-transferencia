"""Conservacion y verificacion de los originales referenciados por `objeto_archivo` (DT06).

`objeto_archivo` guarda el sha256 de cada original junto a una `ruta_objeto`. Registrar el hash
prueba que el contenido se identifico, pero **no** prueba que el archivo siga estando ni que siga
siendo el mismo: si la ruta apuntaba a una ubicacion volatil, la fila sigue afirmando una
procedencia que ya no se puede abrir. Eso ocurrio de verdad en el piloto -- un consolidado exportado
quedo apuntando a un directorio temporal de una sesion anterior, borrado despues.

Este modulo separa las dos cosas:

- `verificar_originales` dice, para cada objeto, si esta INTACTO, AUSENTE o ALTERADO. Comprueba
  contenido, no solo existencia: un archivo presente cuyo sha256 ya no coincide es peor que uno
  ausente, porque la ruta parece valida.
- `conservar_original` copia el archivo a un almacen por contenido (`<raiz>/originales/<sha256>/`),
  el mismo criterio que ya usa el recorrido web. Copiar convierte una referencia prestada en una
  copia propia: mover o borrar el archivo de origen deja de romper la trazabilidad.

**`objeto_archivo` es inmutable**: el trigger `fn_objeto_archivo_freeze` de la migracion 0001
rechaza cualquier UPDATE o DELETE sobre esa tabla, de modo que `ruta_objeto` queda fijada al
crearse y no se puede repuntar hacia la copia. Eso no es un obstaculo sino la forma correcta de
hacerlo: en vez de reescribir la fila, la verificacion busca la copia conservada **por hash** cuando
la ruta registrada ya no sirve, y lo informa como CONSERVADO. La fila conserva la procedencia
original -- de donde vino de verdad el archivo -- y el contenido sigue siendo recuperable.

La conservacion se hace con una copia real, nunca con un enlace duro: un enlace compartiria inodo
con el archivo de origen, asi que editarlo en su sitio alterararia tambien la copia y ajustar los
permisos de la copia cambiaria los del original ajeno.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psycopg

from asistia.db import sha256_de

INTACTO = "INTACTO"  # la ruta registrada existe y su contenido coincide con el hash
CONSERVADO = (
    "CONSERVADO"  # la ruta registrada ya no sirve, pero el almacen tiene el contenido
)
ALTERADO = "ALTERADO"  # la ruta existe y abre, pero su contenido ya no es el importado
AUSENTE = "AUSENTE"  # ni la ruta ni el almacen tienen el contenido

ESTADOS = (INTACTO, CONSERVADO, ALTERADO, AUSENTE)


@dataclass
class EstadoOriginal:
    objeto_archivo_id: str
    sha256: str
    ruta_objeto: str
    estado: str
    ruta_conservada: str | None = None

    @property
    def recuperable(self) -> bool:
        """El contenido sigue siendo accesible, por la ruta registrada o por el almacen."""
        return self.estado in (INTACTO, CONSERVADO)


@dataclass
class ResultadoVerificacion:
    objetos: list[EstadoOriginal]

    @property
    def recuperables(self) -> list[EstadoOriginal]:
        return [o for o in self.objetos if o.recuperable]

    @property
    def problemas(self) -> list[EstadoOriginal]:
        """Alterados y ausentes: el contenido importado ya no se puede recuperar de ningun sitio."""
        return [o for o in self.objetos if not o.recuperable]

    def por_estado(self) -> dict[str, int]:
        conteo = dict.fromkeys(ESTADOS, 0)
        for o in self.objetos:
            conteo[o.estado] += 1
        return conteo


def ruta_en_almacen(sha256: str, raiz: Path) -> Path | None:
    """Copia conservada de ese contenido, si el almacen la tiene y sigue coincidiendo."""
    carpeta = raiz / "originales" / sha256
    if not carpeta.is_dir():
        return None
    try:
        for candidato in sorted(carpeta.iterdir()):
            if candidato.is_file() and sha256_de(candidato) == sha256:
                return candidato
    except OSError:
        return None
    return None


def estado_de(
    sha256: str, ruta: str | Path, almacen: Path | None = None
) -> tuple[str, Path | None]:
    """Estado del original y, si aplica, la copia conservada que lo respalda.

    Compara contenido, no solo existencia: un archivo presente cuyo sha256 ya no coincide es peor
    que uno ausente, porque la ruta parece valida. Una ruta ilegible cuenta igual que una borrada --
    para la trazabilidad, un permiso denegado y un archivo inexistente dan el mismo resultado.
    """
    p = Path(ruta)
    try:
        if p.is_file() and sha256_de(p) == sha256:
            return INTACTO, None
        en_ruta_pero_distinto = p.is_file()
    except OSError:
        en_ruta_pero_distinto = False

    copia = ruta_en_almacen(sha256, almacen) if almacen is not None else None
    if copia is not None:
        return CONSERVADO, copia
    return (ALTERADO if en_ruta_pero_distinto else AUSENTE), None


def verificar_originales(
    conn: psycopg.Connection, almacen: Path | None = None
) -> ResultadoVerificacion:
    """Recorre todo `objeto_archivo` y comprueba cada original contra su hash registrado.

    Con `almacen`, un original cuya ruta registrada ya no sirve se busca por contenido en el almacen
    antes de darlo por perdido. `objeto_archivo` es inmutable, asi que la fila nunca se reescribe:
    la procedencia registrada se conserva y la copia se localiza aparte.
    """
    filas = conn.execute(
        "SELECT objeto_archivo_id, sha256, ruta_objeto FROM objeto_archivo ORDER BY ruta_objeto"
    ).fetchall()
    objetos = []
    for f in filas:
        estado, copia = estado_de(f["sha256"], f["ruta_objeto"], almacen)
        objetos.append(
            EstadoOriginal(
                objeto_archivo_id=str(f["objeto_archivo_id"]),
                sha256=f["sha256"],
                ruta_objeto=f["ruta_objeto"],
                estado=estado,
                ruta_conservada=str(copia) if copia is not None else None,
            )
        )
    return ResultadoVerificacion(objetos=objetos)


def conservar_original(ruta: Path, raiz: Path, sha256: str | None = None) -> Path:
    """Copia el original a `<raiz>/originales/<sha256>/<nombre>` y devuelve esa ruta.

    Mismo almacen por contenido que usa el recorrido web, para que ambos caminos conserven igual.
    Es idempotente: si la copia ya existe con el mismo contenido se reutiliza; si existe con
    contenido distinto se levanta un error en vez de sobrescribir, porque eso significaria que el
    almacen esta corrupto y sobrescribir borraria la evidencia de ello.

    Se copian los bytes; **no** se enlaza. Un enlace duro compartiria inodo con el archivo de
    origen, de modo que editarlo en su sitio alteraria tambien la copia -- justo lo que se quiere
    evitar -- y ajustar los permisos de la copia cambiaria los del archivo ajeno.
    """
    sha = sha256 or sha256_de(ruta)
    carpeta = raiz / "originales" / sha
    carpeta.mkdir(parents=True, exist_ok=True, mode=0o700)
    destino = carpeta / ruta.name

    if destino.exists():
        if sha256_de(destino) != sha:
            raise ValueError(
                f"La copia conservada en {destino} no coincide con el hash registrado. "
                "Revisar el almacenamiento antes de continuar; no se sobrescribe."
            )
        return destino

    destino.write_bytes(ruta.read_bytes())
    try:
        destino.chmod(0o400)
    except OSError:
        pass
    return destino
