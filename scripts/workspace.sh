#!/usr/bin/env bash
# Workspace de ASISTIA: consolas remotas o servicios locales según instalación.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

port_busy() {
    [[ -n "$(ss -ltnH 'sport = :8000')" ]]
}

production_configured() {
    # La copia real local verificada tiene prioridad durante la ronda autorizada.
    [[ -f data/production/active.json && ! -f data/production/local-development.json ]]
}

db_command=(docker compose)
db_service=db
if [[ -f data/production/local-development.json ]]; then
    db_command+=( -f docker-compose.local.yml )
    db_service=db_local
    if [[ "${1:-}" != stop ]]; then
        .venv/bin/python scripts/local.py check
    fi
fi

case "${1:-}" in
    prepare)
        if production_configured; then
            echo 'Workspace de desarrollo listo. La base operativa permanece en EC2.'
            exit 0
        fi
        if [[ ! -x .venv/bin/asistia ]]; then
            echo 'Falta .venv/bin/asistia. Consulta README.md para preparar el entorno.' >&2
            exit 1
        fi
        if port_busy; then
            echo 'Puerto 8000 ocupado. Detén la instancia existente antes de abrir este workspace.' >&2
            exit 1
        fi
        # Solo la base operativa; no recrear contenedores ni iniciar db_test.
        "${db_command[@]}" up -d --no-recreate --wait --wait-timeout 30 "$db_service"
        ;;
    web)
        if production_configured; then
            echo 'ASISTIA · web de producción en EC2'
            python3 scripts/production/dev.py status
            printf '%s\n' \
                'Esta es la configuración guardada, no una comprobación de disponibilidad.' \
                'Web y SQL requieren EC2 encendida; abrir el workspace no la enciende.' \
                'Reanudación: docs/runbooks/PRODUCCION_EC2.md'
            exit 0
        fi
        if [[ -z "${TMUX_PANE:-}" ]] || [[ "$(tmux display-message -p -t "$TMUX_PANE" '#{session_name}')" != asistia ]]; then
            echo 'Abre la web con ws asistia para administrarla desde tmux.' >&2
            exit 1
        fi
        if port_busy; then
            echo 'Puerto 8000 ocupado; no se inició otra web.' >&2
            exit 1
        fi
        umask 077
        mkdir -p data/logs
        touch data/logs/workspace-web.log
        chmod 600 data/logs/workspace-web.log
        tmux set-option -t asistia @asistia_web_pane "$TMUX_PANE"
        tmux pipe-pane -o -t "$TMUX_PANE" "cat >> '$PWD/data/logs/workspace-web.log'"
        export PYTHONUNBUFFERED=1
        # Esta rama sirve solo en loopback; producción no usa este lanzador local.
        export ASISTIA_WEB_DEVELOPMENT_EXPORTS=1
        export ASISTIA_WEB_DEVELOPMENT_TOOLS=1
        exec .venv/bin/asistia web servir --host 127.0.0.1 --puerto 8000
        ;;
    logs)
        if production_configured; then
            printf '%s\n' \
                'PostgreSQL · acceso remoto a EC2' \
                'Cuando EC2 esté disponible, abre aquí el túnel:' \
                '  python3 scripts/production/dev.py tunnel' \
                'Después usa SQL en el panel de consola. Ctrl+c cierra el túnel.'
        else
            exec "${db_command[@]}" logs --tail=100 --follow "$db_service"
        fi
        ;;
    console)
        if production_configured; then
            printf '%s\n' \
                'Consola de desarrollo · base operativa en EC2' \
                'Con el túnel abierto, SQL de solo lectura:' \
                '  python3 scripts/production/dev.py sql' \
                'Puedes editar código sin conectar a producción.'
        else
            printf '%s\n' 'Web: http://127.0.0.1:8000' 'Logs web: data/logs/workspace-web.log'
            "${db_command[@]}" ps
            if [[ "$db_service" == db_local ]]; then
                printf '%s\n' \
                    'Datos reales locales; los cambios forman parte de la próxima entrega.' \
                    'SQL: .venv/bin/python scripts/local.py sql' \
                    'Migraciones/DDL: .venv/bin/python scripts/local.py sql --owner'
            fi
        fi
        printf '%s\n' \
            'Cerrar: ./scripts/workspace.sh stop' \
            'Desconectar: prefijo de tmux, d (Ctrl+b por defecto; F12 en Byobu)'
        ;;
    stop)
        tmux has-session -t '=asistia'
        if production_configured; then
            # Cierra también los procesos de estos paneles, sin operar Docker ni EC2.
            tmux kill-session -t '=asistia'
            exit 0
        fi
        tmux display-message -t 'asistia:' 'Deteniendo web y PostgreSQL de ASISTIA...'
        web_pane="$(tmux show-option -qv -t asistia @asistia_web_pane)"
        if [[ -n "$web_pane" ]] && [[ "$(tmux display-message -p -t "$web_pane" '#{session_name}' 2>/dev/null)" == asistia ]]; then
            tmux send-keys -t "$web_pane" C-c
        fi
        for ((attempt = 0; attempt < 40; attempt++)); do
            if ! port_busy; then break; fi
            sleep 0.25
        done
        if port_busy; then
            tmux display-message -t 'asistia:' '8000 sigue ocupado; se conservan la base y la sesión. Revisa el panel web.'
            exit 1
        fi
        stop_services=("$db_service")
        if [[ "$db_service" == db ]]; then stop_services+=(db_test); fi
        if ! "${db_command[@]}" stop "${stop_services[@]}"; then
            tmux display-message -t 'asistia:' 'No se pudo detener Docker; la sesión sigue abierta para revisar.'
            exit 1
        fi
        tmux kill-session -t '=asistia'
        ;;
    *)
        echo 'Uso: scripts/workspace.sh prepare|web|logs|console|stop' >&2
        exit 2
        ;;
esac
