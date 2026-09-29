"""Restauración inicial en EC2 (root); no sobrescribe una base ya inicializada.

Uso: python3 install.py BUCKET CAPTURE_PREFIX PUBLIC_IP. El material privado se
recupera con el rol IAM; ningún secreto entra en user-data, comandos SSM o stdout.
"""

import hashlib
import json
import os
import secrets
import shlex
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path("/data/projects/asistia")
PRIVATE = Path("/var/lib/asistia")
PG_IMAGE = "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def save(path, text, mode=0o600):
    path.write_text(text)
    path.chmod(mode)


def main(bucket, prefix, ip):
    os.umask(0o077)
    if (PRIVATE / "initialized").exists() or (PRIVATE / "postgres/PG_VERSION").exists():
        raise RuntimeError(
            "Base inicializada: usar actualización de código, no restauración inicial."
        )
    if not (PRIVATE / "bootstrap-ready").exists():
        raise RuntimeError("El arranque del servidor aún no terminó.")
    stage = PRIVATE / "staging"
    stage.mkdir(exist_ok=True)
    run(
        "aws",
        "s3",
        "sync",
        f"s3://{bucket}/{prefix}/",
        str(stage),
        "--only-show-errors",
    )
    manifest = json.loads((stage / "capture.json").read_text())
    for name, meta in manifest["artifacts"].items():
        with (stage / name).open("rb") as f:
            assert hashlib.file_digest(f, "sha256").hexdigest() == meta["sha256"], name
    for name in ("code.tar.gz", "data.tar.gz"):
        with tarfile.open(stage / name, "r:gz") as archive:
            archive.extractall(ROOT, filter="data")
    for item in json.loads((stage / "files.json").read_text()):
        with (ROOT / item["path"]).open("rb") as f:
            assert hashlib.file_digest(f, "sha256").hexdigest() == item["sha256"], (
                "Archivo distinto"
            )
    print("Archivos transferidos y verificados", flush=True)
    db = {k: secrets.token_urlsafe(36) for k in ("owner", "app", "read")}
    save(PRIVATE / "database.json", json.dumps(db))
    save(
        PRIVATE / "postgres.env",
        f"POSTGRES_USER=asistia_owner\nPOSTGRES_PASSWORD={db['owner']}\nPOSTGRES_DB=asistia\n",
    )
    (PRIVATE / "postgres").mkdir(mode=0o700)
    run(
        "docker",
        "run",
        "-d",
        "--name",
        "asistia-postgres",
        "--restart",
        "unless-stopped",
        "--publish",
        "127.0.0.1:5432:5432",
        "--env-file",
        str(PRIVATE / "postgres.env"),
        "--mount",
        "type=bind,source=/var/lib/asistia/postgres,target=/var/lib/postgresql/data",
        PG_IMAGE,
        stdout=subprocess.DEVNULL,
    )
    for attempt in range(60):
        ready = subprocess.run(
            [
                "docker",
                "exec",
                "asistia-postgres",
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                "asistia_owner",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("PostgreSQL no inició")
    with (stage / "database.dump").open("rb") as source:
        run(
            "docker",
            "exec",
            "-i",
            "asistia-postgres",
            "pg_restore",
            "-U",
            "asistia_owner",
            "-d",
            "asistia",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            stdin=source,
        )
    sql = f"""
CREATE ROLE asistia_app LOGIN PASSWORD '{db["app"]}';
CREATE ROLE asistia_read LOGIN PASSWORD '{db["read"]}';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE asistia TO asistia_app,asistia_read;
GRANT USAGE ON SCHEMA public TO asistia_app,asistia_read;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO asistia_app;
GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA public TO asistia_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO asistia_read;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO asistia_read;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO asistia_app;
ALTER DEFAULT PRIVILEGES FOR ROLE asistia_owner IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO asistia_app;
ALTER DEFAULT PRIVILEGES FOR ROLE asistia_owner IN SCHEMA public GRANT USAGE,SELECT,UPDATE ON SEQUENCES TO asistia_app;
ALTER DEFAULT PRIVILEGES FOR ROLE asistia_owner IN SCHEMA public GRANT SELECT ON TABLES TO asistia_read;
ALTER DEFAULT PRIVILEGES FOR ROLE asistia_owner IN SCHEMA public GRANT SELECT ON SEQUENCES TO asistia_read;
ALTER DEFAULT PRIVILEGES FOR ROLE asistia_owner IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE asistia_owner IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO asistia_app;
ALTER ROLE asistia_read SET default_transaction_read_only=on;
ALTER ROLE asistia_read SET statement_timeout='30s';
"""
    run(
        "docker",
        "exec",
        "-i",
        "asistia-postgres",
        "psql",
        "-X",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "asistia_owner",
        "-d",
        "asistia",
        input=sql,
        text=True,
        stdout=subprocess.DEVNULL,
    )
    sys.path.insert(0, str(ROOT / "src"))
    from asistia.configuracion import leer_env

    env = leer_env()
    env.update(
        {
            "ASISTIA_DATABASE_URL": f"postgresql://asistia_app:{db['app']}@127.0.0.1:5432/asistia",
            "ASISTIA_WEB_HTTPS": "1",
            "ASISTIA_WEB_HOSTS": f"{ip},127.0.0.1,localhost",
            "ASISTIA_WEB_SECRET": secrets.token_urlsafe(48),
            "ASISTIA_ENVIRONMENT": "production",
        }
    )
    save(ROOT / ".env", "".join(f"{k}={shlex.quote(v)}\n" for k, v in env.items()))
    for role, key in (("read", "read"), ("app", "app")):
        save(
            PRIVATE / f"development-{role}.env",
            f"ASISTIA_DATABASE_URL=postgresql://asistia_{role}:{db[key]}@127.0.0.1:5546/asistia\nASISTIA_ENVIRONMENT=production-remote\n",
        )
        run(
            "aws",
            "s3",
            "cp",
            str(PRIVATE / f"development-{role}.env"),
            f"s3://{bucket}/backups/access/development-{role}.env",
            "--only-show-errors",
        )
    run("chown", "-R", "asistia:asistia", str(ROOT))
    run(
        "runuser",
        "-u",
        "asistia",
        "--",
        "/opt/asistia-tools/bin/uv",
        "sync",
        "--frozen",
        "--no-dev",
        "--python",
        "python3.12",
        cwd=ROOT,
    )
    save(
        PRIVATE / "deployment.json",
        json.dumps(
            {
                "bucket": bucket,
                "ip": ip,
                "capture": prefix,
                "release_sha256": manifest["artifacts"]["code.tar.gz"]["sha256"],
            },
            indent=2,
        ),
    )
    finalize()


def finalize():
    """Reanudar solo comprobación y servicios tras una restauración completa."""
    os.umask(0o077)
    config = json.loads((PRIVATE / "deployment.json").read_text())
    manifest = json.loads((PRIVATE / "staging/capture.json").read_text())
    ip = config["ip"]
    if (PRIVATE / "initialized").exists():
        raise RuntimeError("Ya inicializado: usar actualización de código.")
    run(
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/production/verify.py"),
        cwd=ROOT,
    )
    save(
        Path("/etc/systemd/system/asistia.service"),
        """[Unit]
Description=ASISTIA web RRHH
After=network-online.target docker.service
Requires=docker.service
[Service]
Type=simple
User=asistia
Group=asistia
WorkingDirectory=/data/projects/asistia
ExecStart=/data/projects/asistia/.venv/bin/asistia web servir --host 127.0.0.1 --puerto 8000
Restart=on-failure
RestartSec=5
TimeoutStopSec=330
UMask=0077
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/data/projects/asistia/data
[Install]
WantedBy=multi-user.target
""",
        0o644,
    )
    run(
        "/opt/asistia-tools/bin/certbot",
        "certonly",
        "--non-interactive",
        "--agree-tos",
        "--register-unsafely-without-email",
        "--preferred-profile",
        "shortlived",
        "--webroot",
        "--webroot-path",
        "/var/www/acme",
        "--ip-address",
        ip,
        "--cert-name",
        "asistia",
        "--deploy-hook",
        "systemctl reload nginx",
    )
    nginx = f"""server {{
    listen 80 default_server;
    server_name {ip};
    location /.well-known/acme-challenge/ {{ root /var/www/acme; }}
    location / {{ return 301 https://{ip}$request_uri; }}
}}
server {{
    listen 443 ssl default_server;
    server_name {ip};
    ssl_certificate /etc/letsencrypt/live/asistia/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/asistia/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 32m;
    access_log off;
    location / {{
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 360s;
    }}
}}
"""
    save(Path("/etc/nginx/sites-available/asistia"), nginx, 0o644)
    save(
        Path("/etc/systemd/system/asistia-certificates.service"),
        """[Unit]
Description=Renovar HTTPS ASISTIA
[Service]
Type=oneshot
ExecStart=/opt/asistia-tools/bin/certbot renew --quiet --deploy-hook "systemctl reload nginx"
""",
        0o644,
    )
    save(
        Path("/etc/systemd/system/asistia-certificates.timer"),
        """[Unit]
Description=Comprobar certificado cada 12 horas
[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=600
Persistent=true
[Install]
WantedBy=timers.target
""",
        0o644,
    )
    save(
        Path("/etc/systemd/system/asistia-backup.service"),
        """[Unit]
Description=Respaldar ASISTIA en S3 privado
After=docker.service network-online.target
[Service]
Type=oneshot
WorkingDirectory=/data/projects/asistia
ExecStart=/data/projects/asistia/.venv/bin/python scripts/production/backup.py
UMask=0077
""",
        0o644,
    )
    save(
        Path("/etc/systemd/system/asistia-backup.timer"),
        """[Unit]
Description=Respaldo diario ASISTIA
[Timer]
OnCalendar=*-*-* 08:00:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
""",
        0o644,
    )
    run("systemctl", "daemon-reload")
    run("nginx", "-t")
    run(
        "systemctl",
        "enable",
        "--now",
        "asistia",
        "asistia-certificates.timer",
        "asistia-backup.timer",
    )
    run("systemctl", "reload", "nginx")
    save(PRIVATE / "initialized", manifest["captured_at"] + "\n")
    print(
        "Instalación y HTTPS listos; completar comprobación externa y backup/restauración",
        flush=True,
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--resume"]:
        finalize()
    else:
        main(*sys.argv[1:])
