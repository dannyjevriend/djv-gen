#!/bin/sh
# Build the skill's Python environment once, into a per-user cache.
set -eu

VENV=${DRAWTHINGS_VENV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/djv-tools-gen/venv}
SPEC="drawthings-py[ffmpeg]==0.4.0"

find_uv() {
    for candidate in uv "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

find_python() {
    for candidate in python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    # uv installs managed interpreters here even when its own binary is not on PATH.
    for candidate in \
        "$HOME"/.local/share/uv/python/*/bin/python3.13 \
        "$HOME"/.local/share/uv/python/*/bin/python3.12 \
        "$HOME"/.local/share/uv/python/*/bin/python3.11; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    for candidate in \
        /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
        /usr/local/bin/python3.12 /usr/local/bin/python3.11; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    if command -v python3 >/dev/null 2>&1 && \
       python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        command -v python3
        return 0
    fi
    return 1
}

mkdir -p "$(dirname -- "$VENV")"
tmp="${VENV}.tmp.$$"
trap 'rm -rf "$tmp"' EXIT INT TERM

if UV=$(find_uv); then
    "$UV" venv --python 3.12 "$tmp"
    "$UV" pip install --python "$tmp/bin/python" "$SPEC"
else
    PYTHON=$(find_python) || {
        printf '%s\n' "Need uv or Python 3.11+ to install drawthings-py." >&2
        exit 1
    }
    "$PYTHON" -m venv "$tmp"
    "$tmp/bin/python" -m pip install --quiet --disable-pip-version-check "$SPEC"
fi

rm -rf "$VENV"
mv "$tmp" "$VENV"
trap - EXIT INT TERM
