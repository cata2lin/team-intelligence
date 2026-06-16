#!/usr/bin/env bash
# Install rembg for background removal WITHOUT pymatting/numba.
# pymatting -> numba -> llvmlite fails to build on Python 3.13. We never use
# alpha_matting, but rembg imports pymatting at module top level, so we stub it.
# Usage: bash setup_env.sh [path-to-venv]   (default: .venv in CWD)
set -euo pipefail
VENV="${1:-.venv}"
[ -x "$VENV/bin/python" ] || { echo "no venv at $VENV — create one first (uv venv $VENV)"; exit 1; }

uv pip install --python "$VENV" --no-deps rembg
uv pip install --python "$VENV" onnxruntime pooch tqdm opencv-python-headless scipy jsonschema scikit-image pillow numpy

SP="$("$VENV/bin/python" -c 'import site;print(site.getsitepackages()[0])')"
mkdir -p "$SP/pymatting/alpha" "$SP/pymatting/foreground" "$SP/pymatting/util"
printf '%s\n' "def _stub(*a, **k):" "    raise RuntimeError('pymatting stub: alpha_matting unsupported in this env')" > "$SP/pymatting/__init__.py"
echo "from pymatting import _stub as estimate_alpha_cf"        > "$SP/pymatting/alpha/estimate_alpha_cf.py"
echo "from pymatting import _stub as estimate_foreground_ml"   > "$SP/pymatting/foreground/estimate_foreground_ml.py"
echo "from pymatting import _stub as stack_images"             > "$SP/pymatting/util/util.py"
touch "$SP/pymatting/alpha/__init__.py" "$SP/pymatting/foreground/__init__.py" "$SP/pymatting/util/__init__.py"

"$VENV/bin/python" -c "from rembg import remove, new_session; print('✓ rembg ready in $VENV')"
