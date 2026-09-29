"""Errores de cuota, integridad de respuestas y reutilización sin llamadas adicionales."""

import io
import json
import urllib.error

import pytest

from asistia.cierre import gemini


def test_rotacion_429_y_cache_sin_secretos(tmp_path, monkeypatch):
    monkeypatch.setattr(gemini.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        gemini,
        "configuracion",
        lambda _: (["clave_ficticia_1", "clave_ficticia_2"], "modelo-ficticio"),
    )
    llamadas = []

    def request(req, timeout):
        llamadas.append(req)
        if len(llamadas) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 429, "quota", {}, io.BytesIO(b'{"daily":true}')
            )
        return io.BytesIO(
            json.dumps(
                {
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {"parts": [{"text": '{"bloques":[]}'}]},
                        }
                    ]
                }
            ).encode()
        )

    monkeypatch.setattr(gemini.urllib.request, "urlopen", request)
    ruta = tmp_path / "ficticio.pdf"
    ruta.write_bytes(b"%PDF-1.0 ficticio")
    motor = gemini.Gemini(cache=tmp_path / "cache")
    resultado = motor.extraer(ruta, "Prueba")
    assert len(llamadas) == 2
    assert motor.extraer(ruta, "Prueba") == resultado
    assert len(llamadas) == 2
    assert llamadas[0].get_header("X-goog-api-key") == "clave_ficticia_1"
    assert llamadas[1].get_header("X-goog-api-key") == "clave_ficticia_2"
    assert "clave_ficticia" not in next((tmp_path / "cache").glob("*.json")).read_text()
    assert all("clave_ficticia" not in r.full_url for r in llamadas)


def test_respuesta_truncada_no_se_aplica_ni_cachea(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gemini, "configuracion", lambda _: (["ficticia"], "modelo-ficticio")
    )
    monkeypatch.setattr(
        gemini.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(b'{"candidates":[{"finishReason":"MAX_TOKENS"}]}'),
    )
    ruta = tmp_path / "ficticio.pdf"
    ruta.write_bytes(b"%PDF-1.0 ficticio")
    motor = gemini.Gemini(cache=tmp_path / "cache")
    with pytest.raises(ValueError, match="truncada"):
        motor.extraer(ruta, "Prueba")
    assert list((tmp_path / "cache").glob("*.json")) == []


def test_modelo_lento_reserva_tiempo_para_alternativo(tmp_path, monkeypatch):
    monkeypatch.setattr(gemini, "configuracion", lambda _: (["ficticia"], "modelo-uno"))
    reloj = [100.0]
    monkeypatch.setattr(gemini.time, "monotonic", lambda: reloj[0])
    ruta = tmp_path / "ficticio.pdf"
    ruta.write_bytes(b"%PDF-ficticio")
    motor = gemini.Gemini(cache=tmp_path / "cache", modelos=["modelo-dos"])
    intentos = []

    def extraer(ruta, contexto, modelo, limite):
        intentos.append((modelo, limite))
        if modelo == "modelo-uno":
            reloj[0] = limite
            raise gemini.SinCupo("Límite del primer modelo")
        return {"modelo": modelo}

    monkeypatch.setattr(motor, "_extraer_modelo", extraer)
    assert motor.extraer(ruta, "Prueba")["modelo"] == "modelo-dos"
    assert intentos[0][1] < intentos[1][1] <= 400


def test_timeout_del_proveedor_no_reintenta_pdf_por_paginas(tmp_path, monkeypatch):
    from pypdf import PdfWriter

    from asistia.cierre import extraccion

    monkeypatch.setattr(gemini, "configuracion", lambda _: (["ficticia"], "modelo-uno"))
    reloj = [0.0]
    monkeypatch.setattr(gemini.time, "monotonic", lambda: reloj[0])
    ruta = tmp_path / "ficticio.pdf"
    pdf = PdfWriter()
    pdf.add_blank_page(width=400, height=500)
    pdf.add_blank_page(width=400, height=500)
    pdf.write(ruta)
    motor = gemini.Gemini(cache=tmp_path / "cache", modelos=["modelo-dos"])
    intentos = []

    def lento(ruta, contexto, modelo, limite):
        intentos.append(modelo)
        reloj[0] = 301.0
        raise gemini.SinCupo("Proveedor sin respuesta en el plazo")

    def no_dividir(*args):
        pytest.fail("Un timeout no es evidencia de un problema de formato del PDF")

    monkeypatch.setattr(motor, "_extraer_modelo", lento)
    monkeypatch.setattr(extraccion, "extraer_paginas", no_dividir)
    with pytest.raises(gemini.SinCupo):
        extraccion.extraer_documento(motor, {"ruta": str(ruta)}, "Prueba de límite")
    assert intentos == ["modelo-uno"]
