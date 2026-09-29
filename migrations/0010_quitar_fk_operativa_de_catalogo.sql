-- La migracion 0008 agrego `creado_por` con una FK hacia `usuario` (tabla operativa) desde una
-- tabla catalogo_ (pensada para persistir siempre, ver tests/conftest.py: excluye "catalogo_%" del
-- TRUNCATE entre pruebas). Esa FK rompe esa invariante: TRUNCATE ... CASCADE sobre `usuario`
-- arrastra en cascada a catalogo_codigo_tipo_dia_fuente aunque este fuera de la lista de truncado
-- por nombre -- hallazgo real: la semilla L/G/D desaparecia despues de correr la suite completa de
-- pruebas. `creado_por` se conserva como referencia blanda (sin FK); las lecturas ya hacen
-- LEFT JOIN con `usuario` y no dependen de la restriccion para funcionar.
ALTER TABLE catalogo_codigo_tipo_dia_fuente DROP CONSTRAINT catalogo_codigo_tipo_dia_fuente_creado_por_fkey;
