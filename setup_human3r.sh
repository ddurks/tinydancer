#!/usr/bin/env bash
# Human3R setup for a Linux + NVIDIA GPU box (SPIKE — see HUMAN3R.md).
#
# Human3R (https://github.com/fanegg/Human3R) replaces GVHMR's 4-stage
# YOLO->ViTPose->HMR2->GVHMR chain with ONE feed-forward model that outputs
# world-grounded SMPL-X directly. This mirrors setup.sh (the GVHMR equivalent):
# a self-hosted GPU box you drive by hand. Run once per environment.
#
# Requirements per upstream: Python 3.11, CUDA 12.4, a CUDA GPU (~18 GB VRAM for
# the 896L model per third-party reports; use a 672* checkpoint for less).
set -euo pipefail

# 1. Clone Human3R.
if [ ! -d Human3R ]; then
    git clone https://github.com/fanegg/Human3R.git
fi
cd Human3R

# 2. Python env. Human3R targets Python 3.11 + CUDA 12.4. With conda:
#    conda create -y -n human3r python=3.11 && conda activate human3r
#    conda install -y pytorch torchvision pytorch-cuda=12.4 -c pytorch -c nvidia
pip install -r requirements.txt
# llvm-openmp<16 avoids an OpenMP clash on some stacks (conda only; harmless to skip).
conda install -y 'llvm-openmp<16' 2>/dev/null || true
pip install git+https://github.com/nerfstudio-project/gsplat.git

# 3. Compile the RoPE CUDA kernels (REQUIRED — inference fails without them).
( cd src/croco/models/curope/ && python setup.py build_ext --inplace )

# 4. Model checkpoint from Hugging Face (~4.7 GB). 896L = best quality; swap for
#    human3r_672S.pth for the fastest / lowest-VRAM variant.
CKPT="${HUMAN3R_CKPT:-human3r_896L.pth}"
mkdir -p src
pip install -q -U "huggingface_hub[cli]"
if [ ! -f "src/$CKPT" ]; then
    echo "Downloading Human3R checkpoint $CKPT ..."
    huggingface-cli download faneggg/human3r "$CKPT" --local-dir ./src
fi

# 5. Body models — license-gated. fetch_smplx.sh pulls SMPL-X + SMPL after you
#    provide registration credentials; the public regressors come down automatically.
#    Register first: https://smpl-x.is.tue.mpg.de/  and  https://smpl.is.tue.mpg.de/
if [ ! -f src/models/smplx/SMPLX_NEUTRAL.npz ]; then
    bash scripts/fetch_smplx.sh || {
        echo ""
        echo "================================================================"
        echo "  ACTION REQUIRED: body models (license-gated)"
        echo "  fetch_smplx.sh needs your SMPL-X / SMPL registration login."
        echo "  Expected afterwards:"
        echo "    src/models/smplx/SMPLX_{NEUTRAL,MALE,FEMALE}.npz"
        echo "    src/models/smpl/SMPL_{NEUTRAL,MALE,FEMALE}.pkl"
        echo "================================================================"
    }
fi

echo ""
echo "Setup done. 1) Sanity-check Human3R's own demo:"
echo "    python demo.py --model_path src/$CKPT --size 512 \\"
echo "        --seq_path examples/*.mp4 --save --output_dir tmp \\"
echo "        --subsample 1 --use_ttt3r --vis_threshold 2 \\"
echo "        --downsample_factor 1 --reset_interval 100"
echo "2) Convert to BVH with our adapter (run from inside Human3R/):"
echo "    python ../run_pipeline.py ../buyit.mp4 --backend human3r --out ../buyit.bvh"
