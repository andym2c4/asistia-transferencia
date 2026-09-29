"""Paraleliza solicitudes independientes; todas las escrituras SQL siguen serializadas."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from asistia.db import sha256_de

from .gemini import Gemini, SinCupo
from .ingesta import (
    RAIZ,
    aplicar_extraccion,
    estado,
    estado_extraccion,
    preseleccionar_calendarios_2025,
    registrar_fuente,
)


def extraer_lote(conn, tipos=None, trabajadores=3, limite=None):
    if not conn.execute("SELECT pg_try_advisory_lock(26091206) AS ok").fetchone()["ok"]:
        raise RuntimeError("Otra carga está activa")
    motor = Gemini()
    try:
        if tipos is None or "CALENDARIO_2025" in tipos:
            preseleccionar_calendarios_2025(conn)
        items = conn.execute(
            """SELECT * FROM cierre_documento WHERE tipo=ANY(%s)
          AND estado IN ('PENDIENTE','ERROR','ESPERANDO_CUPO','PROCESANDO')
          ORDER BY CASE tipo WHEN 'CALENDARIO_2026' THEN 0 WHEN 'ASISTENCIA' THEN 1 ELSE 2 END,ruta""",
            (tipos or ["ASISTENCIA", "CALENDARIO_2026", "CALENDARIO_2025"],),
        ).fetchall()
        if limite:
            items = items[:limite]
        for item in items:
            registrar_fuente(conn, item)
        conn.commit()

        def extraer(item):
            ruta = Path(item["ruta"])
            if sha256_de(ruta) != item["sha256"]:
                raise ValueError("El original cambió desde el inventario")
            contexto = f"Tipo esperado: {item['tipo']}. Nombre/ruta proporcionados por usuario: {ruta.relative_to(RAIZ.resolve()) if ruta.is_relative_to(RAIZ.resolve()) else ruta.name}."
            from .extraccion import extraer_documento

            return extraer_documento(motor, item, contexto)

        with ThreadPoolExecutor(max_workers=trabajadores) as pool:
            futuros = {pool.submit(extraer, item): item for item in items}
            for n, futuro in enumerate(as_completed(futuros), 1):
                item = futuros[futuro]
                try:
                    resultado = futuro.result()
                    if resultado["sha256"] != item["sha256"]:
                        raise ValueError(
                            "La extracción no corresponde a la huella inventariada"
                        )
                    conn.execute(
                        "UPDATE cierre_documento SET intentos=intentos+1 WHERE cierre_documento_id=%s",
                        (item["cierre_documento_id"],),
                    )
                    datos = aplicar_extraccion(conn, item, resultado)
                    nuevo = estado_extraccion(datos)
                    estado(conn, item, nuevo, datos, metodo="GEMINI")
                except SinCupo as exc:
                    conn.rollback()
                    nuevo = "ESPERANDO_CUPO"
                    estado(conn, item, nuevo, error=str(exc), metodo="GEMINI")
                except Exception as exc:  # noqa: BLE001 -- frontera de un archivo recuperable
                    conn.rollback()
                    nuevo = "ERROR"
                    mensaje = (
                        str(exc)[:500]
                        if isinstance(exc, (ValueError, TypeError))
                        else type(exc).__name__
                    )
                    estado(conn, item, nuevo, error=mensaje, metodo="GEMINI")
                print(
                    f"OCR {n}/{len(items)} {item['tipo']} {nuevo} {str(item['cierre_documento_id'])[:8]}",
                    flush=True,
                )
    finally:
        conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(26091206)")
        conn.commit()
