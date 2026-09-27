#!/bin/sh
set -eu
VBT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$VBT_ROOT"
UV_PROJECT_ENVIRONMENT="$HOME/Library/Application Support/VirtualBiotech/venv" uv sync --locked --extra dev
