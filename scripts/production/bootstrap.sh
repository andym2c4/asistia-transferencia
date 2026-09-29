#!/usr/bin/env bash
# EC2 Ubuntu 24.04 x86_64. No contiene credenciales ni datos del proyecto.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io nginx python3.12-venv postgresql-client-16 curl unzip ca-certificates bubblewrap poppler-utils
# Ubuntu 24.04 exige autorización por ejecutable para namespaces no privilegiados.
# Mantener la restricción global y el aislamiento de la aplicación.
cat > /etc/apparmor.d/asistia-bwrap <<'APPARMOR'
abi <abi/4.0>,
include <tunables/global>
profile asistia_bwrap /usr/bin/bwrap flags=(unconfined) {
    userns,
}
APPARMOR
apparmor_parser -r /etc/apparmor.d/asistia-bwrap
if ! command -v aws >/dev/null; then
    aws_install_dir="$(mktemp -d)"
    curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.37.4.zip -o "$aws_install_dir/aws.zip"
    unzip -q "$aws_install_dir/aws.zip" -d "$aws_install_dir"
    "$aws_install_dir/aws/install"
    rm -rf "$aws_install_dir"
fi
systemctl enable --now docker
id asistia >/dev/null 2>&1 || useradd --system --create-home --home-dir /data/projects/asistia --shell /usr/sbin/nologin asistia
install -d -o asistia -g asistia -m 0750 /data/projects/asistia
install -d -m 0700 /var/lib/asistia /var/lib/asistia/staging
install -d -m 0755 /var/www/acme
python3.12 -m venv /opt/asistia-tools
/opt/asistia-tools/bin/pip install --disable-pip-version-check 'uv==0.12.19' 'certbot==5.4.0'
cat > /etc/nginx/sites-available/asistia <<'NGINX'
server {
    listen 80 default_server;
    server_name _;
    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 503; }
}
NGINX
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/asistia /etc/nginx/sites-enabled/asistia
nginx -t
systemctl enable --now nginx
systemctl reload nginx
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\nMaxRetentionSec=14day\n' > /etc/systemd/journald.conf.d/asistia.conf
systemctl restart systemd-journald
touch /var/lib/asistia/bootstrap-ready
