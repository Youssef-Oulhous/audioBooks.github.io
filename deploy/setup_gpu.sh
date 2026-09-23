#!/usr/bin/env bash
# One-time setup on a Linux machine with an NVIDIA GPU (≥24 GB VRAM), e.g. a RunPod / Vast.ai pod.
# Run it from inside the auk-audiobook folder:   bash deploy/setup_gpu.sh [--base]
#   --base   also download AuK (Base): slower, a bit higher quality (default is AuK-Flash only)
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
AUK_DIR="${AUK_DIR:-$HERE/../AuK}"
WITH_BASE=0
[ "${1:-}" = "--base" ] && WITH_BASE=1

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

say "Checking the GPU"
if ! command -v nvidia-smi >/dev/null; then
  echo "No NVIDIA GPU found (nvidia-smi missing). AuK needs a CUDA GPU with ~24 GB of memory." >&2
  exit 1
fi
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')
FREE_GB=$(df -BG --output=avail "$HERE" | tail -1 | tr -dc '0-9')
if [ "${FREE_GB:-0}" -lt 40 ]; then
  echo "Warning: only ${FREE_GB} GB free disk. The model weights need ~25 GB (+7 GB with --base)." >&2
fi

say "Installing uv (Python manager)"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

say "Getting AuK"
if [ ! -d "$AUK_DIR/src/auk" ]; then
  git clone --depth 1 https://github.com/Tencent-Hunyuan/AuK "$AUK_DIR"
fi
AUK_DIR="$(cd "$AUK_DIR" && pwd)"

say "Creating the Python environment"
cd "$HERE/server"
uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -e "$AUK_DIR"
uv pip install --python .venv/bin/python -r requirements.txt "huggingface_hub[cli]"

say "Downloading model weights (AuK-Flash + Qwen2.5-Omni-3B encoder, ~19 GB)"
HF=".venv/bin/hf"
$HF download tencent/AuK-Flash --local-dir "$AUK_DIR/ckpts/AuK-Flash"
$HF download Qwen/Qwen2.5-Omni-3B --local-dir "$AUK_DIR/ckpts/Qwen2.5-Omni-3B"
if [ "$WITH_BASE" = 1 ]; then
  $HF download tencent/AuK --local-dir "$AUK_DIR/ckpts/AuK"
fi

say "Writing server/.env"
if [ ! -f .env ]; then
  TOKEN=$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(9))")
  OFFLOAD=0
  # AuK peaks at ~25 GB without offload and ~17 GB with it (README table)
  [ "${VRAM_MB:-0}" -lt 30000 ] && OFFLOAD=1
  cat > .env <<ENV
AUDIOBOOK_ENGINE=auk
AUK_CKPT_DIR=$AUK_DIR/ckpts
AUK_VARIANT=flash
AUK_CPU_OFFLOAD=$OFFLOAD
AUDIOBOOK_TOKEN=$TOKEN
AUDIOBOOK_DATA=$HERE/server/data
ENV
fi
cat .env

say "Done. Start the server with:  bash deploy/start.sh"
