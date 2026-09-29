-- Agrupaciones para significados expresos que el catálogo inicial no distinguía.
-- La categoría concreta y su remuneración permanecen en la leyenda local.
INSERT INTO catalogo_estado_asistencia(codigo,nombre,categoria) VALUES
 ('LSG','Licencia sin goce de remuneraciones','LICENCIA'),
 ('PCG','Permiso con goce de remuneraciones','PERMISO'),
 ('OTRO_REPORTADO','Otra categoría explícita de la leyenda local','OTRO')
ON CONFLICT(codigo) DO NOTHING;
