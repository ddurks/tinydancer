#!/usr/bin/env bash
# GVHMR setup for a Linux + NVIDIA GPU box. Run once per environment.
# For the fully-automated AWS path, use aws/launch.sh instead — this script is for
# a self-hosted GPU box you drive by hand (AWS g4dn/g5, a workstation, etc.).
set -euo pipefail

# 1. Clone GVHMR (with submodules — it vendors ViTPose / HMR2 / DPVO).
if [ ! -d GVHMR ]; then
    git clone https://github.com/zju3dv/GVHMR.git --recursive
fi
cd GVHMR

# 2. Python env. GVHMR targets Python 3.10. If you use conda:
#    conda create -y -n gvhmr python=3.10 && conda activate gvhmr
# Install the package and its deps (PyTorch per GVHMR's requirements.txt).
pip install -r requirements.txt
pip install -e .
# NOTE: DPVO is NOT required — GVHMR uses its built-in SimpleVO by default, and
# we run the demo with -s (static camera) which skips visual odometry entirely.

# 3. Pretrained checkpoints (GVHMR weights, HMR2, ViTPose, YOLO) — public Google
# Drive folder, pulled headlessly with gdown. Expected tree afterwards:
#   inputs/checkpoints/{gvhmr,hmr2,vitpose,yolo,dpvo}/...
mkdir -p inputs/checkpoints
pip install -q gdown
if [ ! -f inputs/checkpoints/gvhmr/gvhmr_siga24_release.ckpt ]; then
    echo "Downloading GVHMR checkpoints (~a few GB)..."
    gdown --folder https://drive.google.com/drive/folders/1eebJ13FUEXrKBawHpJroW0sNSxLjh9xD \
        -O inputs/checkpoints
    # gdown --folder may nest under a folder name; verify the tree matches:
    #   inputs/checkpoints/gvhmr/gvhmr_siga24_release.ckpt  (move files up if nested)
fi

# 4. Body model — SMPL-X NEUTRAL, license-gated (manual download).
mkdir -p inputs/checkpoints/body_models/smplx
if [ ! -f inputs/checkpoints/body_models/smplx/SMPLX_NEUTRAL.npz ]; then
    echo ""
    echo "================================================================"
    echo "  ACTION REQUIRED (2/2): SMPL-X body model"
    echo "  Register at https://smpl-x.is.tue.mpg.de/  (free account)"
    echo "  Download SMPL-X v1.1 and place the neutral .npz at:"
    echo "    GVHMR/inputs/checkpoints/body_models/smplx/SMPLX_NEUTRAL.npz"
    echo "================================================================"
fi

echo ""
echo "Setup steps done. Verify with GVHMR's own demo first:"
echo "    python tools/demo/demo.py --video=docs/example_video/tennis.mp4 -s"
echo "Then run our converter:"
echo "    python ../run_pipeline.py ../buyit.mp4 --out ../buyit.bvh"
