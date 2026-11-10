#!/usr/bin/env bash
# Hands-off AWS run, driven from your LOCAL machine (macOS/Linux bash or Git Bash,
# AWS CLI configured).
#
# Run from the repo root:   bash aws/launch.sh
#
# It uploads your local inputs to S3, then launches a GPU instance whose user-data
# installs GVHMR, runs your clip, uploads <clip-stem>.bvh back to S3, and SELF-
# TERMINATES. It then follows run.log live and downloads the BVH when it lands.
#
# Default clip is buyit.mp4. To run a different one:
#   VIDEO_PATH=./blackandwhite.mp4 bash aws/launch.sh
#
# The ONLY manual prerequisite (license, cannot be scripted): download
# SMPLX_NEUTRAL.npz once from https://smpl-x.is.tue.mpg.de/ and put its path below.
set -euo pipefail

############################## EDIT THESE ##############################
REGION="us-east-1"
# S3 bucket names are GLOBALLY unique. Append your AWS account id to avoid
# collisions, e.g. tinydancer-123456789012 (find it: aws sts get-caller-identity
# --query Account --output text). Override per-run: BUCKET=my-bucket bash aws/launch.sh
BUCKET="${BUCKET:-tinydancer-CHANGE-ME}"
INSTANCE_TYPE="g5.xlarge"              # or g4dn.xlarge (cheaper, slower)

# Local input files to upload (relative to where you run this):
SMPLX_PATH="./SMPLX_NEUTRAL.npz"       # you downloaded this (license-gated)
# Clip to process. Override per-run:  VIDEO_PATH=./buyit.mp4 bash aws/launch.sh
VIDEO_PATH="${VIDEO_PATH:-./buyit.mp4}"
RUNPIPE_PATH="./run_pipeline.py"
#######################################################################

if [ "$BUCKET" = "tinydancer-CHANGE-ME" ]; then
  echo "Set BUCKET first: edit aws/launch.sh (or run BUCKET=tinydancer-<your-account-id> bash aws/launch.sh)." >&2
  exit 1
fi

# Derived names: each clip gets its own BVH + cached .pt so runs don't clobber
# each other and you can compare results across clips.
VIDEO_FILE="$(basename "$VIDEO_PATH")"
VIDEO_STEM="${VIDEO_FILE%.*}"
OUT_BVH="${VIDEO_STEM}.bvh"

# --- live watcher: stream run.log here until the BVH lands, then download it ---
# Re-watch an in-progress run without relaunching:  WATCH_ONLY=1 bash aws/launch.sh
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
      echo " Build the Blender armature locally (blender = your Blender executable):"
      echo "   blender --background --python blender_import.py -- $OUT_BVH ${VIDEO_STEM}.blend"
      echo "==================================================================="
      return 0
    fi
    live=$(aws ec2 describe-instances --region "$REGION" \
      --filters "Name=tag:Name,Values=tinydancer" "Name=instance-state-name,Values=running,pending" \
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
      echo " Raw inference output (if any) preserved at: s3://$bucket/${VIDEO_STEM}.hmr4d_results.pt"
      echo "==================================================================="
      return 1
    fi
    sleep 30
  done
}

if [ "${WATCH_ONLY:-0}" = "1" ]; then
  watch_run "$BUCKET"; exit $?
fi

# --- preflight ---------------------------------------------------------------
aws sts get-caller-identity >/dev/null || { echo "AWS CLI not configured (aws configure)."; exit 1; }
for f in "$SMPLX_PATH" "$VIDEO_PATH" "$RUNPIPE_PATH"; do
  if [ ! -f "$f" ]; then
    echo "Missing input file: $f"
    [ "$f" = "$SMPLX_PATH" ] && echo "  -> download SMPLX_NEUTRAL.npz from https://smpl-x.is.tue.mpg.de/ (free account)."
    exit 1
  fi
done

# --- bucket + upload inputs --------------------------------------------------
aws s3 mb "s3://$BUCKET" --region "$REGION" 2>/dev/null || true
echo "Uploading inputs to s3://$BUCKET ..."
aws s3 cp "$SMPLX_PATH"   "s3://$BUCKET/SMPLX_NEUTRAL.npz"
aws s3 cp "$VIDEO_PATH"   "s3://$BUCKET/$VIDEO_FILE"
aws s3 cp "$RUNPIPE_PATH" "s3://$BUCKET/run_pipeline.py"

# Clear stale outputs for THIS clip from a previous run (keep the
# checkpoints.tar.gz cache and other clips' BVHs).
aws s3 rm "s3://$BUCKET/run.log" 2>/dev/null || true
aws s3 rm "s3://$BUCKET/$OUT_BVH" 2>/dev/null || true

# --- IAM role so the instance can read/write the bucket ----------------------
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

# --- latest Deep Learning AMI (PyTorch, Ubuntu 22.04) ------------------------
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
# Quoted heredoc (no local expansion); the __PLACEHOLDERS__ are filled by sed below.
# Written inside the repo (not /tmp) so the Windows aws.exe can read it via a
# relative file:// path when run from Git Bash.
UD="aws/_userdata.gen.sh"
cat > "$UD" <<'USERDATA'
#!/bin/bash
set -xeuo pipefail
BUCKET="__BUCKET__"
REGION="__REGION__"
VIDEO_FILE="__VIDEO_FILE__"
VIDEO_STEM="__VIDEO_STEM__"
OUT_BVH="__OUT_BVH__"
export AWS_DEFAULT_REGION="$REGION"
LOG=/var/log/tinydancer.log
exec > >(tee "$LOG") 2>&1

finish(){
  aws s3 cp "$LOG" "s3://$BUCKET/run.log" || true
  # Preserve the raw GPU inference output (if produced) so a converter bug never
  # forces a full GPU re-run — we can re-convert from this .pt cheaply. Keyed per
  # clip so comparison runs don't overwrite each other.
  PT=/home/ubuntu/GVHMR/outputs/demo/$VIDEO_STEM/hmr4d_results.pt
  [ -f "$PT" ] && aws s3 cp "$PT" "s3://$BUCKET/${VIDEO_STEM}.hmr4d_results.pt" || true
  shutdown -h now
}
trap finish EXIT
# heartbeat: push the log to S3 every 60s so you can watch progress live
( while true; do sleep 60; aws s3 cp "$LOG" "s3://$BUCKET/run.log" >/dev/null 2>&1 || true; done ) &

# This AMI's system Python is 3.10 (matches GVHMR's cp310 wheels). Just make
# `python` resolve to it (the AMI only ships `python3`), and pip into it.
ln -sf "$(command -v python3)" /usr/local/bin/python
python --version
# GVHMR's body_model.py has a stray `from turtle import forward`, which drags in
# tkinter. Headless boxes lack it; install so the import resolves like on a desktop.
apt-get update -y -qq || true
apt-get install -y -qq python3-tk
python -m pip install -q --upgrade pip
python -m pip install -q awscli

cd /home/ubuntu
git clone https://github.com/zju3dv/GVHMR.git --recursive
cd GVHMR

python -m pip install torch==2.3.0 torchvision==0.18.0 --index-url https://download.pytorch.org/whl/cu121
# chumpy (a SMPL-era dep) has an ancient setup.py that does `import pip`, which
# fails under PEP517 build isolation. Install it first WITHOUT isolation so it
# sees the real pip; then the requirements pass finds it satisfied and skips it.
python -m pip install --no-build-isolation chumpy
python -m pip install -r requirements.txt
python -m pip install -e .
python -c "import pytorch3d" 2>/dev/null || \
  FORCE_CUDA=1 python -m pip install "git+https://github.com/facebookresearch/pytorch3d.git@stable"

mkdir -p inputs/checkpoints inputs/checkpoints/body_models/smplx inputs/checkpoints/yolo

# --- checkpoints: S3 cache first, else the Hugging Face mirror --------------
if aws s3 ls "s3://$BUCKET/checkpoints.tar.gz" >/dev/null 2>&1; then
  echo "Using cached checkpoints from S3."
  aws s3 cp "s3://$BUCKET/checkpoints.tar.gz" /tmp/ck.tgz
  tar xzf /tmp/ck.tgz -C inputs/checkpoints
else
  # Pull from the Hugging Face mirror (no Google-Drive rate limits). Its layout
  # mirrors inputs/checkpoints/{gvhmr,hmr2,vitpose,yolo,dpvo}/ exactly.
  python -m pip install -q -U huggingface_hub
  python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="camenduru/GVHMR",
    local_dir="inputs/checkpoints",
    allow_patterns=["gvhmr/*", "hmr2/*", "vitpose/*", "yolo/*", "dpvo/*"],
)
print("HF checkpoints downloaded.")
PY
  # YOLO fallback from Ultralytics if the mirror lacks it.
  [ -f inputs/checkpoints/yolo/yolov8x.pt ] || \
    curl -fSL https://github.com/ultralytics/assets/releases/download/v8.1.0/yolov8x.pt \
      -o inputs/checkpoints/yolo/yolov8x.pt
fi

# Verify the checkpoints the demo actually needs; fail loudly (-> run.log) if not.
test -f inputs/checkpoints/gvhmr/gvhmr_siga24_release.ckpt || { echo "MISSING gvhmr ckpt"; exit 1; }
ls inputs/checkpoints/hmr2/*.ckpt   >/dev/null 2>&1 || { echo "MISSING hmr2 ckpt"; exit 1; }
ls inputs/checkpoints/vitpose/*.pth >/dev/null 2>&1 || { echo "MISSING vitpose ckpt (pre-stage checkpoints.tar.gz to S3 if the mirror lacks it)"; exit 1; }
test -f inputs/checkpoints/yolo/yolov8x.pt || { echo "MISSING yolo"; exit 1; }

# Cache the complete set to S3 so future runs skip the download entirely.
aws s3 ls "s3://$BUCKET/checkpoints.tar.gz" >/dev/null 2>&1 || {
  tar czf /tmp/ck.tgz -C inputs/checkpoints gvhmr hmr2 vitpose yolo dpvo 2>/dev/null || \
  tar czf /tmp/ck.tgz -C inputs/checkpoints gvhmr hmr2 vitpose yolo
  aws s3 cp /tmp/ck.tgz "s3://$BUCKET/checkpoints.tar.gz" || true
}

aws s3 cp "s3://$BUCKET/SMPLX_NEUTRAL.npz" inputs/checkpoints/body_models/smplx/SMPLX_NEUTRAL.npz
aws s3 cp "s3://$BUCKET/run_pipeline.py" run_pipeline.py
aws s3 cp "s3://$BUCKET/$VIDEO_FILE" "$VIDEO_FILE"

# Your clip -> BVH in static-camera mode (-s, run_pipeline's default: no --moving-cam).
# SimpleVO needs strong inter-frame parallax + texture to estimate camera motion;
# on a near-static or low-texture clip it finds too few matches and GVHMR's two-view
# solver returns None -> crash. Static mode skips VO entirely (also faster) and the
# retargeted body joint angles are unaffected. (run_pipeline.py runs GVHMR's demo.py,
# which saves the .pt then crashes on its SMPL-only render step — run_pipeline
# tolerates that and converts the saved .pt.) Inference is already validated, so no
# separate tennis sanity check.
python run_pipeline.py "$VIDEO_FILE" --out "$OUT_BVH"
aws s3 cp "$OUT_BVH" "s3://$BUCKET/$OUT_BVH"
echo "DONE: s3://$BUCKET/$OUT_BVH"
USERDATA
# Write via a temp file rather than `sed -i`, whose syntax differs on macOS (BSD sed).
sed -e "s|__BUCKET__|$BUCKET|g" -e "s|__REGION__|$REGION|g" \
    -e "s|__VIDEO_FILE__|$VIDEO_FILE|g" -e "s|__VIDEO_STEM__|$VIDEO_STEM|g" \
    -e "s|__OUT_BVH__|$OUT_BVH|g" "$UD" > "$UD.tmp" && mv "$UD.tmp" "$UD"

# --- launch (retry while IAM profile finishes propagating) -------------------
ID=""
for i in 1 2 3 4 5; do
  ID=$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI" --instance-type "$INSTANCE_TYPE" \
    --iam-instance-profile "Name=$ROLE" \
    --instance-initiated-shutdown-behavior terminate \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":120,"DeleteOnTermination":true}}]' \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=tinydancer}]' \
    --user-data "file://$UD" --count 1 \
    --query 'Instances[0].InstanceId' --output text 2>aws/_run.err) && break
  echo "run-instances attempt $i failed; retrying in 15s ..."
  cat aws/_run.err; sleep 15
done

echo ""
echo "Launched instance: ${ID:-<failed>}  (installs GVHMR -> runs $VIDEO_FILE -> uploads $OUT_BVH -> self-terminates)"
if [ -z "${ID:-}" ] || [ "${ID:-}" = "None" ]; then
  echo "Launch failed — not watching. Check the error above."
  exit 1
fi

# Auto-follow the run to completion and download the BVH. Ctrl-C is safe (the box
# keeps running; re-attach with: WATCH_ONLY=1 bash aws/launch.sh).
watch_run "$BUCKET"
