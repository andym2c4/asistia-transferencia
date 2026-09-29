# Mapa del proyecto y reglas de cambio

La web se renderiza en el servidor con Flask y Jinja; JavaScript añade selección, revisión y carga progresiva. Waitress sirve la aplicación detrás de Nginx HTTPS en producción. PostgreSQL 16 conserva datos, reglas, versiones y auditoría. Los originales se guardan por contenido en archivos privados; la base mantiene referencias y hashes.

| Cambio | Punto de entrada |
|---|---|
| Comandos y usuarios | `src/asistia/cli.py` |
| Configuración y conexión | `configuracion.py`, `db.py` |
| NEXUS, Excel y calendarios | `src/asistia/importar/` |
| Lectura documental, Gemini, conversión y caché | `src/asistia/cierre/` |
| Universo, consolidación y revisión | `src/asistia/consolidado/` |
| Autenticación y servidor | `src/asistia/web/__init__.py`, `auth.py` |
| Rutas, consultas y acciones | `src/asistia/web/routes.py`, módulos de `web/` |
| Pantallas y comportamiento visual | `src/asistia/web/templates/`, `static/` |
| Modelos, cortes y monitoreo | `src/asistia/monitoreo/` |
| Esquema y datos de catálogo iniciales | `migrations/0001` a `0023`, runner en `migrations/scripts/aplicar.py` |
| Pruebas reproducibles y fixtures | `tests/`, especialmente `conftest.py`, `fixtures_excel.py`, `test_web.py` |
| Versiones, respaldos y acceso remoto | `scripts/production/` |

Consultar `uv.lock` como versión efectiva de dependencias; `pyproject.toml` define rangos y grupos. No se requiere un frontend separado, Node, GPU, RDS, Redis ni Kubernetes para ejecutar el núcleo.

## Invariantes que deben conservarse

- Institución = código modular + anexo como texto; no confundir servicio educativo con local físico.
- Persona, plaza, vínculo, rol y vigencia son distintos. NEXUS llega consolidado externamente; una ausencia en ese archivo no prueba cese.
- Vacío, desconocido, ilegible y no aplicable conservan significado distinto. Días esperados dependen de calendario y vigencia.
- Original, valor extraído, valor corregido y salida revisada tienen procedencia/versiones propias. Una fuente nueva no modifica silenciosamente lo ya aprobado.
- Clasificar códigos no equivale a confirmar la revisión completa. Coherencia, revisión de reporte y aprobación del consolidado siguen separadas.
- Las reglas de alertas e Isolation Forest priorizan cotejo. No prueban fraude ni sustituyen decisiones de RRHH o de remuneración.
- El grano de los joins y la población deben quedar explícitos; contar vínculos o filas no equivale a contar personas.
- La idempotencia, trazabilidad, permisos, CSRF y aislamiento del visor se verifican al modificar los recorridos.

## Migraciones

Producción tiene `0001`–`0023`. Añadir `0024_...sql` para un cambio nuevo; probar tanto instalación vacía como actualización desde el esquema anterior si afecta datos existentes. El runner registra nombres aplicados en `_migraciones_aplicadas`. No editar retrospectivamente esos archivos. Un cambio de datos/esquema necesita respaldo y un procedimiento explícito de actualización y recuperación; el actualizador de código rechaza SQL distinto.

## Alcance

Oficio, firma y envío posterior a la DRE están fuera del sistema. QR y ampliaciones del modelo requieren evidencia y una petición concreta. Verificación técnica no significa aceptación RRHH, exactitud OCR medida ni ahorro observado. Las definiciones y antecedentes completos permanecen en el repositorio original privado.
