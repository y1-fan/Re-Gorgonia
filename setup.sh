#!/usr/bin/env bash
# One-time WSL setup: CUDA build of torch + web/inference deps.
set -euo pipefail
cd "$(dirname "$0")"

VENV="${MEDUSA_VENV:-$HOME/.venvs/medusa}"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install -U pip wheel

# RTX 40xx / CUDA 12.x. Change cu128 if your driver needs another build.
"$VENV/bin/pip" install torch torchvision --index-url https://download.pytorch.org/whl/cu128
"$VENV/bin/pip" install -r requirements.txt

"$VENV/bin/python" - <<'PY'
import torch, transformers, torchvision
print("torch       ", torch.__version__)
print("torchvision ", torchvision.__version__)
print("transformers", transformers.__version__)
print("cuda        ", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu         ", torch.cuda.get_device_name(0))
PY
