"""Cotejo en EC2 antes de habilitar operación: tablas, secuencias y lecturas web.

La sesión técnica temporal no registra aprobación RRHH. Sin llamadas a Gemini.
"""

import argparse
import hashlib
import importlib.util
import json
import secrets
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from asistia.configuracion import entorno_local
from asistia.web import create_app
from asistia.web.auth import token_hash

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path("/var/lib/asistia")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true", help="Comprobar también Nginx y TLS reales"
    )
    args = parser.parse_args()
    db = json.loads((PRIVATE / "database.json").read_text())
    reference = json.loads((PRIVATE / "staging/database.json").read_text())
    config = json.loads((PRIVATE / "deployment.json").read_text())
    spec = importlib.util.spec_from_file_location(
        "recuperacion", ROOT / "scripts/recuperacion.py"
    )
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    owner_dsn = f"postgresql://asistia_owner:{db['owner']}@127.0.0.1:5432/asistia"
    with psycopg.connect(owner_dsn, row_factory=dict_row) as conn:
        before = recovery.tablas(conn)
        assert before == reference, "Tablas/secuencias distintas de la captura"
        conn.rollback()
        uid = conn.execute(
            "SELECT usuario_id FROM usuario WHERE activo AND rol='RRHH' ORDER BY usuario_id LIMIT 1"
        ).fetchone()["usuario_id"]
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO web_sesion(token_hash,usuario_id,vence_en) VALUES(%s,%s,%s)",
            (token_hash(token), uid, datetime.now(UTC) + timedelta(minutes=15)),
        )
        conn.commit()
        app = create_app({"TESTING": True})
        base_url = f"https://{config['ip']}"
        reads = []
        live_reads = 0
        try:
            client = app.test_client()
            response = client.get("/cierre", base_url=base_url)
            assert response.status_code == 302
            with client.session_transaction(base_url=base_url) as session:
                session.update(token=token, csrf=secrets.token_urlsafe(32))
            for path in (
                "/salud",
                "/cierre",
                "/instituciones",
                "/calendarios",
                "/categorias",
                "/monitoreo",
                "/monitoreo/diario",
                "/monitoreo/leyendas",
            ):
                result = client.get(path, base_url=base_url, follow_redirects=True)
                assert result.status_code == 200, (path, result.status_code)
                assert result.request.path != "/ingresar", path
                reads.append(path)
            exports = json.loads(
                (ROOT / "data/cierre/salidas_2026-07.json").read_text()
            )
            for entry in exports:
                path = f"/salidas/{entry['web_salida_id']}/descargar"
                result = client.get(path, base_url=base_url)
                assert result.status_code == 200, path
                assert hashlib.sha256(result.data).hexdigest() == entry["sha256"]
                reads.append(path)
            if args.live:
                cookie = app.session_interface.get_signing_serializer(app).dumps(
                    {"token": token, "csrf": secrets.token_urlsafe(32)}
                )
                hashes = {
                    f"/salidas/{entry['web_salida_id']}/descargar": entry["sha256"]
                    for entry in exports
                }
                for path in reads:
                    request = urllib.request.Request(
                        base_url + path,
                        headers={"Cookie": f"asistia_sesion={cookie}"},
                    )
                    with urllib.request.urlopen(request, timeout=60) as response:
                        assert response.status == 200, path
                        assert "/ingresar" not in response.geturl(), path
                        content = response.read()
                        if path in hashes:
                            assert hashlib.sha256(content).hexdigest() == hashes[path]
                    live_reads += 1
        finally:
            conn.execute(
                "DELETE FROM web_sesion WHERE token_hash=%s", (token_hash(token),)
            )
            conn.commit()
        assert recovery.tablas(conn) == reference, "Las lecturas modificaron datos"
    read_dsn = f"postgresql://asistia_read:{db['read']}@127.0.0.1:5432/asistia"
    with psycopg.connect(read_dsn) as conn:
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        assert (
            conn.execute("SELECT count(*) FROM institucion_educativa").fetchone()[0]
            == reference["tablas"]["institucion_educativa"]["filas"]
        )
        assert not conn.execute(
            "SELECT has_table_privilege(current_user,'institucion_educativa','UPDATE')"
        ).fetchone()[0]
    with psycopg.connect(entorno_local()["ASISTIA_DATABASE_URL"]) as conn:
        assert not conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege(current_user,'institucion_educativa','TRUNCATE')"
        ).fetchone()[0]
    result = {
        "verified_at": datetime.now(UTC).isoformat(),
        "tables": len(reference["tablas"]),
        "sequences": len(reference["secuencias"]),
        "database_identical": True,
        "http_reads": len(reads),
        "live_https_reads": live_reads,
        "export_hashes": len(exports),
        "unauthenticated_denied": True,
        "read_role_cannot_update": True,
        "app_not_superuser": True,
        "temporary_session_removed": True,
        "gemini_requests": 0,
    }
    (PRIVATE / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
