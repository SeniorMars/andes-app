#!/usr/bin/env bash
set -euo pipefail

ROOT="${ANDES_DEMO_ROOT:-/home/cjh16/andes-app}"
ENV_PREFIX="${ANDES_DEMO_ENV:-$ROOT/.demo-env}"
LOG_DIR="$ROOT/.demo-logs"
PID_DIR="$ROOT/.demo-pids"
UV="$ENV_PREFIX/bin/uv"
NPM="$ENV_PREFIX/bin/npm"

export PATH="$ENV_PREFIX/bin:$PATH"
export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-http://localhost:8000}"

mkdir -p "$LOG_DIR" "$PID_DIR" "$ROOT/cache" "$ROOT/runs"

require_runtime() {
    if [[ ! -x "$UV" ]]; then
        echo "Missing uv at $UV" >&2
        exit 1
    fi
    if [[ ! -x "$NPM" ]]; then
        echo "Missing npm at $NPM" >&2
        exit 1
    fi
}

is_running() {
    local pid_file="$1"
    [[ -s "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

start_service() {
    local name="$1"
    local workdir="$2"
    shift 2
    local pid_file="$PID_DIR/$name.pid"
    local log_file="$LOG_DIR/$name.log"

    if is_running "$pid_file"; then
        echo "$name already running: pid $(cat "$pid_file")"
        return
    fi

    (
        cd "$workdir"
        nohup "$@" >"$log_file" 2>&1 &
        echo "$!" >"$pid_file"
    )
    echo "started $name: pid $(cat "$pid_file"), log $log_file"
}

stop_service() {
    local name="$1"
    local pid_file="$PID_DIR/$name.pid"
    if ! is_running "$pid_file"; then
        echo "$name not running"
        rm -f "$pid_file"
        return
    fi
    local pid
    pid="$(cat "$pid_file")"
    kill "$pid"
    rm -f "$pid_file"
    echo "stopped $name: pid $pid"
}

status_service() {
    local name="$1"
    local pid_file="$PID_DIR/$name.pid"
    if is_running "$pid_file"; then
        echo "$name: running pid $(cat "$pid_file")"
    else
        echo "$name: stopped"
    fi
}

start_all() {
    require_runtime
    start_service api "$ROOT/backend" "$UV" run andes-api
    start_service worker "$ROOT/backend" "$UV" run andes-worker
    start_service web "$ROOT/web" "$NPM" run start -- -H 127.0.0.1 -p 3000
}

stop_all() {
    stop_service web
    stop_service worker
    stop_service api
}

status_all() {
    status_service api
    status_service worker
    status_service web
    if command -v curl >/dev/null 2>&1; then
        curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 \
            && echo "api health: ok" || echo "api health: unavailable"
        curl -fsS -I http://127.0.0.1:3000 >/dev/null 2>&1 \
            && echo "web health: ok" || echo "web health: unavailable"
    fi
}

case "${1:-status}" in
    start)
        start_all
        ;;
    stop)
        stop_all
        ;;
    restart)
        stop_all
        start_all
        ;;
    status)
        status_all
        ;;
    logs)
        tail -n "${2:-80}" "$LOG_DIR"/api.log "$LOG_DIR"/worker.log "$LOG_DIR"/web.log
        ;;
    *)
        echo "usage: $0 {start|stop|restart|status|logs [lines]}" >&2
        exit 2
        ;;
esac
