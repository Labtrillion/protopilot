#!/usr/bin/env bash

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
runtime="$repo_root/.runtime"

prepare_runtime() {
    umask 077
    mkdir -p "$runtime/logs" "$runtime/pids"
    if [[ -f "$runtime/zoro.env" ]]; then
        set -a
        # shellcheck source=/dev/null
        source "$runtime/zoro.env"
        set +a
    fi
}

start_background() {
    local service=$1 marker=$2 old_pid state pid_file log_file lock_fd attempt
    shift 2
    if [[ ! -x "$1" ]]; then
        printf "Missing executable: %s\n" "$1" >&2
        return 1
    fi
    pid_file="$runtime/pids/$service.pid"
    log_file="$runtime/logs/$service.log"

    exec {lock_fd}>"$runtime/pids/$service.lock"
    flock -n "$lock_fd"

    if [[ -s "$pid_file" ]]; then
        read -r old_pid < "$pid_file"
        if [[ "$old_pid" =~ ^[1-9][0-9]*$ ]]; then
            state="$(ps -o stat= -p "$old_pid" | tr -d ' ' || true)"
            if [[ -n "$state" && "$state" != Z* ]]; then
                if ! tr '\0' ' ' < "/proc/$old_pid/cmdline" | grep -Fq -- "$marker"; then
                    printf '%s\n' "PID $old_pid does not match $service. Inspect $pid_file." >&2
                    return 1
                fi
                kill -TERM "$old_pid"
                for ((attempt = 0; attempt < 40; attempt++)); do
                    state="$(ps -o stat= -p "$old_pid" | tr -d ' ' || true)"
                    [[ -z "$state" || "$state" == Z* ]] && break
                    sleep 0.25
                done
                if [[ -n "$state" && "$state" != Z* ]]; then
                    # SGLang can stop its workers while the HTTP process stays alive.
                    if [[ "$service" != zoro ]] || ! zoro_workers_stopped "$old_pid"; then
                        printf '%s\n' "$service (PID $old_pid) is still stopping." >&2
                        return 1
                    fi
                    kill -KILL "$old_pid"
                    for ((attempt = 0; attempt < 20; attempt++)); do
                        state="$(ps -o stat= -p "$old_pid" | tr -d ' ' || true)"
                        [[ -z "$state" || "$state" == Z* ]] && break
                        sleep 0.1
                    done
                    [[ -z "$state" || "$state" == Z* ]] || return 1
                fi
            fi
        fi
    fi

    setsid nohup "$@" >> "$log_file" 2>&1 < /dev/null {lock_fd}>&- &
    printf '%s\n' "$!" > "$pid_file"
    printf '%s\n' "$service PID $! · $log_file"
}

zoro_workers_stopped() {
    local pid=$1
    ps -eo pid=,pgid=,sid=,stat= | awk -v main="$pid" '
        $1 == main && $2 == main && $3 == main { is_session_leader = 1 }
        $1 != main && $3 == main && $4 !~ /^Z/ { worker_alive = 1 }
        END { exit !(is_session_leader && !worker_alive) }
    '
}
