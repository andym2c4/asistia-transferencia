# Configuración que se entrega fuera de GitHub

La carpeta local `asistia-transferencia-privado`, junto a este repositorio, conserva copias con permisos restringidos. No forma parte del repositorio, de sus commits ni del envío a GitHub. Contiene un inventario con hashes y una guía de uso; compartirla solo por el medio separado elegido por el responsable.

| Grupo | Uso |
|---|---|
| `snapshot-local/` | `.env`, parámetros del contenedor, dueño local, clave de sesión y configuración Gemini del snapshot histórico. No es la configuración vigente de EC2. |
| `acceso-produccion/` | Metadatos de infraestructura y conexiones PostgreSQL de lectura/aplicación para el túnel; requieren acceso AWS autorizado aparte. |
| `produccion-respaldo-20260929T163500Z/` | `.env`, clave web y preferencias Gemini obtenidos del respaldo retenido previo a PP02 y cotejados por hash. PP02 no cambió configuración. Es un corte fechado, no una sincronización continua. |
| `recursos/onlyoffice.tar.gz` | Conversor, SDK, diccionarios, fuentes, licencia y manifiesto del runtime Linux x86_64. |

Para una instalación de desarrollo nueva usar `.env.example` y generar su propia clave web y usuario. No copiar el `.env` productivo sobre el entorno de prueba ni importar la base real para verificar una feature rutinaria. Las claves Gemini se configuran solo cuando sea necesario probar la integración autorizada; las pruebas automatizadas usan simulaciones.

## Preparar el conversor en otra carpeta

Instalar `bubblewrap` y `poppler-utils` en el sistema. Recibir `onlyoffice.tar.gz`, extraerlo en una carpeta temporal y copiarlo a `data/runtime/onlyoffice`. Si ya existe, conservarlo y comparar versiones antes de reemplazarlo. Regenerar el índice de fuentes para la nueva ruta:

```bash
uv run python - <<'PY'
from pathlib import Path
p = Path('data/runtime/onlyoffice/fonts').resolve()
template = (p / 'AllFonts.template.js').read_text()
(p / 'AllFonts.js').write_text(template.replace('__ASISTIA_FONTS__', str(p)))
PY
```

Conservar `converter/x2t` ejecutable. `ldd data/runtime/onlyoffice/converter/x2t` permite detectar bibliotecas faltantes. El manifiesto original incluye el índice anterior: documentar su regeneración al comparar hashes. Después ejecutar las pruebas del visor indicadas en [desarrollo](DESARROLLO.md). En Ubuntu con AppArmor, revisar el perfil específico del visor usado por `scripts/production/bootstrap.sh`; no desactivar restricciones globales para hacerlo funcionar.

## Accesos y datos que no viajan con el repositorio

No se copian claves SSH, tokens GitHub ni credenciales personales de AWS. El nuevo responsable necesita acceso individual al repositorio y un perfil AWS propio con los permisos adecuados. Los scripts históricos usan el nombre de perfil `personal`, región `us-east-1` y stack `asistia-production`; ese nombre se puede configurar para su propia identidad, sin reutilizar el acceso del dueño.

La carpeta separada no incluye base, originales nominales ni dumps. Si se transfiere la operación, obtener del respaldo S3 autorizado un conjunto consistente de base, archivos, configuración y código, conservar su manifiesto y comprobar una restauración aislada. Git por sí solo no recupera la operación. La copia local histórica no sustituye los datos actuales de EC2.
