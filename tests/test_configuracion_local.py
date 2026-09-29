"""Autonomía: configuración local, precedencia y recursos sin escapes de raíz."""

import json

import pytest

from asistia import configuracion as local
from asistia.cierre import gemini


@pytest.fixture
def proyecto(tmp_path, monkeypatch):
    root = tmp_path / "asistia"
    root.mkdir()
    monkeypatch.setattr(local, "RAIZ_PROYECTO", root)
    for key in [
        "ASISTIA_GEMINI_ENV_FILE",
        "ASISTIA_GEMINI_KEYS",
        "GEMINI_API_KEY",
        "VISION_LLM_API_KEYS",
        "VISION_LLM_API_KEY",
        "ASISTIA_GEMINI_MODEL",
        "VISION_LLM_MODEL",
    ]:
        monkeypatch.delenv(key, raising=False)
    return root


def test_gemini_lee_env_local_desde_otro_cwd(proyecto, tmp_path, monkeypatch):
    (proyecto / ".env").write_text(
        'ASISTIA_GEMINI_KEYS="ficticia_1,ficticia_2,ficticia_1"\nASISTIA_GEMINI_MODEL=modelo-local\n'
    )
    monkeypatch.chdir(tmp_path)
    assert gemini.configuracion() == (["ficticia_1", "ficticia_2"], "modelo-local")
    motor = gemini.Gemini()
    assert motor.cache == proyecto / "data/cierre/ocr"


def test_variables_explicitas_prevalecen(proyecto, monkeypatch):
    (proyecto / ".env").write_text(
        "ASISTIA_GEMINI_KEYS=ficticia_archivo\nASISTIA_GEMINI_MODEL=modelo-archivo\n"
    )
    monkeypatch.setenv("ASISTIA_GEMINI_KEYS", "ficticia_proceso")
    monkeypatch.setenv("ASISTIA_GEMINI_MODEL", "modelo-proceso")
    assert gemini.configuracion() == (["ficticia_proceso"], "modelo-proceso")


def test_puntero_relativo_y_modelos_se_conservan(proyecto):
    (proyecto / "data/web").mkdir(parents=True)
    (proyecto / ".env").write_text("ASISTIA_GEMINI_KEYS=ficticia\n")
    (proyecto / "data/web/gemini_config.json").write_text(
        json.dumps(
            {"env_file": ".env", "modelo": "modelo-a", "modelos_respaldo": ["modelo-b"]}
        )
    )
    motor = gemini.Gemini()
    assert motor.claves == ["ficticia"]
    assert motor.modelos == ["modelo-a", "modelo-b"]


@pytest.mark.parametrize("tipo", ["argumento", "puntero", "variable", "symlink"])
def test_no_lee_env_fuera_del_proyecto(proyecto, tmp_path, monkeypatch, tipo):
    externo = tmp_path / "otro.env"
    externo.write_text("ASISTIA_GEMINI_KEYS=secreto_que_no_debe_aparecer\n")
    argumento = None
    if tipo == "argumento":
        argumento = externo
    elif tipo == "variable":
        monkeypatch.setenv("ASISTIA_GEMINI_ENV_FILE", str(externo))
    elif tipo == "symlink":
        (proyecto / ".env").symlink_to(externo)
    else:
        (proyecto / "data/web").mkdir(parents=True)
        (proyecto / "data/web/gemini_config.json").write_text(
            json.dumps({"env_file": str(externo)})
        )
    with pytest.raises(ValueError, match="raíz") as exc:
        gemini.configuracion(argumento)
    assert "secreto_que_no_debe_aparecer" not in str(exc.value)


def test_env_no_ejecuta_comandos_y_admite_comentarios(proyecto):
    (proyecto / ".env").write_text(
        'export EJEMPLO="$(touch no_crear) # literal" # comentario\nOTRO=valor # comentario\n'
    )
    assert local.leer_env() == {
        "EJEMPLO": "$(touch no_crear) # literal",
        "OTRO": "valor",
    }
    assert not (proyecto / "no_crear").exists()


def test_error_de_sintaxis_no_expone_valor(proyecto):
    (proyecto / ".env").write_text('CLAVE="secreto_incompleto\n')
    with pytest.raises(ValueError, match="línea 1") as exc:
        local.leer_env()
    assert "secreto_incompleto" not in str(exc.value)


def test_configuracion_interna_rechaza_recurso_externo(proyecto, tmp_path):
    with pytest.raises(ValueError, match="raíz"):
        local.ruta_interna(tmp_path / "conversor")
    assert local.ruta_interna("data/runtime/x2t") == proyecto / "data/runtime/x2t"


def test_db_toma_configuracion_local_sin_mutar_environ(proyecto, monkeypatch):
    from asistia.db import dsn

    monkeypatch.delenv("ASISTIA_DATABASE_URL", raising=False)
    (proyecto / ".env").write_text(
        "ASISTIA_DATABASE_URL=postgresql://localhost/asistia_test_local\n"
    )
    assert dsn() == "postgresql://localhost/asistia_test_local"
    monkeypatch.setenv(
        "ASISTIA_DATABASE_URL", "postgresql://localhost/asistia_test_explicita"
    )
    assert dsn() == "postgresql://localhost/asistia_test_explicita"
