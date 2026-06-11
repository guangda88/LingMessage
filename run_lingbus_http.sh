#!/usr/bin/env bash
# LingBus MCP HTTP Proxy 进程管理器
# 用法：./run_lingbus_http.sh start|stop|status|restart

set -euo pipefail

PORT=9528
PIDFILE="/home/ai/.lingmessage/lingbus_http_proxy.pid"
LOGFILE="/home/ai/.lingmessage/lingbus_http_proxy.log"
PYTHON="/usr/bin/python3"
SCRIPT="/home/ai/lingmessage/run_lingbus_http.py"

# 加载签名密钥（nohup 不继承交互式 shell 的环境变量）
if [ -f "$HOME/.ling_keys.env" ]; then
    set -a
    source "$HOME/.ling_keys.env"
    set +a
fi

get_pid() {
    if [ -f "$PIDFILE" ]; then
        cat "$PIDFILE" 2>/dev/null
    fi
}

is_running() {
    local pid
    pid=$(get_pid)
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

do_start() {
    if is_running; then
        echo "Already running (PID $(get_pid))"
        return 0
    fi

    # Ensure port is free
    fuser -k "${PORT}/tcp" 2>/dev/null || true
    sleep 0.5

    mkdir -p "$(dirname "$PIDFILE")"
    nohup "$PYTHON" "$SCRIPT" >> "$LOGFILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PIDFILE"

    # Wait for startup
    local retries=10
    while [ $retries -gt 0 ]; do
        if kill -0 "$pid" 2>/dev/null && ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
            echo "Started LingBus HTTP Proxy (PID $pid) on :${PORT}"
            return 0
        fi
        retries=$((retries - 1))
        sleep 0.5
    done

    if kill -0 "$pid" 2>/dev/null; then
        echo "Started LingBus HTTP Proxy (PID $pid) but port check uncertain"
        return 0
    else
        echo "FAILED to start. Check $LOGFILE"
        rm -f "$PIDFILE"
        return 1
    fi
}

do_stop() {
    local pid
    pid=$(get_pid)
    if [ -z "$pid" ]; then
        echo "Not running"
        return 0
    fi

    kill "$pid" 2>/dev/null || true
    local retries=10
    while [ $retries -gt 0 ] && kill -0 "$pid" 2>/dev/null; do
        retries=$((retries - 1))
        sleep 0.3
    done

    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
    fi

    fuser -k "${PORT}/tcp" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "Stopped"
}

do_status() {
    if is_running; then
        echo "Running (PID $(get_pid), port :${PORT})"
    else
        echo "Not running"
        return 1
    fi
}

case "${1:-status}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop; sleep 1; do_start ;;
    status)  do_status ;;
    *)       echo "Usage: $0 {start|stop|restart|status}" ;;
esac
