#!/usr/bin/env bash
# Hands-off AWS run for the HUMAN3R backend (spike — see ../HUMAN3R.md), driven
# from your LOCAL machine (Git Bash / macOS, AWS CLI configured).
#
# Run from the repo root:   bash aws/launch_human3r.sh
#
# This is the Human3R twin of aws/launch.sh (GVHMR). It uploads inputs to S3, then
# launches a GPU instance whose user-data installs Human3R, runs your clip through
# run_pipeline.py --backend human3r, uploads <clip>.bvh back to S3, and SELF-
# TERMINATES. It ALSO caches the raw per-frame smpl/*.npz + camera/*.npz so the
# adapter can be re-validated/re-run WITHOUT another GPU launch.
#
# ============================ COST SAFETY (read me) ==========================
# This script is UNTESTED until its first real run. To make sure a bad run can
# never run up a bill, it uses THREE independent guards:
#   1. instance-initiated-shutdown-behavior=terminate  -> any shutdown TERMINATES
#      the box (no lingering stopped instance, EBS DeleteOnTermination=true).
#   2. finish() EXIT trap -> shuts down on success OR any script error.
#   3. A hard WATCHDOG timer on the box -> force-shutdown after MAX_MINUTES even
#      if a step HANGS (this is the guard the EXIT trap can't provide).
# It also REFUSES to launch if a tinydancer instance is already running, so you
# can't accidentally stack boxes. Worst-case cost is bounded by MAX_MINUTES:
# g5.xlarge ~= $1/hr, so a 75-min cap is ~$1.25 worst case; typical run ~$0.40.
# =============================================================================
set -euo pipefail

############################## EDIT THESE ##############################
REGION="us-east-1"
BUCKET="${BUCKET:-tinydancer-CHANGE-ME}"   # globally-unique; append your acct id
INSTANCE_TYPE="${INSTANCE_TYPE:-g5.xlarge}" # A10G 24GB: fits 896L. g4dn.xlarge
                                            # (T4 16GB) works only with a 672* ckpt.
MAX_MINUTES="${MAX_MINUTES:-75}"            # hard self-terminate cap (cost guard)
HUMAN3R_CKPT="${HUMAN3R_CKPT:-human3r_896L.pth}"  # 672S.pth = fastest/least VRAM

# Local input files (relative to where you run this):
SMPLX_PATH="./SMPLX_NEUTRAL.npz"            # license-gated; you already have this
VIDEO_PATH="${VIDEO_PATH:-./buyit.mp4}"     # clip to process
RUNPIPE_PATH="./run_pipeline.py"
#######################################################################

if [ "$BUCKET" = "tinydancer-CHANGE-ME" ]; then
  echo "Set BUCKET first: BUCKET=tinydancer-<your-account-id> bash aws/launch_human3r.sh" >&2
  exit 1
fi

VIDEO_FILE="$(basename "$VIDEO_PATH")"
VIDEO_STEM="${VIDEO_FILE%.*}"
OUT_BVH="${VIDEO_STEM}.human3r.bvh"          # distinct from the GVHMR <clip>.bvh

# --- live watcher: stream run.log here until the BVH lands, then download it ---
# Re-attach to an in-progress run without relaunching:  WATCH_ONLY=1 bash aws/launch_human3r.sh
watch_run() {
  local bucket="$1" shown=0 total live
  echo ""
  echo ">>> Following s3://$bucket/run.log live — Ctrl-C to stop watching (the box keeps running)."
  echo ""
  while true; do
    if aws s3 cp "s3://$bucket/run.log" /tmp/td_watch.log >/dev/null 2>&1; then
      total=$(wc -l < /tmp/td_watch.log 2>/dev/null || echo 0)
      if [ "${total:-0}" -gt "$shown" ]; then
        sed -n "$((shown+1)),${total}p" /tmp/td_watch.log
        shown=$total
      fi
    fi
    if aws s3 ls "s3://$bucket/$OUT_BVH" >/dev/null 2>&1; then
      echo ""
      echo "==================================================================="
      echo " DONE — $OUT_BVH is ready. Downloading to $(pwd) ..."
      aws s3 cp "s3://$bucket/$OUT_BVH" . || true
      echo ""
      echo " Raw params cached for GPU-free re-runs / validation:"
      echo "   s3://$bucket/${VIDEO_STEM}.human3r_raw.tgz  (smpl/ + camera/ .npz)"
      echo ""
      echo " Build the Blender armature locally:"
      echo '   "/c/Program Files/Blender Foundation/Blender 5.1/blender.exe" \'
      echo "       --background --python blender_import.py -- $OUT_BVH ${VIDEO_STEM}.human3r.blend"
      echo "==================================================================="
      return 0
    fi
    live=$(aws ec2 describe-instances --region "$REGION" \
      --filters "Name=tag:Name,Values=tinydancer-human3r" "Name=instance-state-name,Values=running,pending" \
      --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null || true)
    if [ -z "$live" ]; then
      if aws s3 cp "s3://$bucket/run.log" /tmp/td_watch.log >/dev/null 2>&1; then
        total=$(wc -l < /tmp/td_watch.log 2>/dev/null || echo 0)
        [ "${total:-0}" -gt "$shown" ] && sed -n "$((shown+1)),${total}p" /tmp/td_watch.log
      fi
      if aws s3 ls "s3://$bucket/$OUT_BVH" >/dev/null 2>&1; then
        aws s3 cp "s3://$bucket/$OUT_BVH" . || true; return 0
      fi
      echo ""
      echo "==================================================================="
      echo " Instance terminated WITHOUT producing $OUT_BVH — see log above for the error."
      echo " Raw inference output (if any): s3://$bucket/${VIDEO_STEM}.human3r_raw.tgz"
      echo "==================================================================="
      return 1
    fi
    sleep 30
  done
}

if [ "${WATCH_ONLY:-0}" = "1" ]; then
  watch_run "$BUCKET"; exit $?
fi

# --- preflight (fail fast, BEFORE any AWS spend) -----------------------------
aws sts get-caller-identity >/dev/null || { echo "AWS CLI not configured (aws configure)."; exit 1; }
for f in "$SMPLX_PATH" "$VIDEO_PATH" "$RUNPIPE_PATH"; do
  if [ ! -f "$f" ]; then
    echo "Missing input file: $f"
    [ "$f" = "$SMPLX_PATH" ] && echo "  -> download SMPLX_NEUTRAL.npz from https://smpl-x.is.tue.mpg.de/ (free account)."
    exit 1
  fi
done

# --- COST GUARD: refuse to launch if a tinydancer box is already running -----
EXISTING=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=tinydancer,tinydancer-human3r" \
            "Name=instance-state-name,Values=running,pending" \
  --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null || true)
if [ -n "$EXISTING" ]; then
  echo "A tinydancer instance is already running: $EXISTING" >&2
  echo "Refusing to launch another (cost safety). Attach with WATCH_ONLY=1, or" >&2
  echo "terminate it: aws ec2 terminate-instances --region $REGION --instance-ids $EXISTING" >&2
  exit 1
fi

# --- bucket + upload inputs --------------------------------------------------
aws s3 mb "s3://$BUCKET" --region "$REGION" 2>/dev/null || true
echo "Uploading inputs to s3://$BUCKET ..."
aws s3 cp "$SMPLX_PATH"   "s3://$BUCKET/SMPLX_NEUTRAL.npz"
aws s3 cp "$VIDEO_PATH"   "s3://$BUCKET/$VIDEO_FILE"
aws s3 cp "$RUNPIPE_PATH" "s3://$BUCKET/run_pipeline.py"
# NOTE (body models): Human3R may need more than SMPLX_NEUTRAL.npz. If the run
# fails with a missing-model error, run Human3R's scripts/fetch_smplx.sh on a
# logged-in machine, then:  tar czf human3r_models.tgz models/  (so it holds
# models/smplx/*, models/smpl/*) and upload once:
#   aws s3 cp human3r_models.tgz s3://$BUCKET/human3r_models.tgz
# The box prefers that tarball over the single SMPLX_NEUTRAL.npz.

# Clear stale outputs for THIS clip (keep caches + other clips' results).
aws s3 rm "s3://$BUCKET/run.log" 2>/dev/null || true
aws s3 rm "s3://$BUCKET/$OUT_BVH" 2>/dev/null || true

# --- IAM role (shared with the GVHMR launcher) -------------------------------
ROLE="tinydancer-ec2-role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  echo "Creating IAM role $ROLE ..."
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  echo "Waiting for IAM propagation ..."; sleep 20
fi

# --- latest Deep Learning AMI (Ubuntu 22.04; has conda + NVIDIA driver) -------
AMI=$(aws ec2 describe-images --region "$REGION" --owners amazon \
  --filters "Name=name,Values=Deep Learning OSS Nvidia Driver AMI GPU PyTorch*Ubuntu 22.04*" \
            "Name=state,Values=available" \
  --query 'reverse(sort_by(Images,&CreationDate))[0].ImageId' --output text)
if [ -z "$AMI" ] || [ "$AMI" = "None" ]; then
  echo "No Deep Learning AMI found in $REGION. Check EC2 console -> AMIs -> Public images."
  exit 1
fi
echo "Using AMI: $AMI"

# --- user-data that runs on the box at boot ----------------------------------
UD="aws/_userdata_human3r.gen.sh"
cat > "$UD" <<'USERDATA'
#!/bin/bash
set -xeuo pipefail
BUCKET="__BUCKET__"
REGION="__REGION__"
VIDEO_FILE="__VIDEO_FILE__"
VIDEO_STEM="__VIDEO_STEM__"
OUT_BVH="__OUT_BVH__"
CKPT="__HUMAN3R_CKPT__"
MAX_MINUTES=__MAX_MINUTES__
export AWS_DEFAULT_REGION="$REGION"
LOG=/var/log/tinydancer.log
exec > >(tee "$LOG") 2>&1

push_log(){ aws s3 cp "$LOG" "s3://$BUCKET/run.log" >/dev/null 2>&1 || true; }

finish(){
  push_log
  # Cache the small raw params (smpl/ + camera/ only — NOT the big depth/color
  # dumps) so we can re-run the adapter / validate WITHOUT another GPU launch.
  RAW="/home/ubuntu/Human3R/outputs/human3r/$VIDEO_STEM"
  if [ -d "$RAW" ]; then
    tar czf /tmp/h3r_raw.tgz -C "$RAW" smpl camera 2>/dev/null \
      || tar czf /tmp/h3r_raw.tgz -C "$RAW" . 2>/dev/null || true
    aws s3 cp /tmp/h3r_raw.tgz "s3://$BUCKET/${VIDEO_STEM}.human3r_raw.tgz" || true
  fi
  shutdown -h now
}
trap finish EXIT

# COST-SAFETY WATCHDOG: independent of the main script. If ANY step hangs
# (compile, HF download, inference), this force-shuts-down the box after
# MAX_MINUTES. shutdown == terminate here, so a hung box can't run up the bill.
( sleep $((MAX_MINUTES*60)); echo "WATCHDOG: exceeded ${MAX_MINUTES}m — forcing shutdown."; push_log; shutdown -h now ) &
# Heartbeat: push the log to S3 every 60s so you can watch progress live.
( while true; do sleep 60; push_log; done ) &

apt-get update -y -qq || true
apt-get install -y -qq git build-essential || true

# Human3R needs Python 3.11 + CUDA 12.4 + nvcc (to compile curope/gsplat). Build
# a clean conda env for determinism (the watchdog bounds the extra setup time).
source /opt/conda/etc/profile.d/conda.sh
set +u                                   # conda's own scripts trip `set -u`
conda create -y -n human3r python=3.11
conda activate human3r
set -u
conda install -y pytorch torchvision pytorch-cuda=12.4 -c pytorch -c nvidia
conda install -y -c nvidia cuda-toolkit=12.4       # provides nvcc for ext builds
export CUDA_HOME="$CONDA_PREFIX"
python -m pip install -q --upgrade pip
python -m pip install -q awscli "huggingface_hub[cli]"

cd /home/ubuntu
git clone https://github.com/fanegg/Human3R.git
cd Human3R
python -m pip install -r requirements.txt
python -m pip install "git+https://github.com/nerfstudio-project/gsplat.git"
# Compile the RoPE CUDA kernels (REQUIRED — inference fails without them).
( cd src/croco/models/curope && python setup.py build_ext --inplace )

mkdir -p src src/models/smplx src/models/smpl

# Checkpoint: S3 cache first (skip the 4.7GB HF pull on re-runs), else Hugging Face.
if aws s3 ls "s3://$BUCKET/human3r_ckpt/$CKPT" >/dev/null 2>&1; then
  echo "Using cached checkpoint from S3."
  aws s3 cp "s3://$BUCKET/human3r_ckpt/$CKPT" "src/$CKPT"
else
  huggingface-cli download faneggg/human3r "$CKPT" --local-dir ./src
  aws s3 cp "src/$CKPT" "s3://$BUCKET/human3r_ckpt/$CKPT" || true
fi
test -f "src/$CKPT" || { echo "MISSING Human3R checkpoint src/$CKPT"; exit 1; }

# Body models (license-gated, staged by you). Prefer a full models tarball;
# fall back to the single SMPLX_NEUTRAL.npz you already stage for GVHMR.
if aws s3 ls "s3://$BUCKET/human3r_models.tgz" >/dev/null 2>&1; then
  echo "Using staged human3r_models.tgz."
  aws s3 cp "s3://$BUCKET/human3r_models.tgz" /tmp/hm.tgz
  tar xzf /tmp/hm.tgz -C src/          # expects a models/ tree -> src/models/
else
  aws s3 cp "s3://$BUCKET/SMPLX_NEUTRAL.npz" src/models/smplx/SMPLX_NEUTRAL.npz
fi
test -f src/models/smplx/SMPLX_NEUTRAL.npz \
  || { echo "MISSING src/models/smplx/SMPLX_NEUTRAL.npz — stage body models (see launcher notes)"; exit 1; }

aws s3 cp "s3://$BUCKET/run_pipeline.py" run_pipeline.py
aws s3 cp "s3://$BUCKET/$VIDEO_FILE" "$VIDEO_FILE"

# Human3R inference -> world-grounded SMPL-X -> BVH (one feed-forward model).
python run_pipeline.py "$VIDEO_FILE" --backend human3r \
  --human3r-model "src/$CKPT" --out "$OUT_BVH"
test -f "$OUT_BVH" || { echo "run_pipeline produced no BVH"; exit 1; }
aws s3 cp "$OUT_BVH" "s3://$BUCKET/$OUT_BVH"
echo "DONE: s3://$BUCKET/$OUT_BVH"
USERDATA
# Write via a temp file rather than `sed -i`, whose syntax differs on macOS (BSD sed).
sed -e "s|__BUCKET__|$BUCKET|g" -e "s|__REGION__|$REGION|g" \
    -e "s|__VIDEO_FILE__|$VIDEO_FILE|g" -e "s|__VIDEO_STEM__|$VIDEO_STEM|g" \
    -e "s|__OUT_BVH__|$OUT_BVH|g" -e "s|__HUMAN3R_CKPT__|$HUMAN3R_CKPT|g" \
    -e "s|__MAX_MINUTES__|$MAX_MINUTES|g" "$UD" > "$UD.tmp" && mv "$UD.tmp" "$UD"

# --- launch (retry while the IAM profile finishes propagating) ---------------
ID=""
for i in 1 2 3 4 5; do
  ID=$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI" --instance-type "$INSTANCE_TYPE" \
    --iam-instance-profile "Name=$ROLE" \
    --instance-initiated-shutdown-behavior terminate \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"DeleteOnTermination":true}}]' \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=tinydancer-human3r}]' \
    --user-data "file://$UD" --count 1 \
    --query 'Instances[0].InstanceId' --output text 2>aws/_run.err) && break
  echo "run-instances attempt $i failed; retrying in 15s ..."
  cat aws/_run.err; sleep 15
done

echo ""
echo "Launched: ${ID:-<failed>}  (Human3R -> $VIDEO_FILE -> $OUT_BVH -> self-terminates; ${MAX_MINUTES}m hard cap)"
if [ -z "${ID:-}" ] || [ "${ID:-}" = "None" ]; then
  echo "Launch failed — not watching. Check the error above."
  exit 1
fi

# Auto-follow to completion and download the BVH. Ctrl-C is safe (the box keeps
# running AND still self-terminates; re-attach with WATCH_ONLY=1 bash aws/launch_human3r.sh).
watch_run "$BUCKET"
