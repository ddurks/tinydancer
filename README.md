# tinydancer

Pipeline: dance video → SMPL-X motion (via [GVHMR](https://github.com/zju3dv/GVHMR))
→ BVH → Blender armature → retarget onto your own humanoid mesh.

> Stage 1 was pivoted from WHAM to **GVHMR** (more accurate, no DPVO compile,
> actively maintained). The BVH + Blender half is unchanged and is **verified
> working** locally on Blender 5.1.

## Files

| File | Where it runs | Role |
| --- | --- | --- |
| `examples/buyit.mp4` | — | Bundled demo clip (stationary cam, single subject — the favorable case). Bring your own clip via `VIDEO_PATH=...`. |
| `infra/setup.sh` | GPU box (AWS `g4dn.xlarge` / any Linux+NVIDIA) | Clones GVHMR, installs deps, prompts for checkpoints + the SMPL-X body model (license-gated). |
| `infra/aws/launch.sh` + `docs/AWS.md` | Local bash (macOS / Linux / Git Bash) → AWS | **The Stage-1 path.** One command launches a self-terminating GPU box that installs GVHMR, runs your clip, and returns `<clip>.bvh` to S3 — then live-streams the log and auto-downloads the result (Ctrl-C safe; re-attach with `WATCH_ONLY=1`). Clip is set by `VIDEO_PATH` (default `buyit.mp4`); each clip gets its own BVH. Set your `BUCKET` first. `docs/AWS.md` documents it incl. cost-safety + every gotcha we hit. |
| `motion/run_pipeline.py` | GPU box, run from inside `GVHMR/` | Calls `tools/demo/demo.py`, parses `hmr4d_results.pt`, writes the BVH (`--out`). Axis-angle → ZYX Euler conversion; joint count auto-detected (22 for SMPL-X, 24 for SMPL). `--skip-infer` re-converts an existing `.pt` without re-running GVHMR. |
| `blender/blender_import.py` | Local Blender (any OS, no GPU needed) | Imports the BVH at correct scale/axes, names the armature `WHAM_Source`, pins the frame range, and **saves a `.blend`**. |
| `blender/check_rig.py` | Local Blender | Inspects your character's armature, best-effort matches each SMPL joint to one of its bones, flags missing required joints, and prints a ready-to-paste `BONE_MAP` for `retarget.py`. |
| `blender/retarget.py` | Local Blender | Addon-free retarget: bakes the `WHAM_Source` motion onto your rig as keyframes using a rest-pose-aware transfer. Pass a bone map with `--map`; see [Retargeting](#retargeting-retargetpy) for the other flags. |
| `rigs/*_map.py` (`danceguy_map.py`, `flowerguy_map.py`, `davebot_map.py`, `bigcorpsman_map.py`) | — | Example `BONE_MAP`s for real rigs, with notes on the structural quirks each one needed (and, in the header, the exact `retarget.py` command). Use them as templates for your own. |
| `motion/dance_cues.py` | Any machine (Python) | Beats (from the audio) + key poses (from the motion) → `cues.json`. |
| `motion/loop_clip.py` | Any machine (Python) | Cuts a seamless, beat-aligned loop → looped BVH (+ optional audio and cues). |
| `video/handheld.py` | Any machine (Python) | Transfers the source clip's hand-held camera shake onto a clean render. |
| `blender/bigcorp_dance.py` | Local Blender | BIGCORP's player rig: `retarget.py` plus keyed IK legs, foot planting and retiming, so the clip survives that game's glTF export. |

## Why two machines

Stage 1 (video → motion) is neural inference and needs an **NVIDIA GPU**. Stages 2–3
(BVH → armature → retarget) run in Blender on any machine, no GPU. So: do Stage 1 on
AWS, download the BVH, finish locally.

## End-to-end run (recommended: automated AWS) — see `docs/AWS.md`

One-time: request GPU quota, download the license-gated `SMPLX_NEUTRAL.npz` to the
repo root. Then from a bash shell (macOS/Linux, or Git Bash on Windows):
```bash
# First time: edit infra/aws/launch.sh and set BUCKET (or pass it inline as below).
bash infra/aws/launch.sh                              # default clip: buyit.mp4 -> buyit.bvh
VIDEO_PATH=./your_clip.mp4 bash infra/aws/launch.sh   # any other clip
```
It uploads your inputs, launches a **self-terminating** GPU box that installs GVHMR,
runs your clip in **static-camera mode** (the robust default; no VO), and uploads
`<clip>.bvh` (plus the raw `<clip>.hmr4d_results.pt`) to S3. The script follows
`run.log` live and downloads the BVH when it lands. Each clip writes its own
`<clip>.bvh`, so runs don't clobber each other. Full details,
cost-safety, and troubleshooting are in **`docs/AWS.md`**.

## Blender side (local, verified)

```bash
blender --background --python blender/blender_import.py -- buyit.bvh buyit.blend
# Windows: "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" ...
# macOS:   /Applications/Blender.app/Contents/MacOS/Blender ...
```
…or drag the BVH into Blender's UI. Then open the `.blend` and retarget with
`retarget.py` (below), or with the free
[Rokoko Studio Live addon](https://github.com/Rokoko/rokoko-studio-live-blender)
(source = `WHAM_Source`, target = your rig → auto-build bone list → retarget → bake).

### Retargeting (`retarget.py`)

Put `WHAM_Source` and your rig in one `.blend`, run `check_rig.py` to get a
`BONE_MAP`, save it as a `.py` (or start from a `*_map.py`), then:
```bash
blender --background scene.blend --python blender/retarget.py -- --map my_map.py [flags]
```
- `--target NAME` — the rig to retarget onto; required when the file has several armatures.
- `--action NAME` — key into a new action instead of touching the rig's existing ones.
- `--root-scale auto|<float>` — scale root travel; `auto` = target/source leg-length ratio
  (for rigs that aren't human-proportioned).
- `--ground` — shift the root so the feet rest on the target's rest-pose floor.
- `--face-front` — cancel the clip's average heading so the rig faces its rest-pose front
  (world-grounded output has an arbitrary heading); `--face-damp K` (0–1) also cancels
  that fraction of the per-frame turning.

A `BONE_MAP` value may be a list, so one SMPL joint can drive several target bones
(see `davebot_map.py`). Both rigs should be at the origin, unrotated, in a T-pose rest.

**Alternative — skip retargeting:** for a new, roughly-humanoid, *unrigged* character you
can skin your mesh directly to `WHAM_Source` (parent → Automatic Weights) instead of
retargeting. See [docs/SKINNING.md](docs/SKINNING.md) — including how to automate it via the Blender
MCP server.

## Extras: cues, loops, camera shake

Plain Python scripts (no GPU, no Blender; need `ffmpeg` on PATH). The first two read
the raw `<clip>.hmr4d_results.pt` — grab it with `aws s3 cp s3://<BUCKET>/<clip>.hmr4d_results.pt .`.

- **`dance_cues.py`** — `python motion/dance_cues.py <clip>.hmr4d_results.pt <audio-or-video> --out cues.json [--start SEC]`.
  Beats via librosa, key poses = fast motion snapping to a hold. `--start` = where the
  clip begins in an untrimmed source's audio.
- **`loop_clip.py`** — `python motion/loop_clip.py <clip>.hmr4d_results.pt cues.json --out loop.bvh [--beats 8] [--fade 0.4]`.
  Picks the `--beats`-long window whose end pose best matches its start and crossfades the
  seam. Optional `--audio src.mp4 --audio-start S --audio-out loop.m4a`, `--cues-out`, `--window-out`.
- **`handheld.py`** — `python video/handheld.py source.mp4 render.mp4 out.mp4 [--strength 1.0]`.
  Tracks the source phone clip's jitter (shift + roll, pans removed) and applies it to a
  frame-matched render. For a loop: `--source-start S --loop-fade F`. Needs `opencv-python`.

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
- **Monocular limits.** Foot sliding / depth jitter on fast moves are expected.
  `retarget.py --ground` fixes the floor height but not per-frame contact;
  `--run_smplify`-style refinement or physics-based post (e.g. PhysHMR) are future options.
- **Retargeting needs a bone map.** `check_rig.py` best-effort matches bone names and
  prints a `BONE_MAP` to edit; rigs with unusual structure (several parentless roots, no
  wrist or collar bones) need hand-tuning — the `*_map.py` headers show worked examples.
  Rokoko auto-maps common naming (Mixamo, Rigify, UE5 Manny) if you go that route.

## State of the world

- **Stage 2 (BVH → Blender armature): verified** on Blender 5.1 here, for both the 24-joint
  (SMPL) and 22-joint (SMPL-X) skeletons — produces an animated `WHAM_Source` armature and a
  saved `.blend`.
- **Stage 1 (GVHMR inference + `.pt → BVH`): verified on AWS** via `infra/aws/launch.sh` on a
  g5.xlarge (YOLO → ViTPose → HMR2 → GVHMR → `run_pipeline.py` writes the BVH).
- **Stage 3 (`retarget.py`): working** in recent runs; per-rig bone maps are the `*_map.py` files.

## What's not in this repo

Models, checkpoints, cloned upstreams, and large/personal media are intentionally
not committed — the scripts fetch or generate them. You supply:

- `SMPLX_NEUTRAL.npz` — license-gated, download from <https://smpl-x.is.tue.mpg.de/>.
- GVHMR + its checkpoints — `infra/setup.sh` / `infra/aws/launch.sh` clone and download these.
- Your own source clip — only the small `buyit.mp4` demo is bundled.

## License

The original code here (the `*.py` / `*.sh` glue and docs) is **MIT** — see
[LICENSE](LICENSE). That license covers only this repo's code, **not** the models
it downloads. SMPL-X, GVHMR, and the bundled detectors/pose models carry their own
licenses — several **non-commercial research only** — which you must comply with
when you run the pipeline. See [THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md).
