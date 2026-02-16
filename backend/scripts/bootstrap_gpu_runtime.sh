#!/bin/sh
set -eu

TORCH_WHEEL="${TORCH_WHEEL:-/app/.cache/torch/torch-2.5.1+cu118-cp312-cp312-linux_x86_64.whl}"
TORCH_DEPS_DIR="${TORCH_DEPS_DIR:-/app/.cache/torch/deps}"

check_torch_cuda() {
  python - <<'PY'
import sys
try:
    import torch
except Exception:
    sys.exit(1)

ok = torch.__version__.endswith("+cu118") and (torch.version.cuda == "11.8")
sys.exit(0 if ok else 1)
PY
}

if check_torch_cuda; then
  echo "[gpu-bootstrap] torch cu118 already present"
  exit 0
fi

if [ ! -f "$TORCH_WHEEL" ]; then
  echo "[gpu-bootstrap] missing torch wheel: $TORCH_WHEEL"
  exit 1
fi

echo "[gpu-bootstrap] installing torch cu118 wheel from local cache"
pip install --no-cache-dir --force-reinstall --no-deps "$TORCH_WHEEL"

if [ -d "$TORCH_DEPS_DIR" ] && ls "$TORCH_DEPS_DIR"/*.whl >/dev/null 2>&1; then
  echo "[gpu-bootstrap] installing cuda dependency wheels from local cache"
  pip install --no-cache-dir --no-deps "$TORCH_DEPS_DIR"/*.whl
else
  echo "[gpu-bootstrap] missing dependency wheels directory: $TORCH_DEPS_DIR"
  exit 1
fi

python - <<'PY'
import torch
print("[gpu-bootstrap] torch:", torch.__version__)
print("[gpu-bootstrap] cuda:", torch.version.cuda, "available:", torch.cuda.is_available())
PY
