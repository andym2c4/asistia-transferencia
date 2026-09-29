# ASISTIA para continuar el desarrollo

Aplicación de RRHH de la UGEL Luya para importar NEXUS, revisar calendarios y reportes de asistencia y producir consolidados mensuales trazables. Python 3.12, Flask/Jinja/Waitress, PostgreSQL 16 y Gemini para extracción documental.

Esta transferencia contiene el código actual, las 23 migraciones, pruebas y herramientas de operación. La instalación real sigue en EC2; una clonación nueva empieza sin datos ni claves.

## Empezar

1. [Instalar y ejecutar un entorno separado](docs/DESARROLLO.md).
2. [Entender los módulos y las reglas](docs/ARQUITECTURA.md).
3. [Recibir la configuración por separado](docs/CONFIGURACION_PRIVADA.md).
4. [Consultar el estado publicado y los pendientes](docs/ESTADO.md).
5. [Publicar y recuperar sin reemplazar datos](docs/PRODUCCION.md).

Para agregar una feature: rama propia, caso reproducible, implementación, prueba pertinente y registro en `docs/ESTADO.md`. Las migraciones nuevas van después de `0023`; no modificar las ya aplicadas. Ver también [AGENTS.md](AGENTS.md).

El código parte de `dc875261cf346114a6e3c91ffe9d2455ec282f26` del repositorio original. Producción usa `7c24e47291d4f4829e0d60c271467e3ad8cd2118`; los commits posteriores de ese corte son documentación. [Procedencia y archivos copiados](docs/procedencia.json). El repositorio de transferencia tiene historia nueva y no cambia automáticamente el origen ni producción.

Los datos nominales, `.env`, credenciales, modelos y binarios del conversor se entregan por separado cuando corresponda. No están en GitHub. Los fixtures de pruebas son independientes del estado operativo; una alerta no demuestra fraude ni una declaración acredita presencia física.
