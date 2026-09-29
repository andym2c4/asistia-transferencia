# Reglas para continuar ASISTIA

- Leer `docs/ESTADO.md`, `docs/DESARROLLO.md` y `docs/ARQUITECTURA.md` al comenzar.
- Producción EC2 es la fuente operativa. No restaurar sobre ella una copia local antigua. Mantener sus servicios activos salvo indicación expresa del responsable.
- Este repositorio permite trabajar con una base de desarrollo nueva. Las pruebas destructivas usan exclusivamente `asistia_test*`; nunca `.env` de producción ni datos reales para hacerlas pasar.
- Conservar originales, valores recibidos y corregidos, versiones, responsable, motivo y localizadores. Reimportar no debe duplicar hechos ni alterar silenciosamente salidas revisadas.
- Identidad institucional: `cod_mod + anexo`, ambos texto. Persona, plaza, vínculo y reporte tienen granos distintos. Ausencia en NEXUS no prueba cese.
- Vacío, ilegible, desconocido y no aplicable no son presencia, falta ni cero. Usar calendario y vigencia. Alertas y modelos son apoyo a revisión; no deciden descuentos ni prueban fraude.
- No cambiar retrospectivamente SQL aplicado. Añadir migración y verificarla en una base aislada; `update.py` rechaza cambios de esquema deliberadamente.
- Preservar cambios ajenos y datos privados. Revisar archivos preparados antes de cada commit. Git no respalda la base ni los originales.
- Para frontend conservar estados, confirmación explícita, accesibilidad y recuperación; comprobar recorridos además de apariencia.
- Registrar pruebas realmente ejecutadas, límites, migraciones, configuración y siguiente paso en `docs/ESTADO.md`. Diferenciar implementado, verificado técnicamente y validado con RRHH.
- Publicar únicamente una versión autorizada, respaldada y verificable. No ejecutar `install.py`, `infra.py`, `capture.py`, `local_copy.py`, `pause_backup.py` o restauraciones como parte del arranque habitual.
- No subir `.env`, `data/`, claves, dumps, documentos nominales ni la carpeta de transferencia privada. Los operadores obtienen acceso AWS/GitHub individual; no copiar credenciales personales del propietario.
