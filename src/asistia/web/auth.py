"""Un operador identificado; contraseñas derivadas y sesiones revocables en PostgreSQL."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from flask import Blueprint, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import base

auth = Blueprint("auth", __name__)
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(24))


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def crear_usuario(conn, nombre: str, email: str | None, password: str, *, usuario=None):
    email = email.strip().lower() if email else None
    usuario = usuario.strip().lower() if usuario else None
    if (
        not nombre.strip()
        or len(nombre.strip()) > 150
        or not (email or usuario)
        or (email is not None and ("@" not in email or len(email) > 150))
        or (
            usuario is not None
            and not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", usuario)
        )
        or len(password) < 12
    ):
        raise ValueError(
            "Se requieren nombre, usuario válido o correo, y contraseña de al menos 12 caracteres. "
            "El usuario admite entre 3 y 64 letras, números, puntos, guiones o guiones bajos."
        )
    existente = conn.execute(
        "SELECT 1 FROM usuario WHERE lower(email)=%s OR usuario_login=%s",
        (email, usuario),
    ).fetchone()
    if existente:
        raise ValueError("Ese usuario ya existe. No se reemplazó su contraseña.")
    usuario_id = conn.execute(
        "INSERT INTO usuario(nombre,email,usuario_login) VALUES (%s,%s,%s) RETURNING usuario_id",
        (nombre.strip(), email, usuario),
    ).fetchone()["usuario_id"]
    conn.execute(
        "INSERT INTO web_credencial(usuario_id,password_hash) VALUES (%s,%s)",
        (usuario_id, generate_password_hash(password)),
    )
    conn.commit()
    return usuario_id


def autenticar_peticion():
    if (
        request.endpoint in {"auth.login", "static", "pages.salud"}
        or request.endpoint is None
    ):
        return None
    token = session.get("token")
    if token:
        g.usuario = (
            base()
            .execute(
                "SELECT u.usuario_id,u.nombre,u.rol FROM web_sesion s JOIN usuario u USING(usuario_id) "
                "WHERE s.token_hash=%s AND s.vence_en > now() AND u.activo AND u.rol='RRHH'",
                (token_hash(token),),
            )
            .fetchone()
        )
    if g.usuario is None:
        return redirect(
            url_for(
                "auth.login", next=request.full_path if request.method == "GET" else "/"
            )
        )
    return None


@auth.route("/ingresar", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        identificador = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = base()
        cred = conn.execute(
            "SELECT u.usuario_id,u.activo,u.rol,c.password_hash,c.fallos,c.bloqueado_hasta "
            "FROM usuario u JOIN web_credencial c USING(usuario_id) "
            "WHERE (CASE WHEN position('@' in %s)>0 THEN lower(u.email) ELSE u.usuario_login END)=%s "
            "FOR UPDATE OF c",
            (identificador, identificador),
        ).fetchone()
        now = datetime.now(UTC)
        bloqueado = cred and cred["bloqueado_hasta"] and cred["bloqueado_hasta"] > now
        correcto = check_password_hash(
            cred["password_hash"] if cred else _DUMMY_HASH, password
        )
        if (
            not cred
            or not correcto
            or not cred["activo"]
            or cred["rol"] != "RRHH"
            or bloqueado
        ):
            if cred and not bloqueado:
                fallos = cred["fallos"] + 1
                conn.execute(
                    "UPDATE web_credencial SET fallos=%s,bloqueado_hasta=%s WHERE usuario_id=%s",
                    (
                        fallos,
                        now + timedelta(minutes=5) if fallos >= 5 else None,
                        cred["usuario_id"],
                    ),
                )
            conn.commit()
            error = "No se pudo iniciar sesión. Comprueba tus datos; tras varios intentos, espera cinco minutos."
        else:
            if session.get("token"):
                conn.execute(
                    "DELETE FROM web_sesion WHERE token_hash=%s",
                    (token_hash(session["token"]),),
                )
            token = secrets.token_urlsafe(32)
            conn.execute(
                "UPDATE web_credencial SET fallos=0,bloqueado_hasta=NULL WHERE usuario_id=%s",
                (cred["usuario_id"],),
            )
            conn.execute(
                "INSERT INTO web_sesion(token_hash,usuario_id,vence_en) VALUES (%s,%s,%s)",
                (token_hash(token), cred["usuario_id"], now + timedelta(hours=8)),
            )
            conn.commit()
            session.clear()
            session.update(token=token, csrf=secrets.token_urlsafe(32))
            session.permanent = True
            destino = request.form.get("next", "/")
            if (
                not destino.startswith("/")
                or destino.startswith("//")
                or "\\" in destino
            ):
                destino = "/"
            return redirect(destino, code=303)
    return render_template("login.html", error=error), 401 if error else 200


@auth.post("/salir")
def logout():
    if session.get("token"):
        conn = base()
        conn.execute(
            "DELETE FROM web_sesion WHERE token_hash=%s",
            (token_hash(session["token"]),),
        )
        conn.commit()
    session.clear()
    return redirect(url_for("auth.login"), code=303)
