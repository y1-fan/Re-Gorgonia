#!/usr/bin/env bash
# Start the workbench inside WSL. Windows browser -> http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"

VENV="${MEDUSA_VENV:-$HOME/.venvs/medusa}"
export MEDUSA_WORKSPACE="${MEDUSA_WORKSPACE:-$PWD/workspace}"
PORT="${PORT:-8000}"

if [ ! -x "$VENV/bin/uvicorn" ]; then
  echo "未找到虚拟环境 $VENV，请先运行: bash setup.sh" >&2
  exit 1
fi

echo "MEDUSA workbench"
echo "  model     : ${MEDUSA_MODEL_PATH:-自动（models/.active 或 Medusa0.2Line-4B）}"
echo "  workspace : $MEDUSA_WORKSPACE"
echo "  url       : http://localhost:$PORT"
exec "$VENV/bin/uvicorn" server:app --host 0.0.0.0 --port "$PORT"
