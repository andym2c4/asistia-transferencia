-- DT05, parte de preservacion: `nexus_registro.cod_mod_ie_raw` estaba declarada `character(7)`,
-- un ancho fijo que contradice su proposito. Una columna `_raw` existe para conservar lo que la
-- fuente entrego, y un `character(7)` no puede: rellena con espacios a la derecha lo mas corto y
-- obliga al importador a recortar lo mas largo (`str(...)[:7].ljust(7)[:7]`). Justo cuando el valor
-- no normaliza -- que es cuando mas falta hace saber que traia el archivo -- el original se perdia.
--
-- Al corte de esta migracion ninguna de las 4282 filas de `db` tenia relleno (todas resolvieron a
-- un cod_mod de 7 digitos), asi que el camino de perdida estaba latente, no consumado. El cambio a
-- `text` es ampliador: ningun valor existente se altera. Se hace ademas un TRIM de los valores ya
-- guardados, porque `character(7)` los devuelve rellenados con espacios a la derecha y eso si
-- cambiaria una comparacion de texto exacta.
--
-- Ver docs/TECH_DEBT.md DT05 y docs/decisions/. No toca ninguna otra tabla.

ALTER TABLE nexus_registro
    ALTER COLUMN cod_mod_ie_raw TYPE text USING btrim(cod_mod_ie_raw);

COMMENT ON COLUMN nexus_registro.cod_mod_ie_raw IS
    'Valor de codigo modular tal como lo entrego la fuente, sin recortar ni rellenar. Si no '
    'normaliza a 7 digitos significativos, `institucion_educativa_id` queda NULL y esta columna '
    'conserva el original para poder diagnosticarlo.';
