"""Respaldar ASISTIA y probar una restauración aislada; nunca sustituye el piloto.

Ejecutar desde la raíz con --web-pid del único servidor en 8000. Conserva un
archivo privado de datos, configuración y código, y elimina solo el contenedor
nuevo que crea la propia ejecución. La comparación publica únicamente agregados.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def guardar(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def referencia_en_raiz(ruta):
    path = Path(ruta)
    return (path if path.is_absolute() else ROOT / path).resolve()


def huella(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def ejecutar(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def tablas(conn):
    """Contenido ordenado, independiente del orden físico de restauración."""
    result = {}
    conn.execute("SET TIME ZONE 'UTC'")
    nombres = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
    ).fetchall()
    for r in nombres:
        digest = hashlib.sha256()
        filas = 0
        query = sql.SQL(
            "COPY (SELECT row_to_json(t)::text FROM {} AS t "
            'ORDER BY row_to_json(t)::text COLLATE "C") TO STDOUT'
        ).format(sql.Identifier("public", r["tablename"]))
        with conn.cursor().copy(query) as copia:
            for chunk in copia:
                b = bytes(chunk)
                digest.update(b)
                filas += b.count(b"\n")
        result[r["tablename"]] = {"filas": filas, "sha256": digest.hexdigest()}
    secuencias = {}
    for r in conn.execute(
        "SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY 1"
    ).fetchall():
        secuencias[r["sequencename"]] = conn.execute(
            sql.SQL("SELECT last_value,is_called FROM {}").format(
                sql.Identifier("public", r["sequencename"])
            )
        ).fetchone()
    return {"tablas": result, "secuencias": secuencias}


def puerto_abierto(puerto):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", puerto)) == 0


def detener_web(pid):
    proc = Path(f"/proc/{pid}")
    cmd = proc.joinpath("cmdline").read_bytes().split(b"\0")
    assert proc.joinpath("cwd").resolve() == ROOT
    assert b"8000" in cmd and b"8001" not in cmd and b"web" in cmd
    assert any(c.endswith(b"/asistia") for c in cmd)
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if not puerto_abierto(8000):
            return
        time.sleep(0.1)
    raise RuntimeError("La instancia indicada no se detuvo.")


def iniciar_web():
    assert not puerto_abierto(8000), "Existe otra instancia en 8000."
    with (ROOT / "data/web/servidor_autonomia.log").open("ab") as log:
        proc = subprocess.Popen(
            [
                str(ROOT / ".venv/bin/asistia"),
                "web",
                "servir",
                "--host",
                "127.0.0.1",
                "--puerto",
                "8000",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(100):
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8000/ingresar", timeout=2
            ) as r:
                assert r.status == 200
                return proc.pid
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("La web operativa no respondió tras el reinicio.")


def respaldar_archivos(destino):
    """Incluye data y .env; excluye respaldos recursivos y entornos regenerables."""
    excluidos = {ROOT / "data/backups", ROOT / "data/recuperacion"}
    fuentes = [ROOT / ".env"]
    for base, dirs, files in os.walk(ROOT / "data", followlinks=False):
        carpeta = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d
            not in {
                ".venv",
                "__pycache__",
                "node_modules",
                ".pytest_cache",
                ".ruff_cache",
            }
            and carpeta / d not in excluidos
        )
        fuentes.append(carpeta)
        fuentes.extend(carpeta / f for f in sorted(files))
    manifiesto = {}
    with tarfile.open(destino / "archivos.tar", "w") as tar:
        for path in fuentes:
            assert not path.is_symlink(), (
                "El respaldo no admite dependencias por symlink."
            )
            info = tar.gettarinfo(str(path), arcname=str(path.relative_to(ROOT)))
            if path.is_dir():
                tar.addfile(info)
                continue
            antes = path.stat()
            digest = hashlib.sha256()

            class Lector:
                def __init__(self, archivo, hash_archivo):
                    self.archivo = archivo
                    self.hash_archivo = hash_archivo

                def read(self, size):
                    b = self.archivo.read(size)
                    self.hash_archivo.update(b)
                    return b

            with path.open("rb") as f:
                tar.addfile(info, Lector(f, digest))
            despues = path.stat()
            assert (antes.st_size, antes.st_mtime_ns) == (
                despues.st_size,
                despues.st_mtime_ns,
            ), "Un archivo cambió durante la copia."
            manifiesto[info.name] = {
                "sha256": digest.hexdigest(),
                "bytes": info.size,
                "modo": oct(info.mode),
            }
    guardar(destino / "archivos.json", manifiesto)
    return manifiesto


def comprobar_app(root_copia):
    """Subproceso que importa el código restaurado y solo conecta a la base efímera."""
    root_copia = root_copia.resolve()
    assert root_copia.is_relative_to(ROOT / "data/recuperacion")
    sys.path.insert(0, str(root_copia / "src"))
    os.chdir(root_copia)
    from asistia.cierre import conversion
    from asistia.cierre.gemini import configuracion
    from asistia.configuracion import RAIZ_PROYECTO, entorno_local
    from asistia.web import archivos, cierre, create_app, routes, salidas
    from asistia.web.auth import token_hash

    assert RAIZ_PROYECTO == root_copia
    dsn = entorno_local()["ASISTIA_DATABASE_URL"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert (
            conn.execute("SELECT current_database() AS nombre")
            .fetchone()["nombre"]
            .startswith("asistia_recuperacion_")
        )
        app = create_app({"TESTING": True})
        assert app.config["STORAGE_ROOT"].is_relative_to(root_copia)
        token = secrets.token_urlsafe(32)
        uid = conn.execute(
            "SELECT usuario_id FROM usuario WHERE activo AND rol='RRHH' ORDER BY usuario_id LIMIT 1"
        ).fetchone()["usuario_id"]
        conn.execute(
            "INSERT INTO web_sesion(token_hash,usuario_id,vence_en) VALUES(%s,%s,%s)",
            (token_hash(token), uid, datetime.now(UTC) + timedelta(minutes=10)),
        )
        conn.commit()
        originales_abiertos = []
        verificada = archivos.ruta_verificada

        def ruta_copia(ruta, sha):
            path = referencia_en_raiz(ruta)
            assert path.is_relative_to(ROOT), "Referencia histórica no recuperable."
            copia = root_copia / path.relative_to(ROOT)
            resultado = verificada(str(copia), sha)
            originales_abiertos.append(str(resultado.relative_to(root_copia)))
            return resultado

        salidas_actuales = json.loads(
            (root_copia / "data/cierre/salidas_2026-07.json").read_text()
        )
        actual_ml = json.loads(
            (root_copia / "data/experimental/cierre_20260915/actual.json").read_text()
        )
        lecturas = 0
        descargas = 0
        try:
            with ExitStack() as stack:
                for modulo in [archivos, cierre, routes, salidas]:
                    stack.enter_context(
                        patch.object(modulo, "ruta_verificada", ruta_copia)
                    )
                cliente = app.test_client()
                with cliente.session_transaction() as sesion:
                    sesion.update(token=token, csrf=secrets.token_urlsafe(32))
                for ruta in [
                    "/salud",
                    "/ingresar",
                    "/cierre",
                    "/instituciones",
                    "/experimental",
                    f"/experimental/lotes/{actual_ml['lote_id']}",
                ]:
                    r = cliente.get(ruta)
                    assert r.status_code == 200, (ruta, r.status_code)
                    lecturas += 1
                for salida in salidas_actuales:
                    r = cliente.get(f"/salidas/{salida['web_salida_id']}/descargar")
                    assert r.status_code == 200
                    assert hashlib.sha256(r.data).hexdigest() == salida["sha256"]
                    lecturas += 1
                    descargas += 1
                r = cliente.get(
                    f"/experimental/experimentos/{actual_ml['experimento_id']}/paquete.zip"
                )
                assert r.status_code == 200
                assert hashlib.sha256(r.data).hexdigest() == actual_ml["sha256"]
                lecturas += 1
                descargas += 1
        finally:
            conn.execute(
                "DELETE FROM web_sesion WHERE token_hash=%s", (token_hash(token),)
            )
            conn.commit()
        referencia = json.loads(
            (
                root_copia / "docs/evidencias/autonomia-2026-09-15/conversion.json"
            ).read_text()
        )
        fuente_sha = referencia["fuente_sha256"]
        original = conn.execute(
            "SELECT ruta_objeto FROM objeto_archivo WHERE sha256=%s", (fuente_sha,)
        ).fetchone()["ruta_objeto"]
        conversion.SALIDA = (
            root_copia / "data" / f"comprobacion_conversion_{secrets.token_hex(6)}"
        )
        assert not conversion.SALIDA.exists()
        pdf, _ = conversion.representar_docx(
            root_copia / referencia_en_raiz(original).relative_to(ROOT)
        )
        nuevo = PdfReader(pdf)
        previo = PdfReader(
            root_copia / f"data/cierre/representaciones/{fuente_sha}/documento.pdf"
        )
        assert len(nuevo.pages) == len(previo.pages) == referencia["paginas"]
        assert [p.extract_text() for p in nuevo.pages] == [
            p.extract_text() for p in previo.pages
        ]
        assert [tuple(p.mediabox) for p in nuevo.pages] == [
            tuple(p.mediabox) for p in previo.pages
        ]
        claves, _ = configuracion()
        assert len(claves) == 5
    resultado = {
        "codigo_restaurado": True,
        "lecturas_http": lecturas,
        "descargas_sha256": descargas,
        "excel_desde_almacen_restaurado": len(originales_abiertos),
        "sesion_temporal_eliminada": True,
        "claves_locales": len(claves),
        "conversion_nueva_paginas": referencia["paginas"],
        "conversion_texto_dimensiones_iguales": True,
        "adaptacion_prueba": "Rutas históricas absolutas traducidas al directorio de copia solo en el proceso de prueba; sin alterar referencias en tablas.",
    }
    guardar(root_copia.parent / "aplicacion.json", resultado)
    print(json.dumps(resultado, ensure_ascii=False), flush=True)


def main(web_pid=None, respaldo_existente=None):
    from asistia.db import dsn

    os.umask(0o077)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if respaldo_existente:
        respaldo = respaldo_existente.resolve()
        assert respaldo.parent == ROOT / "data/backups"
        stamp = respaldo.name.removeprefix("recuperacion-")
        datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        assert respaldo.is_dir()
        with (respaldo / "codigo.tar").open("rb") as f:
            commit = subprocess.check_output(
                ["git", "get-tar-commit-id"], stdin=f, text=True
            ).strip()
    else:
        respaldo = ROOT / "data/backups" / f"recuperacion-{stamp}"
        respaldo.mkdir(mode=0o700)
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    recuperado = ROOT / "data/recuperacion" / stamp
    recuperado.mkdir(mode=0o700, exist_ok=bool(respaldo_existente))
    copia = recuperado / "proyecto"
    copia.mkdir(mode=0o700, exist_ok=bool(respaldo_existente))
    ejecutar(
        [
            "git",
            "diff",
            "--exit-code",
            commit,
            "--",
            "src",
            "migrations",
            "pyproject.toml",
            "uv.lock",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
    )
    inspect = json.loads(
        subprocess.check_output(["docker", "inspect", "asistia-db-1"], text=True)
    )[0]
    imagen = inspect["Image"]
    network = next(iter(inspect["NetworkSettings"]["Networks"]))
    resultado = {
        "inicio_utc": datetime.now(UTC).isoformat(),
        "commit_codigo": commit,
        "respaldo": str(respaldo.relative_to(ROOT)),
        "copia_prueba": str(recuperado.relative_to(ROOT)),
        "imagen_postgresql": imagen,
        "volumen_operativo": [
            m["Name"] for m in inspect["Mounts"] if m["Type"] == "volume"
        ],
        "exclusiones_archivos": [
            "data/backups",
            "data/recuperacion",
            ".venv",
            "__pycache__",
            "node_modules",
            ".pytest_cache",
            ".ruff_cache",
        ],
    }
    if respaldo_existente:
        antes = json.loads((respaldo / "base_referencia.json").read_text())
        archivos = json.loads((respaldo / "archivos.json").read_text())
        resultado["reintento_sin_nueva_captura"] = True
        resultado["captura_utc"] = (
            datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
        )
        print(
            "Comprobando el respaldo existente; web operativa sin interrupción.",
            flush=True,
        )
    else:
        print("Capturando código, base y archivos; pausa breve de la web.", flush=True)
        detener_web(web_pid)
        try:
            with psycopg.connect(dsn(), row_factory=dict_row) as conn:
                conn.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                assert (
                    conn.execute("SELECT current_database() AS nombre").fetchone()[
                        "nombre"
                    ]
                    == "asistia"
                )
                assert conn.execute(
                    "SELECT pg_try_advisory_lock(26091206) AS ok"
                ).fetchone()["ok"], "Existe un lote escribiendo."
                snapshot = conn.execute("SELECT pg_export_snapshot() AS id").fetchone()[
                    "id"
                ]
                antes = tablas(conn)
                guardar(respaldo / "base_referencia.json", antes)
                with (respaldo / "base.dump").open("wb") as f:
                    ejecutar(
                        [
                            "docker",
                            "exec",
                            "asistia-db-1",
                            "pg_dump",
                            "-U",
                            "asistia",
                            "-d",
                            "asistia",
                            "-Fc",
                            f"--snapshot={snapshot}",
                        ],
                        stdout=f,
                    )
                ejecutar(
                    [
                        "git",
                        "archive",
                        "--format=tar",
                        f"--output={respaldo / 'codigo.tar'}",
                        commit,
                    ],
                    cwd=ROOT,
                )
                archivos = respaldar_archivos(respaldo)
        finally:
            resultado["web_pid"] = iniciar_web()
        print(
            f"Respaldo capturado: {len(archivos)} archivos. Web nuevamente en 8000.",
            flush=True,
        )
    for nombre in ["codigo.tar", "archivos.tar"]:
        with tarfile.open(respaldo / nombre) as tar:
            tar.extractall(copia, filter="data")
    for nombre, info in archivos.items():
        p = copia / nombre
        assert p.stat().st_size == info["bytes"] and huella(p) == info["sha256"], (
            "La copia de archivos no coincide."
        )
    assert (copia / ".env").stat().st_mode & 0o777 == 0o600
    resultado["archivos"] = {
        "verificados": len(archivos),
        "bytes": sum(v["bytes"] for v in archivos.values()),
        "configuracion_privada_incluida": True,
    }
    contenedor = None
    try:
        nombre = f"asistia-recuperacion-{stamp.lower()}"
        dbname = f"asistia_recuperacion_{stamp.lower()}"
        password = secrets.token_urlsafe(32)
        entorno = {**os.environ, "POSTGRES_PASSWORD": password}
        contenedor = subprocess.check_output(
            [
                "docker",
                "run",
                "-d",
                "--name",
                nombre,
                "--label",
                "asistia.recuperacion=efimera",
                "--network",
                network,
                "--tmpfs",
                "/var/lib/postgresql/data",
                "-e",
                "POSTGRES_USER=asistia",
                "-e",
                f"POSTGRES_DB={dbname}",
                "-e",
                "POSTGRES_PASSWORD",
                imagen,
            ],
            env=entorno,
            text=True,
        ).strip()
        ip = json.loads(
            subprocess.check_output(["docker", "inspect", contenedor], text=True)
        )[0]["NetworkSettings"]["Networks"][network]["IPAddress"]
        destino = f"postgresql://asistia:{password}@{ip}:5432/{dbname}"
        for _ in range(100):
            try:
                with psycopg.connect(destino, connect_timeout=1):
                    break
            except psycopg.OperationalError:
                time.sleep(0.2)
        else:
            raise RuntimeError("El PostgreSQL aislado no inició.")
        with (respaldo / "base.dump").open("rb") as f:
            ejecutar(
                [
                    "docker",
                    "exec",
                    "-i",
                    contenedor,
                    "pg_restore",
                    "-U",
                    "asistia",
                    "-d",
                    dbname,
                    "--exit-on-error",
                    "--single-transaction",
                ],
                stdin=f,
            )
        print(
            "Base restaurada. Comparando tablas, secuencias y originales.", flush=True
        )
        with psycopg.connect(destino, row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            despues = tablas(conn)
            assert antes == despues, "La base restaurada no coincide."
            guardar(recuperado / "base_restaurada.json", despues)
            objetos = conn.execute(
                "SELECT ruta_objeto,sha256 FROM objeto_archivo"
            ).fetchall()
            ausentes = []
            verificados = 0
            for obj in objetos:
                p = referencia_en_raiz(obj["ruta_objeto"])
                if p.is_relative_to(ROOT):
                    q = copia / p.relative_to(ROOT)
                    assert q.is_file() and huella(q) == obj["sha256"].strip()
                    verificados += 1
                else:
                    assert not p.exists(), "Dependencia activa fuera del proyecto."
                    ausentes.append(
                        {
                            "sha256": obj["sha256"].strip(),
                            "motivo": "Referencia histórica ausente antes del respaldo.",
                        }
                    )
            assert len(ausentes) == 1
            rutas = conn.execute(
                "SELECT ruta AS valor FROM cierre_documento UNION ALL SELECT jsonb_array_elements_text(rutas) FROM cierre_documento UNION ALL SELECT ruta_copia FROM web_carga"
            ).fetchall()
            for r in rutas:
                if r["valor"]:
                    p = referencia_en_raiz(r["valor"])
                    assert (
                        p.is_relative_to(ROOT)
                        and (copia / p.relative_to(ROOT)).is_file()
                    )
            resultado["objetos"] = {
                "registrados": len(objetos),
                "recuperados_integridad_sha256": verificados,
                "ausentes_historicos": len(ausentes),
                "rutas_operativas_incluidas": len(rutas),
            }
            guardar(respaldo / "excepciones.json", ausentes)
        ejecutar(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--comprobar-app",
                str(copia),
            ],
            cwd=ROOT,
            env={**os.environ, "ASISTIA_DATABASE_URL": destino},
        )
        resultado["aplicacion"] = json.loads(
            (recuperado / "aplicacion.json").read_text()
        )
        with psycopg.connect(destino, row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            assert tablas(conn) == antes, (
                "La lectura dejó cambios en la base restaurada."
            )
        resultado["base"] = {
            "tablas_identicas": len(antes["tablas"]),
            "secuencias_identicas": len(antes["secuencias"]),
            "filas": sum(t["filas"] for t in antes["tablas"].values()),
            "migraciones": antes["tablas"]["_migraciones_aplicadas"]["filas"],
            "lecturas_sin_cambios_persistentes": True,
        }
    finally:
        if contenedor:
            ejecutar(["docker", "rm", "-f", contenedor], stdout=subprocess.DEVNULL)
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        assert tablas(conn) == antes, (
            "La base operativa cambió durante la comprobación; revisar concurrencia."
        )
    assert puerto_abierto(8000) and not puerto_abierto(8001)
    with urllib.request.urlopen("http://127.0.0.1:8000/ingresar", timeout=3) as r:
        assert r.status == 200
    resultado.update(
        base_operativa_intacta=True,
        web_8000_http=200,
        web_8001_cerrado=True,
        contenedor_prueba_eliminado=True,
        fin_utc=datetime.now(UTC).isoformat(),
    )
    resultado["respaldos"] = {
        p.name: {"sha256": huella(p), "bytes": p.stat().st_size}
        for p in respaldo.iterdir()
        if p.is_file()
    }
    guardar(respaldo / "verificacion.json", resultado)
    evidencia = ROOT / "docs/evidencias/recuperacion-2026-09-15"
    evidencia.mkdir(exist_ok=True)
    guardar(evidencia / "verificacion.json", resultado)
    print(json.dumps(resultado, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--web-pid", type=int)
    grupo.add_argument(
        "--respaldo",
        type=Path,
        help="Repetir la comprobación sobre una captura existente, sin pausar la web.",
    )
    grupo.add_argument("--comprobar-app", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.comprobar_app:
        comprobar_app(args.comprobar_app)
    else:
        main(args.web_pid, args.respaldo)
