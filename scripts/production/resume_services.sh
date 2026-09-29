#!/usr/bin/env bash
# Ejecutar como root por SSM después de StartInstances autorizado.
# No crea ni restaura bases; reactiva la instalación y comprueba HTTPS/SQL.
set -euo pipefail
cd /data/projects/asistia
systemctl start docker nginx
docker start asistia-postgres >/dev/null
for ((attempt = 0; attempt < 60; attempt++)); do
    if docker exec asistia-postgres pg_isready -h 127.0.0.1 -U asistia_owner >/dev/null; then
        break
    fi
    sleep 2
done
docker exec asistia-postgres pg_isready -h 127.0.0.1 -U asistia_owner >/dev/null
# Tras una pausa larga el certificado puede haber vencido. systemd espera el
# resultado de Certbot, también si el timer ya había iniciado el mismo servicio.
systemctl start asistia-certificates.service
systemctl start asistia asistia-backup.timer asistia-certificates.timer
.venv/bin/python - <<'PY'
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg

from asistia.configuracion import entorno_local

config = json.loads(Path('/var/lib/asistia/deployment.json').read_text())
url = f"https://{config['ip']}"
for attempt in range(60):
    try:
        for path in ('/salud', '/ingresar'):
            with urllib.request.urlopen(url + path, timeout=5) as response:
                assert response.status == 200
        break
    except (OSError, urllib.error.URLError):
        if attempt == 59:
            raise
        time.sleep(2)
with psycopg.connect(entorno_local()['ASISTIA_DATABASE_URL'], connect_timeout=5) as conn:
    conn.execute('SET TRANSACTION READ ONLY')
    database, role = conn.execute('SELECT current_database(),current_user').fetchone()
    assert (database, role) == ('asistia', 'asistia_app')
print(json.dumps({'url': url, 'https_health_and_login': 200, 'database_connected': True}))
PY
