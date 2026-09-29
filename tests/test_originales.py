"""DT06: conservacion y verificacion de los originales referenciados por `objeto_archivo`.

Guardar el sha256 de un archivo prueba que su contenido se identifico al importarlo; no prueba que
el archivo siga estando ni que siga siendo el mismo. Estas pruebas fijan esa diferencia: un original
borrado y uno modificado deben distinguirse entre si y de uno intacto, y conservar una copia debe
dejar de depender de la ubicacion desde la que se importo.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest

from asistia.db import registrar_documento, sha256_de
from asistia.originales import (
    ALTERADO,
    AUSENTE,
    CONSERVADO,
    INTACTO,
    conservar_original,
    estado_de,
    verificar_originales,
)

LIMA = ZoneInfo("America/Lima")


def _archivo(tmp_path, nombre="original.xlsx", contenido=b"contenido de prueba"):
    p = tmp_path / nombre
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(contenido)
    return p


# --- Estado de un original (sin base de datos) -------------------------------------------------


def test_un_original_intacto_se_reconoce_por_contenido(tmp_path):
    p = _archivo(tmp_path)
    assert estado_de(sha256_de(p), p)[0] == INTACTO


def test_un_original_borrado_es_ausente(tmp_path):
    p = _archivo(tmp_path)
    sha = sha256_de(p)
    p.unlink()
    assert estado_de(sha, p)[0] == AUSENTE


def test_un_original_modificado_es_alterado_no_intacto(tmp_path):
    """Es el caso peligroso: la ruta sigue siendo valida y el archivo abre, pero ya no es el que
    se importo. Comprobar solo existencia lo daria por bueno."""
    p = _archivo(tmp_path)
    sha = sha256_de(p)
    p.write_bytes(b"alguien edito el original despues de importarlo")
    assert estado_de(sha, p)[0] == ALTERADO


def test_una_ruta_que_es_un_directorio_no_es_un_original_intacto(tmp_path):
    assert estado_de("da39a3ee" * 8, tmp_path)[0] == AUSENTE


# --- Conservacion en el almacen por contenido --------------------------------------------------


def test_conservar_deja_una_copia_con_el_mismo_contenido(tmp_path):
    origen = _archivo(tmp_path / "entrada", "parte.xlsx")
    almacen = tmp_path / "almacen"
    destino = conservar_original(origen, almacen)
    assert destino.exists()
    assert sha256_de(destino) == sha256_de(origen)
    assert sha256_de(origen) in str(destino), "el almacen se direcciona por contenido"


def test_conservar_sobrevive_a_que_el_origen_desaparezca(tmp_path):
    """El punto de DT06: una copia propia no se rompe porque el archivo de origen se mueva."""
    origen = _archivo(tmp_path / "entrada", "parte.xlsx")
    almacen = tmp_path / "almacen"
    sha = sha256_de(origen)
    destino = conservar_original(origen, almacen)

    origen.unlink()
    assert estado_de(sha, origen)[0] == AUSENTE
    assert estado_de(sha, destino)[0] == INTACTO


def test_conservar_dos_veces_el_mismo_contenido_es_idempotente(tmp_path):
    origen = _archivo(tmp_path / "entrada", "parte.xlsx")
    almacen = tmp_path / "almacen"
    assert conservar_original(origen, almacen) == conservar_original(origen, almacen)


def test_conservar_no_sobrescribe_una_copia_corrupta(tmp_path):
    """Si el almacen ya tiene algo distinto bajo ese hash, sobrescribir borraria la evidencia de
    que el almacen esta corrupto."""
    origen = _archivo(tmp_path / "entrada", "parte.xlsx")
    almacen = tmp_path / "almacen"
    destino = conservar_original(origen, almacen)
    destino.chmod(0o600)
    destino.write_bytes(b"corrupto")

    with pytest.raises(ValueError, match="no coincide"):
        conservar_original(origen, almacen)


def test_conservar_copia_bytes_y_no_enlaza_al_original(tmp_path):
    """Un enlace duro compartiria inodo: editar el original en su sitio alteraria la copia, y los
    permisos de solo lectura de la copia se aplicarian tambien al archivo ajeno."""
    origen = _archivo(tmp_path / "entrada", "parte.xlsx")
    destino = conservar_original(origen, tmp_path / "almacen")
    assert destino.stat().st_ino != origen.stat().st_ino
    assert origen.stat().st_mode & 0o200, "el original no debe quedar en solo lectura"


# --- Contra la base de datos -------------------------------------------------------------------


def test_verificar_distingue_intacto_ausente_y_alterado(conn, tmp_path):
    intacto = _archivo(tmp_path, "intacto.xlsx", b"A")
    borrado = _archivo(tmp_path, "borrado.xlsx", b"B")
    editado = _archivo(tmp_path, "editado.xlsx", b"C")
    with conn.cursor() as cur:
        for p in (intacto, borrado, editado):
            registrar_documento(cur, p, "application/octet-stream")
    conn.commit()

    borrado.unlink()
    editado.write_bytes(b"C modificado despues de importar")

    resultado = verificar_originales(conn)
    por_ruta = {o.ruta_objeto: o.estado for o in resultado.objetos}
    assert por_ruta[str(intacto)] == INTACTO
    assert por_ruta[str(borrado)] == AUSENTE
    assert por_ruta[str(editado)] == ALTERADO
    assert resultado.por_estado() == {
        INTACTO: 1,
        CONSERVADO: 0,
        ALTERADO: 1,
        AUSENTE: 1,
    }
    assert len(resultado.problemas) == 2


def test_registrar_con_conservar_en_apunta_a_la_copia_no_al_origen(conn, tmp_path):
    origen = _archivo(tmp_path / "entrada", "nexus.xlsx")
    almacen = tmp_path / "almacen"
    with conn.cursor() as cur:
        registrar_documento(
            cur, origen, "application/octet-stream", conservar_en=almacen
        )
    conn.commit()

    ruta = conn.execute("SELECT ruta_objeto FROM objeto_archivo").fetchone()[
        "ruta_objeto"
    ]
    assert str(almacen) in ruta

    # Y por eso sobrevive a que el original de entrada desaparezca.
    origen.unlink()
    assert verificar_originales(conn).por_estado()[INTACTO] == 1


def test_registrar_sin_conservar_en_mantiene_el_comportamiento_anterior(conn, tmp_path):
    origen = _archivo(tmp_path, "nexus.xlsx")
    with conn.cursor() as cur:
        registrar_documento(cur, origen, "application/octet-stream")
    conn.commit()
    ruta = conn.execute("SELECT ruta_objeto FROM objeto_archivo").fetchone()[
        "ruta_objeto"
    ]
    assert ruta == str(origen)


def test_fecha_de_recepcion_real_no_se_sustituye_por_la_de_carga(conn, tmp_path):
    """docs/CLAUDE.md: una fecha de carga no es la fecha real de recepcion en mesa de partes."""
    recibido = datetime.now(LIMA) - timedelta(days=9)
    origen = _archivo(tmp_path, "parte.xlsx")
    with conn.cursor() as cur:
        registrar_documento(
            cur, origen, "application/octet-stream", recibido_en=recibido
        )
    conn.commit()

    guardado = conn.execute("SELECT recibido_en FROM documento_recibido").fetchone()[
        "recibido_en"
    ]
    assert abs((guardado - recibido).total_seconds()) < 1


def test_sin_fecha_explicita_se_usa_el_defecto_de_la_columna(conn, tmp_path):
    origen = _archivo(tmp_path, "parte.xlsx")
    with conn.cursor() as cur:
        registrar_documento(cur, origen, "application/octet-stream")
    conn.commit()
    guardado = conn.execute("SELECT recibido_en FROM documento_recibido").fetchone()[
        "recibido_en"
    ]
    assert (datetime.now(LIMA) - guardado).total_seconds() < 120


def test_el_almacen_recupera_un_original_cuya_ruta_ya_no_sirve(conn, tmp_path):
    """El caso real del piloto: la ruta registrada apuntaba a un temporal ya borrado. Con una copia
    conservada el contenido sigue siendo recuperable, y la fila conserva su procedencia original."""
    almacen = tmp_path / "almacen"
    origen = _archivo(tmp_path / "entrada", "parte.xlsx")
    with conn.cursor() as cur:
        registrar_documento(cur, origen, "application/octet-stream")
    conn.commit()

    conservar_original(origen, almacen)
    origen.unlink()

    sin_almacen = verificar_originales(conn)
    assert sin_almacen.por_estado()[AUSENTE] == 1

    con_almacen = verificar_originales(conn, almacen)
    objeto = con_almacen.objetos[0]
    assert objeto.estado == CONSERVADO
    assert objeto.recuperable
    assert objeto.ruta_objeto == str(origen), (
        "la procedencia registrada no se reescribe"
    )
    assert objeto.ruta_conservada and sha256_de(Path(objeto.ruta_conservada))


def test_objeto_archivo_es_inmutable_por_diseno(conn, tmp_path):
    """El trigger fn_objeto_archivo_freeze (migracion 0001) impide repuntar `ruta_objeto`. Por eso
    la recuperacion se hace buscando por hash, no reescribiendo la fila."""
    origen = _archivo(tmp_path, "parte.xlsx")
    with conn.cursor() as cur:
        registrar_documento(cur, origen, "application/octet-stream")
    conn.commit()

    with pytest.raises(psycopg.errors.RaiseException, match="inmutable"):
        conn.execute("UPDATE objeto_archivo SET ruta_objeto = %s", ("/otra/ruta",))
    conn.rollback()


def test_un_alterado_sin_copia_no_se_da_por_recuperable(conn, tmp_path):
    almacen = tmp_path / "almacen"
    origen = _archivo(tmp_path, "parte.xlsx")
    with conn.cursor() as cur:
        registrar_documento(cur, origen, "application/octet-stream")
    conn.commit()
    origen.write_bytes(b"editado despues de importar")

    objeto = verificar_originales(conn, almacen).objetos[0]
    assert objeto.estado == ALTERADO
    assert not objeto.recuperable
