"""Cotejar originales y ejecutar conversión nueva como el usuario de la web.

Solo lectura en PostgreSQL; la salida de prueba es temporal y no altera documentos.
No realiza llamadas OCR/Gemini ni representa validación de RRHH.
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from pypdf import PdfReader

from asistia.cierre import conversion
from asistia.configuracion import entorno_local
from asistia.originales import verificar_originales

ROOT = Path(__file__).resolve().parents[2]


def main():
    with psycopg.connect(
        entorno_local()["ASISTIA_DATABASE_URL"], row_factory=dict_row
    ) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        originals = verificar_originales(conn, ROOT / "data/web")
        counts = originals.por_estado()
        assert counts["ALTERADO"] == 0
        # DT06 conserva una referencia histórica ausente anterior al despliegue.
        assert counts["AUSENTE"] == 1
        reference = json.loads(
            (ROOT / "docs/evidencias/autonomia-2026-09-15/conversion.json").read_text()
        )
        sha = reference["fuente_sha256"]
        source = Path(
            conn.execute(
                "SELECT ruta_objeto FROM objeto_archivo WHERE sha256=%s", (sha,)
            ).fetchone()["ruta_objeto"]
        )
    (ROOT / "data/production").mkdir(exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=ROOT / "data/production") as temporary:
        conversion.SALIDA = Path(temporary)
        pdf, _ = conversion.representar_docx(source)
        new = PdfReader(pdf)
        previous = PdfReader(ROOT / f"data/cierre/representaciones/{sha}/documento.pdf")
        assert len(new.pages) == len(previous.pages) == reference["paginas"]
        assert [p.extract_text() for p in new.pages] == [
            p.extract_text() for p in previous.pages
        ]
        assert [tuple(p.mediabox) for p in new.pages] == [
            tuple(p.mediabox) for p in previous.pages
        ]
    print(
        json.dumps(
            {
                "verified_at": datetime.now(UTC).isoformat(),
                "originals": counts,
                "conversion_pages": reference["paginas"],
                "conversion_text_and_dimensions_equal": True,
                "gemini_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
