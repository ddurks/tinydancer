# tinydancer

Pipeline: dance video → SMPL-X motion (via [GVHMR](https://github.com/zju3dv/GVHMR))
→ BVH → Blender armature → retarget onto your own humanoid mesh.

> Stage 1 was pivoted from WHAM to **GVHMR** (more accurate, no DPVO compile,
> actively maintained). The BVH + Blender half is unchanged and is **verified
> working** locally on Blender 5.1.

## Files

| File | Where it runs | Role |
| --- | --- | --- |
| `buyit.mp4` | — | Bundled demo clip (stationary cam, single subject — the favorable case). Bring your own clip via `VIDEO_PATH=...`. |
| `setup.sh` | GPU box (AWS `g4dn.xlarge` / any Linux+NVIDIA) | Clones GVHMR, installs deps, prompts for checkpoints + the SMPL-X body model (license-gated). |
| `aws/launch.sh` + `AWS.md` | Local Git Bash → AWS | **The Stage-1 path.** One command launches a self-terminating GPU box that installs GVHMR, runs your clip, and returns `<clip>.bvh` to S3 — then live-streams the log and auto-downloads the result (Ctrl-C safe; re-attach with `WATCH_ONLY=1`). Clip is set by `VIDEO_PATH` (default `buyit.mp4`); each clip gets its own BVH. Set your `BUCKET` first. `AWS.md` documents it incl. cost-safety + every gotcha we hit. |
| `run_pipeline.py` | GPU box, run from inside `GVHMR/` | Calls `tools/demo/demo.py`, parses `hmr4d_results.pt`, writes the BVH (`--out`). Axis-angle → ZYX Euler conversion; joint count auto-detected (22 for SMPL-X, 24 for SMPL). |
| `blender_import.py` | Local Blender (any OS, no GPU needed) | Imports the BVH at correct scale/axes, names the armature `WHAM_Source`, pins the frame range, and **saves a `.blend`**. |
| `check_rig.py` | Local Blender | Inspects your character's armature, best-effort matches each SMPL joint to one of its bones, flags missing required joints, and prints a ready-to-paste `BONE_MAP` for `retarget.py`. |
| `retarget.py` | Local Blender | Addon-free retarget: bakes the `WHAM_Source` motion onto your rig as keyframes using a rest-pose-aware transfer. Pass a bone map with `--map`. |
| `*_map.py` (e.g. `danceguy_map.py`, `flowerguy_map.py`) | — | Example `BONE_MAP`s for real rigs, with notes on the structural quirks each one needed. Use them as templates for your own. |

## Why two machines

Stage 1 (video → motion) is neural inference and needs an **NVIDIA GPU**. Stages 2–3
(BVH → armature → retarget) run in Blender on any machine, no GPU. So: do Stage 1 on
AWS, download the BVH, finish locally.

## End-to-end run (recommended: automated AWS) — see `AWS.md`

One-time: request GPU quota, download the license-gated `SMPLX_NEUTRAL.npz` to the
repo root. Then from Git Bash:
```bash
# First time: edit aws/launch.sh and set BUCKET (or pass it inline as below).
bash aws/launch.sh                              # default clip: buyit.mp4 -> buyit.bvh
VIDEO_PATH=./your_clip.mp4 bash aws/launch.sh   # any other clip
```
It uploads your inputs, launches a **self-terminating** GPU box that installs GVHMR,
runs your clip in **static-camera mode** (the robust default; no VO), and uploads
`<clip>.bvh` to S3. Watch `s3://<bucket>/run.log`; download the BVH when it appears.
Each clip writes its own `<clip>.bvh`, so runs don't clobber each other. Full details,
cost-safety, and troubleshooting are in **`AWS.md`**.

## Blender side (local, verified)

```
"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" ^
    --background --python blender_import.py -- buyit.bvh buyit.blend
```
…or drag the BVH into Blender's UI. Then open the `.blend`. To retarget:

- Install the free [Rokoko Studio Live addon](https://github.com/Rokoko/rokoko-studio-live-blender).
- Source armature = `WHAM_Source`, target = your humanoid rig. Auto-build bone list → retarget → bake.

## Skeleton reference (SMPL/SMPL-X joints, used by `run_pipeline.py`)

`Pelvis → {L_Hip→L_Knee→L_Ankle→L_Foot, R_Hip→R_Knee→R_Ankle→R_Foot, Spine1→Spine2→Spine3→{Neck→Head, L_Collar→L_Shoulder→L_Elbow→L_Wrist(→L_Hand), R_Collar→R_Shoulder→R_Elbow→R_Wrist(→R_Hand)}}`

SMPL-X output uses 22 joints (the `L_Hand`/`R_Hand` tips are dropped and the wrists
become leaf bones); plain SMPL uses all 24. The script detects which from the data.

BVH conventions: cm units, Y-up, `-Z` forward, ZYX rotation order, 6-channel root
(`Xpos Ypos Zpos Zrot Yrot Xrot`), 3-channel joints (`Zrot Yrot Xrot`).

## Known gotchas / next refinements

- **GVHMR is SMPL-X.** Its `body_pose` covers 21 body joints (63 dims); `run_pipeline.py`
  prepends `global_orient` and detects the joint count, so it handles SMPL-X (22) or
  plain SMPL (24) without changes. If GVHMR ever emits separate hand poses you want to
  keep, the skeleton would need the hand joints re-added.
- **Rest-pose offsets are approximate.** `SMPL_JOINTS` offsets are hand-tuned, not from
  the model's J-regressor. Blender re-derives bone lengths on import so retargeting works;
  if proportions drift, compute offsets from the SMPL-X neutral body (`J = J_regressor @ v_template`, then child − parent).
- **World vs camera frame.** Default is `smpl_params_global` (gravity-aligned, world-grounded
  — GVHMR's strength). Pass `--incam` for camera-frame params. Use `-s`/default static-cam
  unless the camera moves (`--moving-cam` → SimpleVO; still no DPVO compile).
- **Body models are license-gated.** SMPL-X cannot be redistributed — download
  `SMPLX_NEUTRAL.npz` once from <https://smpl-x.is.tue.mpg.de/> (the AWS script
  uploads it from your machine to your private S3 bucket).
- **Monocular limits.** Foot sliding / depth jitter on fast moves are expected. GVHMR is
  already near state of the art here; `--run_smplify`-style refinement or physics-based
  post (e.g. PhysHMR) are future options.
- **Retargeting quality depends on the target rig's bone naming.** Rokoko auto-maps common
  naming (Mixamo, Rigify, UE5 Manny); custom rigs need a manual bone list.

## State of the world

- **Stage 2 (BVH → Blender armature): verified** on Blender 5.1 here, for both the 24-joint
  (SMPL) and 22-joint (SMPL-X) skeletons — produces an animated `WHAM_Source` armature and a
  saved `.blend`.
- **Stage 1 (GVHMR inference): runs on AWS** via `aws/launch.sh` — GVHMR inference is
  confirmed working on a g5.xlarge (YOLO → ViTPose → HMR2 → GVHMR produce motion). The
  last piece under validation is `run_pipeline.py`'s `.pt → BVH` conversion on a real
  output.

## What's not in this repo

Models, checkpoints, cloned upstreams, and large/personal media are intentionally
not committed — the scripts fetch or generate them. You supply:

- `SMPLX_NEUTRAL.npz` — license-gated, download from <https://smpl-x.is.tue.mpg.de/>.
- GVHMR + its checkpoints — `setup.sh` / `aws/launch.sh` clone and download these.
- Your own source clip — only the small `buyit.mp4` demo is bundled.

## License

The original code here (the `*.py` / `*.sh` glue and docs) is **MIT** — see
[LICENSE](LICENSE). That license covers only this repo's code, **not** the models
it downloads. SMPL-X, GVHMR, and the bundled detectors/pose models carry their own
licenses — several **non-commercial research only** — which you must comply with
when you run the pipeline. See [THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md).
