-- Identificador de acceso independiente del correo; conserva cuentas y credenciales.
ALTER TABLE usuario ADD COLUMN usuario_login varchar(64);
ALTER TABLE usuario ALTER COLUMN email DROP NOT NULL;
ALTER TABLE usuario ADD CONSTRAINT usuario_identificador_requerido
    CHECK (email IS NOT NULL OR usuario_login IS NOT NULL);
ALTER TABLE usuario ADD CONSTRAINT usuario_login_formato
    CHECK (usuario_login IS NULL OR usuario_login ~ '^[a-z0-9][a-z0-9_.-]{2,63}$');
CREATE UNIQUE INDEX usuario_login_unico ON usuario(usuario_login)
    WHERE usuario_login IS NOT NULL;
