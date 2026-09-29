"""Accesos ficticios por nombre sin perder las cuentas históricas por correo."""

from contextlib import nullcontext

import psycopg
import pytest
from click.testing import CliRunner
from werkzeug.security import check_password_hash

from asistia import cli
from asistia.web.auth import crear_usuario

from .test_web import app, post  # noqa: F401

# ruff: noqa: F811
CLAVE_FICTICIA = "Clave_Solo_Pruebas_2032"


def test_usuario_sin_correo_y_correo_historico_pueden_ingresar(app, conn):
    uid = crear_usuario(
        conn, "Jefatura ficticia", None, CLAVE_FICTICIA, usuario="RRHH_demo"
    )
    registro = conn.execute(
        "SELECT * FROM usuario JOIN web_credencial USING(usuario_id) WHERE usuario_id=%s",
        (uid,),
    ).fetchone()
    assert registro["email"] is None and registro["usuario_login"] == "rrhh_demo"
    assert registro["rol"] == "RRHH" and registro["activo"]
    assert registro["password_hash"] != CLAVE_FICTICIA
    assert check_password_hash(registro["password_hash"], CLAVE_FICTICIA)
    for identificador, clave, nombre in [
        (" RRHH_DEMO ", CLAVE_FICTICIA, "Jefatura ficticia"),
        ("OPERADOR@example.invalid", "UnaClaveDePrueba_2026", "Operador de prueba"),
    ]:
        client = app.test_client()
        html = client.get("/ingresar").text
        assert (
            '<label for="email">Usuario</label>' in html
            and 'type="text" id="email"' in html
        )
        assert "Tu mes de trabajo" not in html and "Acceso reservado" not in html
        assert "ugel-luya.png" in html and "UGEL LUYA | BREIT" in html
        r = post(client, "/ingresar", {"email": identificador, "password": clave})
        assert r.status_code == 303 and r.location == "/"
        assert nombre in client.get("/").text
        assert post(client, "/salir").status_code == 303
        assert client.get("/").status_code == 302


def test_usuario_no_se_infiere_del_prefijo_de_correo_y_no_reemplaza_cuentas(app, conn):
    uid = crear_usuario(
        conn, "Cuenta original", "rrhh_demo@example.invalid", CLAVE_FICTICIA
    )
    client = app.test_client()
    client.get("/ingresar")
    assert (
        post(
            client, "/ingresar", {"email": "rrhh_demo", "password": CLAVE_FICTICIA}
        ).status_code
        == 401
    )
    otro = crear_usuario(
        conn, "Otra cuenta", None, "Otra_Clave_Ficticia_2032", usuario="rrhh_demo"
    )
    assert otro != uid
    with pytest.raises(ValueError, match="No se reemplazó"):
        crear_usuario(conn, "Duplicado", None, CLAVE_FICTICIA, usuario="RRHH_DEMO")
    with pytest.raises(ValueError, match="No se reemplazó"):
        crear_usuario(conn, "Duplicado", "RRHH_DEMO@example.invalid", CLAVE_FICTICIA)
    assert conn.execute("SELECT count(*) n FROM usuario").fetchone()["n"] == 3
    assert (
        post(
            client, "/ingresar", {"email": "rrhh_demo", "password": CLAVE_FICTICIA}
        ).status_code
        == 401
    )
    assert (
        post(
            client,
            "/ingresar",
            {"email": "rrhh_demo@example.invalid", "password": CLAVE_FICTICIA},
        ).status_code
        == 303
    )


def test_usuario_conserva_bloqueo_inactivo_y_rol(app, conn):
    uid = crear_usuario(
        conn, "Cuenta ficticia", None, CLAVE_FICTICIA, usuario="rrhh_demo"
    )
    client = app.test_client()
    client.get("/ingresar")
    for _ in range(5):
        assert (
            post(
                client, "/ingresar", {"email": "rrhh_demo", "password": "incorrecta"}
            ).status_code
            == 401
        )
    assert (
        post(
            client, "/ingresar", {"email": "RRHH_DEMO", "password": CLAVE_FICTICIA}
        ).status_code
        == 401
    )
    estado = conn.execute(
        "SELECT fallos,bloqueado_hasta FROM web_credencial WHERE usuario_id=%s", (uid,)
    ).fetchone()
    assert estado["fallos"] == 5 and estado["bloqueado_hasta"] is not None
    conn.execute(
        "UPDATE web_credencial SET fallos=0,bloqueado_hasta=NULL WHERE usuario_id=%s",
        (uid,),
    )
    for activo, rol in [(False, "RRHH"), (True, "TECNICO")]:
        conn.execute(
            "UPDATE usuario SET activo=%s,rol=%s WHERE usuario_id=%s",
            (activo, rol, uid),
        )
        conn.commit()
        assert (
            post(
                client, "/ingresar", {"email": "rrhh_demo", "password": CLAVE_FICTICIA}
            ).status_code
            == 401
        )


@pytest.mark.parametrize(
    "usuario",
    [None, "", "ab", "con espacio", "otro@example.invalid", "x" * 65, "_inicio"],
)
def test_creacion_rechaza_usuario_invalido(conn, usuario):
    with pytest.raises(ValueError):
        crear_usuario(conn, "Ficticio", None, CLAVE_FICTICIA, usuario=usuario)
    assert conn.execute("SELECT count(*) n FROM usuario").fetchone()["n"] == 0


def test_restricciones_de_migracion_en_la_base(conn):
    uid = crear_usuario(conn, "Ficticio", None, CLAVE_FICTICIA, usuario="rrhh_demo")
    for sql, error in [
        (
            "INSERT INTO usuario(nombre) VALUES('Sin acceso')",
            psycopg.errors.CheckViolation,
        ),
        (
            "INSERT INTO usuario(nombre,usuario_login) VALUES('Duplicado','rrhh_demo')",
            psycopg.errors.UniqueViolation,
        ),
        (
            "INSERT INTO usuario(nombre,usuario_login) VALUES('Inválido','RRHH_DEMO')",
            psycopg.errors.CheckViolation,
        ),
    ]:
        with pytest.raises(error), conn.transaction():
            conn.execute(sql)
    assert (
        conn.execute(
            "SELECT usuario_login FROM usuario WHERE usuario_id=%s", (uid,)
        ).fetchone()["usuario_login"]
        == "rrhh_demo"
    )


def test_cli_admite_nombre_sin_correo_y_conserva_opcion_email(conn, monkeypatch):
    monkeypatch.setattr(cli, "conectar", lambda: nullcontext(conn))
    runner = CliRunner()
    for identificador in [
        ["--usuario", "rrhh_demo"],
        ["--email", "historico@example.invalid"],
    ]:
        r = runner.invoke(
            cli.crear_usuario_web,
            ["--nombre", "Ficticio", *identificador],
            input=f"{CLAVE_FICTICIA}\n{CLAVE_FICTICIA}\n",
        )
        assert r.exit_code == 0, r.output
        assert CLAVE_FICTICIA not in r.output
    assert conn.execute("SELECT count(*) n FROM web_credencial").fetchone()["n"] == 2
