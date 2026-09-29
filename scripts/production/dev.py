"""Acceso explícito desde la laptop a la base operativa remota.

`tunnel` abre el puerto 5546 solo en localhost mediante AWS SSM.
`sql` abre psql con rol de lectura; `sql --write` usa el rol operativo sin DDL.
Las pruebas conservan su propia base asistia_test: nunca se apuntan a producción.
"""

import argparse
import json
import os
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["tunnel", "sql", "status"])
    parser.add_argument(
        "--write",
        action="store_true",
        help="SQL operativo en producción; no otorga DDL ni TRUNCATE",
    )
    args = parser.parse_args()
    config = json.loads((ROOT / "data/production/active.json").read_text())
    if args.action == "status":
        print(
            json.dumps(
                {
                    k: config[k]
                    for k in ("url", "instance_id", "region", "activated_at")
                },
                indent=2,
            )
        )
        return
    if args.action == "tunnel":
        plugin = ROOT / "data/production/tools/usr/local/sessionmanagerplugin/bin"
        env = {**os.environ, "PATH": f"{plugin}:{os.environ['PATH']}"}
        command = [
            "aws",
            "--profile",
            "personal",
            "--region",
            config["region"],
            "ssm",
            "start-session",
            "--target",
            config["instance_id"],
            "--document-name",
            "AWS-StartPortForwardingSession",
            "--parameters",
            json.dumps({"portNumber": ["5432"], "localPortNumber": ["5546"]}),
        ]
        os.execvpe(command[0], command, env)
    role = "app" if args.write else "read"
    values = (ROOT / f"data/production/development-{role}.env").read_text().splitlines()
    url = next(
        line.split("=", 1)[1]
        for line in values
        if line.startswith("ASISTIA_DATABASE_URL=")
    )
    parsed = urlparse(url)
    env = {
        **os.environ,
        "PGHOST": "127.0.0.1",
        "PGPORT": "5546",
        "PGDATABASE": "asistia",
        "PGUSER": unquote(parsed.username),
        "PGPASSWORD": unquote(parsed.password),
        "PGCONNECT_TIMEOUT": "5",
    }
    if not shutil.which("psql"):
        raise SystemExit(
            "Instala el cliente psql; el túnel y las credenciales ya están preparados."
        )
    print(
        f"PRODUCCIÓN · {'ESCRITURA OPERATIVA' if args.write else 'SOLO LECTURA'} · {config['url']}",
        flush=True,
    )
    os.execvpe("psql", ["psql", "-X", "-v", "ON_ERROR_STOP=1"], env)


if __name__ == "__main__":
    main()
