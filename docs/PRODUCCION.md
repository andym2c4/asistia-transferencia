# Operación y publicación

ASISTIA opera en AWS `us-east-1`, stack `asistia-production`: una EC2 t3a.medium Linux, disco gp3 cifrado de 40 GiB, IPv4 fija y S3 privado/versionado para respaldos. Web y PostgreSQL 16 comparten servidor; no hay alta disponibilidad. El responsable conserva los identificadores concretos en el paquete privado. EC2 permanece activa.

Presupuesto del corte 29/09/2026: AWS US$38–42/mes antes de impuestos con uso previsto; saldo estimado US$199,26. No es saldo en tiempo real. Gemini se factura aparte. El informe de costos entregado al propietario desarrolla supuestos y fechas.

## Consultar sin cambiar datos

Con AWS CLI v2, Session Manager plugin, cliente PostgreSQL y acceso individual autorizado, colocar exclusivamente los archivos de `acceso-produccion/data/production/` en `data/production/` del clon. Revisar su fecha; `active.json` es un marcador, no prueba por sí solo del release actual.

```bash
python3 scripts/production/dev.py status
python3 scripts/production/dev.py tunnel
# En otra terminal; lectura por defecto:
python3 scripts/production/dev.py sql
```

El túnel utiliza el puerto local 5546. `sql --write` habilita escritura con el rol de aplicación: usar solo para una operación explícita y trazable. Preferir la UI para decisiones de RRHH. Los comandos de desarrollo y pytest de `DESARROLLO.md` no requieren estos archivos.

## Publicar código

1. Comprobar el commit que está realmente en EC2 y las operaciones posteriores. Revisar el alcance autorizado, cambios y pruebas; incluir archivos nuevos y `uv.lock`.
2. Crear un respaldo consistente antes de la intervención y verificar dump, hashes, archivos y manifiesto; conservar reversión. No restaurar el snapshot local sobre la base actual.
3. Construir el paquete local con `python3 scripts/production/release.py` desde un commit revisado. Se genera `data/production/releases/FECHA/code.tar.gz` y su SHA256, sin `.env` ni `data/`.
4. Transferir el paquete al bucket privado autorizado y al servidor mediante el acceso operativo. Los scripts `remote.py send/result` usan SSM; no poner secretos dentro de sus comandos ni logs.
5. En EC2, con privilegios y ventana coordinados, ejecutar el actualizador existente: `/data/projects/asistia/.venv/bin/python /data/projects/asistia/scripts/production/update.py RUTA_DEL_PAQUETE SHA256`. El script detiene brevemente la web, reemplaza código/dependencias, comprueba salud y conserva reversión; no restaura datos.
6. Verificar `/salud`, ingreso/logout, flujo afectado y descargas por HTTPS. Confirmar servicios/timers, respaldos y datos conservados. Registrar commit, SHA256, respaldo y comprobaciones en `ESTADO.md`.

`update.py` compara las migraciones entrantes con las instaladas y rechaza diferencias. Para nuevo esquema preparar una migración nueva, probar su actualización en base aislada y realizar su aplicación explícita respaldada antes de promover el código compatible. No retirar la guardia ni tratar el runner como reversión automática de datos.

Los scripts de infraestructura/instalación/copia real (`infra.py`, `install.py`, `local_copy.py`, `capture.py`) son herramientas del procedimiento original. Algunas verificaciones históricas requieren su corpus y evidencias locales. No ejecutarlas para actualizar una feature ni para iniciar el clon vacío. La carpeta servidor `/data/projects/asistia` y los recursos administrativos `/var/lib/asistia` permanecen como están; el nombre nuevo del repositorio no exige renombrarlos.

## Respaldo y recuperación

Los timers `asistia-backup.timer` y `asistia-certificates.timer` acompañan a `asistia`. El respaldo diario está programado a las 03:00 de Lima. Debe existir su manifiesto final; una copia parcial no acredita respaldo exitoso. Los dumps ordinarios caducan a los 30 días, `staging/` a los siete; versiones de archivos y respaldos retenidos tienen otro tratamiento. Revisar la política vigente antes de depender de ella.

Respaldo retenido previo a PP02: `backups/releases/20260929-pp02-before/latest.json`, corte de 16:35 UTC. Es anterior a esa publicación; usar un respaldo posterior completo para recuperar operaciones posteriores. Para un incidente, capturar lo actual si es posible, restaurar primero en un destino nuevo y cotejar base, secuencias, hashes, originales, configuración y código. Solo cambiar el servicio después de comprobarlo. Una reversión de código no revierte esquema ni decisiones de negocio.

Revisar antigüedad del backup, certificado, espacio y disponibilidad. El presupuesto AWS existente no apaga servicios ni avisa automáticamente. No detener EC2 para ahorrar sin coordinar la interrupción con RRHH.
