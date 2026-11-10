# Running Stage 1 on AWS (you control Python → GVHMR's pinned wheels just install)

GVHMR pins Python 3.10 wheels (e.g. `pytorch3d ...cp310...`). The AWS Deep Learning
AMI ships **system Python 3.10**, so the official install works without wheel-version
roulette — you control the environment instead of fighting a hosted one.

---

## Hands-off path (local AWS CLI + self-terminating instance) — `aws/launch.sh`

Driven from your own machine in a bash shell — macOS/Linux, or **Git Bash** on
Windows (AWS CLI already configured). The
script uploads your local inputs, launches the GPU box, and the box does the rest
and self-terminates. The **one unavoidable manual thing** is the license-gated
SMPL-X download.

**A. Quota (once):** if this is a new account, request GPU quota first — *Service
Quotas → EC2 → "Running On-Demand G and VT instances" → ≥ 4 vCPUs.*

**B. Get SMPL-X (once):** download `SMPLX_NEUTRAL.npz` from
<https://smpl-x.is.tue.mpg.de/> (free account, accept license) and drop it in the
repo root next to `run_pipeline.py`.

**C. Edit + run** (from the repo root, in bash):
1. Open `aws/launch.sh`, set `REGION`, a globally-unique `BUCKET`, and
   `INSTANCE_TYPE` (and the input paths if your files live elsewhere).
2. `bash aws/launch.sh`
   It uploads the three inputs, creates the IAM role, finds the AMI, launches the
   box, then **auto-follows the run** and downloads the BVH when done — one command,
   walk away.

**D. Watching (automatic).** `launch.sh` streams `run.log` to your terminal live and,
when `<clip>.bvh` appears (e.g. `buyit.bvh`), downloads it to the repo folder and prints
the Blender command. You don't poll anything.
- **Ctrl-C is safe** — it stops *watching* only; the instance keeps running.
- **Re-attach** (after Ctrl-C, or from another terminal) without relaunching:
  ```bash
  WATCH_ONLY=1 bash aws/launch.sh
  ```
- **On failure** the watcher reports the box terminated without a BVH and points at
  the preserved `s3://<BUCKET>/<clip>.hmr4d_results.pt` (raw inference output, if any).
- Manual equivalents, if you prefer:
  ```bash
  aws s3 cp s3://<BUCKET>/run.log - | tail -n 40
  aws s3 cp s3://<BUCKET>/buyit.bvh .   # or <clip>.bvh for another clip
  ```

**E. Local Blender:** see step 8 below. For the beat/loop/camera-shake helpers, also
grab `s3://<BUCKET>/<clip>.hmr4d_results.pt` (see the README's "Extras").

> Note: `aws/launch.sh` fails loudly to `run.log` so a broken run is diagnosable.
> The manual runbook below is the fallback if you'd rather SSH in and drive it yourself.

---

## Cost & safety (it cleans up after itself)

Each `bash aws/launch.sh` launches **exactly one** instance that **self-terminates**
when its script ends — success *or* failure. Two mechanisms guarantee it:
- `--instance-initiated-shutdown-behavior terminate` on launch, and
- a `trap … shutdown -h now` in the boot script (runs on any exit, incl. `set -e` aborts).
- EBS is `DeleteOnTermination=true`, so no orphaned volumes either.

Cost is ~**$1/hr** for `g5.xlarge` (or ~$0.53/hr `g4dn.xlarge`); a run is 15–25 min,
so **well under $1 each**, even across several debug iterations.

**Audit anytime** (should be empty once a run finishes):
```bash
aws ec2 describe-instances --region us-east-1 \
  --filters "Name=instance-state-name,Values=running,pending" \
  --query 'Reservations[].Instances[].[InstanceId,InstanceType]' --output table
```
**Kill-switch** (terminate any live tinydancer box; no-ops if none):
```bash
aws ec2 terminate-instances --region us-east-1 --instance-ids \
  $(aws ec2 describe-instances --region us-east-1 \
    --filters "Name=tag:Name,Values=tinydancer" "Name=instance-state-name,Values=running,pending" \
    --query 'Reservations[].Instances[].InstanceId' --output text)
```
> The only way a box would *not* self-terminate is if the script truly hangs (a
> download stalling forever rather than erroring). Hasn't happened; the kill-switch
> covers it.

---

## Gotchas baked into `aws/launch.sh` (lessons from real runs)

Each of these was a real failure we hit and fixed in the script — documented so the
fixes aren't mysterious and so a future break is faster to diagnose:

1. **Windows + Git Bash user-data path.** The Windows `aws.exe` resolves
   `file:///tmp/x` as `C:\tmp\x`. Fix: the boot script is written into the repo
   (`aws/_userdata.gen.sh`) and passed as a relative `file://` path both shells agree on.
2. **`python: command not found`.** The Deep Learning AMI ships `python3`, not
   `python`, and isn't reliably a conda env. Fix: symlink `python → python3` and use
   the **system Python 3.10** (which is exactly GVHMR's target version).
3. **`chumpy` build fails (`No module named 'pip'`).** Its ancient `setup.py` does
   `import pip`, which breaks under PEP517 build isolation. Fix: install it first with
   `--no-build-isolation` before the `requirements.txt` pass.
4. **Google Drive checkpoint throttling** ("Too many users… downloaded recently").
   GVHMR's checkpoints live on a Drive folder that rate-limits hard after repeated
   pulls — `gdown` failed partway every run. Fix: download from the **Hugging Face
   mirror [`camenduru/GVHMR`](https://huggingface.co/camenduru/GVHMR)** via
   `huggingface_hub.snapshot_download` (no rate limits), YOLO fallback from
   Ultralytics, **verify** all required checkpoints, then **cache the full set to S3**
   (`s3://<bucket>/checkpoints.tar.gz`) so reruns skip the download entirely.
5. **`numpy`/`chumpy`** — auto-resolved: GVHMR's `requirements.txt` pins
   `numpy==1.23.5`, which is below where chumpy's `np.bool`/`np.float` break, so the
   feared runtime domino never fires.
6. **`ModuleNotFoundError: No module named 'tkinter'`** — GVHMR's `body_model.py` has
   a stray `from turtle import forward` (an IDE auto-import goof; `turtle` pulls in
   `tkinter`). It's silently fine on desktops but crashes headless. Fix:
   `apt-get install -y python3-tk`.
7. **`demo.py` crashes on render (`body_models/smpl does not exist`).** GVHMR
   *inference* uses SMPL-X (which we supply), but its demo's preview *rendering* needs
   the plain **SMPL** model. The `.pt` results are saved **before** rendering, and we
   don't want the preview — so `run_pipeline.py` uses `subprocess.run` (not
   `check_call`) and treats "did the `.pt` get written?" as the success check. No
   second license-gated SMPL download needed.
8. **Fail loud.** `set -e` + everything tee'd to `run.log` (pushed to S3 every 60s)
   means a broken run stops at the real error instead of printing a false "DONE".

---

## Manual runbook (full control / debugging)

## 0. Heads-up before you start
- **GPU quota:** brand-new AWS accounts often have a **0 vCPU limit** for GPU
  instances. In the console: *Service Quotas → EC2 → "Running On-Demand G and VT
  instances" → request ≥ 4 vCPUs.* Approval can take minutes to a day. Do this first.
- **Cost:** `g4dn.xlarge` (1× NVIDIA T4) ≈ **$0.53/hr** on-demand. One clip incl.
  setup ≈ 1 hr ≈ **under $1**. **STOP or TERMINATE the instance when done** or it
  keeps billing.

## 1. Launch the instance (EC2 console)
- **AMI:** search Quickstart for **"Deep Learning AMI GPU PyTorch" (Ubuntu 22.04)** —
  NVIDIA drivers + CUDA + conda preinstalled (no driver pain).
- **Instance type:** `g4dn.xlarge` (or `g5.xlarge` / A10G if you want it faster, ~$1/hr).
- **Key pair:** create/download one (e.g. `tinydancer.pem`) for SSH.
- **Storage:** keep the default (~100+ GB EBS) — checkpoints are a few GB.
- **Security group:** allow **SSH (port 22)** from your IP.
- Launch, then copy the instance's **Public IPv4 DNS**.

## 2. SSH in (PowerShell on your machine)
```powershell
icacls "tinydancer.pem" /inheritance:r ; icacls "tinydancer.pem" /grant:r "$($env:USERNAME):(R)"
ssh -i "tinydancer.pem" ubuntu@<PUBLIC_DNS>
```
(macOS/Linux: `chmod 400 tinydancer.pem` instead of `icacls`; the `ssh`/`scp` commands
below are the same.)

## 3. Set up GVHMR (on the box, Python 3.10 env)
```bash
conda create -y -n gvhmr python=3.10
conda activate gvhmr

git clone https://github.com/zju3dv/GVHMR.git --recursive
cd GVHMR

# Torch 2.3 + CUDA 12.1 (GVHMR's target), then the package + deps.
pip install torch==2.3.0 torchvision==0.18.0 --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt
pip install -e .

# pytorch3d: on Python 3.10 the cp310 wheel installs. If requirements.txt didn't
# already pull it, build from source (works on any env, ~10-15 min):
python -c "import pytorch3d" 2>/dev/null || \
    FORCE_CUDA=1 pip install "git+https://github.com/facebookresearch/pytorch3d.git@stable"

# Checkpoints (public Google Drive folder). Drive rate-limits hard (Gotcha 4); if
# gdown fails, use the Hugging Face mirror instead:
#   pip install huggingface_hub && python -c "from huggingface_hub import snapshot_download as s; s('camenduru/GVHMR', local_dir='inputs/checkpoints')"
pip install gdown
mkdir -p inputs/checkpoints
gdown --folder https://drive.google.com/drive/folders/1eebJ13FUEXrKBawHpJroW0sNSxLjh9xD \
    -O inputs/checkpoints
# Verify the tree: inputs/checkpoints/gvhmr/gvhmr_siga24_release.ckpt etc.
# (gdown --folder may nest one level — move files up if so.)
```

## 4. SMPL-X body model (one-time, license-gated)
Register at <https://smpl-x.is.tue.mpg.de/>, download SMPL-X v1.1, then upload the
neutral file from your machine (new PowerShell window):
```powershell
scp -i "tinydancer.pem" SMPLX_NEUTRAL.npz ubuntu@<PUBLIC_DNS>:~/GVHMR/inputs/checkpoints/body_models/smplx/
```
(Make the folder first on the box: `mkdir -p inputs/checkpoints/body_models/smplx`)

## 5. Upload our converter + the clip
```powershell
scp -i "tinydancer.pem" run_pipeline.py buyit.mp4 ubuntu@<PUBLIC_DNS>:~/GVHMR/
```

## 6. Run it (on the box)
```bash
# Sanity check on GVHMR's bundled clip — confirms the install is healthy:
python tools/demo/demo.py --video=docs/example_video/tennis.mp4 -s

# Your clip -> BVH (one command: inference + convert). Static-camera mode is the
# default and the robust path; add --moving-cam ONLY for a clip with real camera
# motion AND plenty of texture (SimpleVO crashes if it can't find enough matches):
python run_pipeline.py buyit.mp4 --out buyit.bvh
```
Success prints `Wrote buyit.bvh (... frames, 22 joints @ 30 fps)`.

## 7. Pull the BVH back, then STOP the instance
```powershell
scp -i "tinydancer.pem" ubuntu@<PUBLIC_DNS>:~/GVHMR/buyit.bvh .
```
Then in the EC2 console: **Instance state → Stop** (or **Terminate** if you're done).

## 8. Local Blender (no GPU)
```powershell
& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --python blender_import.py -- buyit.bvh buyit.blend
```
(macOS: `/Applications/Blender.app/Contents/MacOS/Blender --background --python blender_import.py -- buyit.bvh buyit.blend`)
Open `buyit.blend` → animated `WHAM_Source` armature, ready to retarget.
