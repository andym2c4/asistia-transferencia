# Instalación y trabajo diario

Entorno comprobable: Linux x86_64, Python 3.12, `uv`, Docker con Compose. Para conversión y visores se necesitan también `bubblewrap`, `poppler-utils` y el runtime ONLYOFFICE descrito en [configuración](CONFIGURACION_PRIVADA.md). PostgreSQL se ejecuta en Docker. Git, AWS CLI v2 y Session Manager plugin son necesarios solo según la tarea; desarrollar en una base ficticia no requiere acceso AWS.

## Instalación nueva

Con acceso concedido al repositorio privado, clonar por SSH:

```bash
git clone git@github.com:andym2c4/asistia-transferencia.git
cd asistia-transferencia
```

Desde esa carpeta, sin copiar secretos de producción:

```bash
uv sync --frozen
cp -n .env.example .env
chmod 600 .env
docker compose up -d --wait db db_test
ASISTIA_DATABASE_URL=postgresql://asistia:asistia_dev@127.0.0.1:5554/asistia_dev \
  uv run python migrations/scripts/aplicar.py
uv run asistia web crear-clave
uv run asistia web crear-usuario --nombre "Operador de desarrollo" --usuario operador
uv run asistia web servir --host 127.0.0.1 --puerto 8080
```

La creación del usuario solicita y confirma una contraseña; no ponerla en la línea de comandos. Abrir `http://127.0.0.1:8080/ingresar`. `GET /salud` comprueba disponibilidad básica. Es normal encontrar una instalación vacía: las migraciones siembran catálogos, no documentos de RRHH. Importar exclusivamente archivos ficticios durante las primeras comprobaciones.

Los puertos 5554/5555 y el proyecto Compose `asistia_transferencia` evitan compartir los volúmenes y puertos de la instalación anterior. Las contraseñas de Compose son ejemplos exclusivos de desarrollo en loopback. No usar este Compose como configuración productiva. El runner de migraciones lee `ASISTIA_DATABASE_URL` del proceso: el prefijo del comando es necesario.

Para terminar, detener la web con Ctrl+C y ejecutar `docker compose stop`. El volumen de desarrollo se conserva. No borrar volúmenes para resolver errores de arranque. `scripts/workspace.sh` y `scripts/local.py` conservan el flujo histórico de administración del proyecto original; no son el lanzador de este entorno nuevo.

## Pruebas sin datos privados

Preparar primero el conversor y las fuentes siguiendo `CONFIGURACION_PRIVADA.md`: las pruebas de calendario y visor necesitan ese runtime incluso con documentos ficticios. Esto no requiere copiar datos de RRHH ni claves Gemini.

```bash
docker compose up -d --wait db_test
export ASISTIA_TEST_DATABASE_URL=postgresql://asistia:asistia_test@127.0.0.1:5555/asistia_test_transferencia
uv run pytest -q \
  --ignore=tests/test_asistencia_regresion.py \
  --ignore=tests/test_ciclo_completo_tactamal.py \
  --ignore=tests/test_revision.py \
  --ignore=tests/test_resolucion.py \
  --ignore=tests/test_evaluacion_manifiesto.py \
  --ignore=tests/test_rubrica.py
```

Es una suite de incorporación autónoma, no la evaluación completa del corpus. `test_evaluacion_manifiesto.py` requiere el corte privado de evaluación; `test_rubrica.py` requiere instalar el grupo opcional con `uv sync --frozen --group reportes` y puede ejecutarse aparte con perfiles ficticios. Los primeros cuatro archivos contienen regresiones contra NEXUS/asistencias reales no distribuidos; también contienen algunos casos sintéticos que se pueden seleccionar por nombre. `test_visor_original.py` se incluye con el conversor y las fuentes preparados. También puede ejecutarse de forma específica con `uv run pytest -q tests/test_visor_original.py`. Dos pruebas nominales del proyecto original no se publican: `test_nexus_regresion.py` y `test_universo_esperado.py`.

`tests/conftest.py` aplica las migraciones y trunca las tablas de la base de pruebas. Rechaza nombres que no empiecen por `asistia_test`; conservar esta guardia. Las pruebas Gemini usan respuestas simuladas, no deben consumir la clave real para validar código.

## Agregar una feature

```bash
git switch -c feat/nombre-del-cambio
# Modificar y probar el comportamiento pertinente.
uv run pytest -q tests/test_archivo_afectado.py
uv run ruff check ruta/del/codigo.py tests/test_archivo_afectado.py
git diff --check
git status --short
```

Registrar en [estado](ESTADO.md) el problema, archivos, pruebas y resultados, configuración/migraciones y siguiente acción. Revisar el diff antes de preparar archivos y crear el commit. Reiniciar Waitress para comprobar cambios de Python. Para cambios web, probar el flujo autenticado, estados vacíos/error, recuperación y una pantalla estrecha. No mezclar datos ficticios con la instalación operativa.

Antes de publicar, seguir [producción](PRODUCCION.md). No hay sincronización ni despliegue automático desde este repositorio.
