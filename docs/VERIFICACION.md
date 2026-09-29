# Verificación de la transferencia

Fecha: 29/09/2026. Linux x86_64, Python 3.12.14, dependencias exactas de `uv.lock` y PostgreSQL 16 en contenedores nuevos del proyecto Compose `asistia_transferencia`. La comprobación no modificó producción ni consultó documentos nominales para generar fixtures.

| Comprobación | Resultado |
|---|---|
| `uv sync --frozen` en carpeta nueva | Correcto; 30 paquetes instalados |
| Aplicación de migraciones a base vacía | Las 23 migraciones aplicadas |
| Clave propia y operador ficticio | Creados solo en base de desarrollo nueva |
| HTTP `/salud`, ingreso e Inicio autenticado | Correctos; web de comprobación detenida |
| Suite documentada en `DESARROLLO.md`, base `asistia_test_transferencia` en 5555 | **467 pruebas y 8 subpruebas aprobadas, 286,46 segundos** |
| Conversor, SDK y fuentes desde el paquete separado | Preparados en la raíz nueva; visor incluido en la suite |
| Comparación de 259 archivos de fuente con el commit de origen | Idénticos byte a byte |
| Revisión de rutas preparadas para Git | Sin `.env`, `data/`, claves privadas ni paquete separado |
| Búsqueda de secretos conocidos y patrones de credenciales | Sin coincidencias en contenido publicable |
| Enlaces de la documentación nueva y `git diff --check` | Correctos |

Las regresiones contra corpus real, el manifiesto privado de evaluación y la rúbrica académica con dependencias opcionales se describen separadamente en `DESARROLLO.md`. No se afirma que todo `pytest` pase sin esos recursos. La revisión de secretos cubre los valores conocidos y patrones habituales; no demuestra ausencia universal de cualquier dato sensible en cambios futuros.

Al preparar la guía, un primer intento de colección detectó el grupo opcional de pandas requerido por la rúbrica. Otro intento detectó que la prueba de calendario también necesita el conversor: se interrumpió después de 102 aprobadas y un fallo por recurso ausente, se instaló el runtime y se ejecutó nuevamente la suite completa indicada, con el resultado anterior. La comprobación HTTP corrigió la expectativa del verificador de «Salir» a la etiqueta real «Cerrar sesión»; no se cambió código de aplicación.

No se publicaron ni modificaron recursos AWS ni se ejecutó otra migración sobre producción. Los comandos de instalación y pruebas usan puertos 5554/5555 y una web temporal 8080; los servicios de prueba quedan detenidos al finalizar. La base de desarrollo ficticia conserva su volumen local y su acceso de comprobación está únicamente en el paquete privado.
