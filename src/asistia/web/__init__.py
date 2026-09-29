"""Recorrido web de RRHH. El núcleo de cálculo permanece independiente de Flask."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from pathlib import Path

import psycopg
from flask import Flask, g, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException, SecurityError

from asistia.configuracion import entorno_local, ruta_interna
from asistia.db import dsn
from asistia.niveles import NIVELES


class ErrorDeTrabajo(Exception):
    def __init__(self, mensaje: str, status: int = 400):
        self.mensaje = mensaje
        self.status = status
        super().__init__(mensaje)


def create_app(config: dict | None = None) -> Flask:
    entorno = entorno_local()
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=entorno.get("ASISTIA_WEB_SECRET"),
        DATABASE_URL=dsn(),
        STORAGE_ROOT=ruta_interna(entorno.get("ASISTIA_WEB_STORAGE", "data/web")),
        ORIGINAL_ROOTS=[str(ruta_interna("data/raw"))],
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,
        MAX_FORM_MEMORY_SIZE=128 * 1024,
        MAX_FORM_PARTS=40,
        SESSION_COOKIE_NAME="asistia_sesion",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=entorno.get("ASISTIA_WEB_HTTPS", "0") == "1",
        DEVELOPMENT_EXPORTS=entorno.get("ASISTIA_WEB_DEVELOPMENT_EXPORTS", "0") == "1",
        DEVELOPMENT_TOOLS=entorno.get("ASISTIA_WEB_DEVELOPMENT_TOOLS", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        TRUSTED_HOSTS=entorno.get(
            "ASISTIA_WEB_HOSTS", "localhost,127.0.0.1,[::1]"
        ).split(","),
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        archivo_clave = Path(app.config["STORAGE_ROOT"]) / "secret.key"
        if archivo_clave.is_file():
            app.config["SECRET_KEY"] = archivo_clave.read_text().strip()
    if not app.config["SECRET_KEY"] or len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError(
            "Ejecuta 'asistia web crear-clave' o configura ASISTIA_WEB_SECRET con al menos 32 caracteres aleatorios."
        )
    app.config["STORAGE_ROOT"] = Path(app.config["STORAGE_ROOT"]).resolve()
    app.config["STORAGE_ROOT"].mkdir(parents=True, exist_ok=True, mode=0o700)

    from .auth import autenticar_peticion, auth
    from .desarrollo import es_herramienta
    from .routes import pages

    app.register_blueprint(auth)
    app.register_blueprint(pages)

    @app.before_request
    def proteger():
        # Hasta 500 documentos seleccionados más CSRF, alcance y motivo.
        # El resto de formularios conserva su límite habitual.
        if request.endpoint == "pages.categoria_aplicar":
            request.max_form_parts = 510
        if request.endpoint == "pages.asignar_dias_calendario":
            request.max_form_parts = (
                380  # 366 días y metadatos, también sin JavaScript.
            )
        # La sesión firmada no contiene PII, contraseña ni borradores de formularios.
        session.setdefault("csrf", secrets.token_urlsafe(32))
        g.usuario = None
        if request.method == "POST":
            token = request.form.get("csrf", "")
            if not token or not secrets.compare_digest(token, session["csrf"]):
                raise ErrorDeTrabajo(
                    "La sesión del formulario venció. Actualiza la página antes de volver a guardar.",
                    400,
                )
        respuesta = autenticar_peticion()
        if respuesta is not None:
            return respuesta
        if es_herramienta(request.endpoint) and not app.config["DEVELOPMENT_TOOLS"]:
            raise ErrorDeTrabajo("No encontramos esa página o registro.", 404)

    @app.after_request
    def cabeceras(response):
        if (
            request.method == "POST"
            and request.headers.get("X-ASISTIA-Async") == "1"
            and response.status_code in {302, 303}
        ):
            # Fetch no debe seguir el redirect y consumir los avisos antes de la navegación.
            response = jsonify(redirect=response.headers["Location"])
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            + (
                "img-src 'self' data: blob:; "
                if request.endpoint in {"pages.fuente", "pages.fuente_calendario"}
                else "img-src 'self' data:; "
            )
            + "frame-src 'self'; object-src 'none'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
        )
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.teardown_appcontext
    def cerrar_base(_error):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()  # Nunca confirma escrituras inconclusas al renderizar un error.

    @app.context_processor
    def contexto_comun():
        return {
            "niveles": NIVELES,
            "catalogo_disponible": True,
            "herramientas_desarrollo": app.config["DEVELOPMENT_TOOLS"],
            "en_herramientas_desarrollo": es_herramienta(request.endpoint),
            "datos_ficticios": app.config.get("tipo") == "PRUEBA_FICTICIA_NO_RRHH",
            "csrf": session.get("csrf", ""),
            "operacion_id": lambda: str(uuid.uuid4()),
            "usuario": getattr(g, "usuario", None),
        }

    @app.errorhandler(ErrorDeTrabajo)
    def error_trabajo(exc):
        if (
            request.endpoint == "pages.cargar_revision"
            and request.accept_mimetypes.best == "application/json"
        ):
            return jsonify(error=exc.mensaje), exc.status
        if request.endpoint in {
            "pages.comparar_asistencia_dia",
            "pages.vista_original",
            "pages.pagina_original",
            "pages.vista_calendario",
            "pages.pagina_calendario",
        }:
            return jsonify(error=exc.mensaje), exc.status
        return render_template("error.html", mensaje=exc.mensaje), exc.status

    @app.errorhandler(psycopg.Error)
    def error_base(exc):
        app.logger.error("Fallo de base de datos: %s", type(exc).__name__)
        return render_template(
            "error.html",
            mensaje=(
                "No se pudo completar la operación con la base de datos. "
                "Comprueba el resultado en la lista antes de reintentar. "
                "Si persiste, solicita revisar la conexión y las migraciones."
            ),
        ), 503

    @app.errorhandler(HTTPException)
    def error_http(exc):
        if isinstance(exc, SecurityError):
            # Un Host rechazado no tiene adaptador de URL para renderizar navegación.
            return "Dirección de acceso no autorizada.", 400
        mensajes = {
            404: "No encontramos esa página o registro.",
            413: "La carga supera 32 MB. Carga los archivos por separado; cada archivo admite hasta 20 MB.",
            405: "Esa acción no está disponible desde este enlace.",
            400: "La solicitud no es válida. Revisa el formulario y vuelve a intentarlo.",
        }
        return render_template(
            "error.html",
            mensaje=mensajes.get(exc.code, "No se pudo completar la solicitud."),
        ), exc.code

    @app.errorhandler(Exception)
    def error_inesperado(exc):
        if app.testing:
            raise exc
        app.logger.error("Fallo de aplicación: %s", type(exc).__name__)
        return render_template(
            "error.html",
            mensaje="La operación se interrumpió. Comprueba su estado en la lista antes de reintentar.",
        ), 500

    return app
