# Human3R backend (spike)

A spike swapping GVHMR's 4-stage Stage-1 chain (YOLO → ViTPose → HMR2 → GVHMR) for
**[Human3R](https://github.com/fanegg/Human3R)** — one feed-forward model that outputs
world-grounded, multi-person SMPL-X directly (real-time, ~8–18 GB VRAM). Why bother:
it collapses the brittle multi-stage setup into a single model and keeps the whole
BVH/Blender half of tinydancer unchanged.

> **Status:** the adapter (`--backend human3r` in `run_pipeline.py`) is implemented
> and its data-plumbing + world-transform math are **validated on synthetic Human3R
> output**. It has **not** yet been run against a real GPU inference — a few field
> semantics (§ "Open validation items") must be confirmed on a real `.npz` before
> relying on it. The GVHMR path is untouched and remains the default.

## Run it

```bash
bash setup_human3r.sh                 # clones Human3R, installs, compiles kernels,
                                      # fetches checkpoint + (license-gated) body models
cd Human3R
python ../run_pipeline.py ../buyit.mp4 --backend human3r --out ../buyit.bvh
```

Useful flags (Human3R-only):
- `--incam` — emit camera-frame params instead of reconstructed world-frame.
- `--person auto|<int>` — subject picker for multi-person clips (`auto` = highest score).
- `--human3r-model src/human3r_672S.pth` — smaller/faster checkpoint (less VRAM).
- `--skip-infer` — re-convert already-saved `.npz` without re-running the GPU.

The BVH then feeds the **unchanged** local half: `blender_import.py`, `check_rig.py`,
`retarget.py`.

## Format mapping (GVHMR → Human3R)

The adapter (`extract_motion_human3r` in `run_pipeline.py`) bridges these differences:

| | GVHMR | Human3R | Adapter does |
| --- | --- | --- | --- |
| Output | one `hmr4d_results.pt` | one `.npz` **per frame** under `<out>/smpl/` | stitches frames → time series |
| Pose keys | `global_orient`+`body_pose` | one stacked `rotvec (N,53,3)` | slices `[0]`=root, `[1:22]`=21 body |
| Rotation | axis-angle | axis-angle | (same — no conversion) |
| Joints | 22 (SMPL-X) | 53 (SMPL-X − eyes; body+hands+jaw) | keeps 22, drops hands/jaw |
| Frame | world **and** camera provided | **camera only** | reconstructs world via `camera/*.npz` c2w |
| Subjects | single | multi-person (leading dim `N`) | picks one per frame (`--person`) |

The 22-joint result drops straight into the existing `write_bvh()` + `SMPL_JOINTS[:22]`
— the same skeleton the GVHMR path already emits, so Stage 2/3 need no changes.

## What's validated

Run against fabricated per-frame `.npz` (multi-person frame, an empty frame, non-identity
camera pose):
- ✅ per-frame aggregation, empty-frame skip, multi-person collapse + warning
- ✅ 53→22 joint slice; valid BVH (22 joints, 69 channels = 6 root + 21×3)
- ✅ world transform: injecting a 30° camera Z-rotation shifted the root `Zrot` by
  exactly +30° while body-joint angles stayed frame-invariant — i.e. the Rodrigues
  round-trip and c2w application are correct.

## Open validation items (confirm on a real GPU run)

The [output-format research](https://github.com/fanegg/Human3R) flagged these as
"convention, not documented" — verify against a real `.npz` before trusting output:

1. **53-joint index boundaries.** `[0]`=global_orient and `[1:22]`=21 body joints is
   the safe part (standard SMPL-X order). The hand/jaw boundaries beyond 22 are
   sliced off, so they don't affect us — but confirm body ends at 22.
2. **`transl` origin.** Human3R builds its SMPL layer with `person_center='head'`, so
   translation may be head-centered, not pelvis-rooted like GVHMR. May shift root
   height / worsen foot contact; check before caring about global position.
3. **World transform.** Human3R saves **camera-frame** params; we reconstruct world by
   applying `camera/*.npz` c2w. Validate the result is gravity-aligned; if it looks
   wrong, `--incam` gives raw camera-frame (fine for a static camera up to a constant
   transform). `_matrix_to_axis_angle` also has the usual θ≈π instability (rare for a
   root orientation).
4. **Multi-person tracking.** `--person auto` picks the highest-score row **per frame**
   with no cross-frame identity — it can swap subjects in true multi-person clips. Fine
   for single-subject dance clips; a real tracker (via `smpl_id`, if it's actually
   persisted in the `.npz`) is future work.

## Hands (dance!)

Human3R already predicts hands — `rotvec[:,22:37]` (left) and `[:,37:52]` (right) — which
the adapter currently **drops** because `SMPL_JOINTS` stops at the wrists. Wiring these
in (re-add the hand joints to the skeleton) is the natural follow-up that GVHMR couldn't
give you, and the single biggest quality win for a *dance* pipeline.

## AWS automation — `aws/launch_human3r.sh`

The Human3R twin of `aws/launch.sh`, kept as a **separate file** so the validated GVHMR
launcher stays untouched. It reuses launch.sh's proven scaffolding (IAM role, AMI query,
launch-retry, live log watcher, self-terminate) and swaps only the model-specific parts:

- **Env/install:** a clean conda env (Python 3.11 + CUDA 12.4 + nvcc), clone Human3R,
  **compile `src/croco/.../curope` CUDA kernels** (a step GVHMR doesn't have), gsplat.
- **Checkpoint:** HF `faneggg/human3r` (~4.7 GB), cached to S3 for cheap re-runs.
- **Body models:** tries your existing `SMPLX_NEUTRAL.npz` first. If Human3R needs more,
  stage a `human3r_models.tgz` (a `models/` tree from its `fetch_smplx.sh`) to S3 — the
  box prefers it. This is the **one likely first-run gotcha**; the run.log names the
  missing file if so.
- **Output:** produces `<clip>.human3r.bvh` AND caches the raw `smpl/` + `camera/` `.npz`
  as `<clip>.human3r_raw.tgz` — so the adapter's open items can be re-validated (and the
  BVH re-generated) **without another GPU run**.

Run it (from repo root):
```bash
BUCKET=tinydancer-<acct-id> bash aws/launch_human3r.sh
VIDEO_PATH=./your_clip.mp4  BUCKET=... bash aws/launch_human3r.sh   # any clip
WATCH_ONLY=1 BUCKET=... bash aws/launch_human3r.sh                  # re-attach a run
```

**Cost safety (three independent guards):** shutdown-behavior=terminate (no lingering
box, EBS auto-deleted) · an EXIT trap that shuts down on success or error · a hard
**watchdog** that force-terminates after `MAX_MINUTES` (default 75) even if a step hangs.
It also refuses to launch if a tinydancer box is already running. Worst case is bounded:
g5.xlarge ≈ $1/hr → ≈ $1.25 at the cap; a typical run is ~$0.40. Tune with
`MAX_MINUTES=…` / `INSTANCE_TYPE=…` / `HUMAN3R_CKPT=human3r_672S.pth` (smaller/cheaper).

> Still **untested until its first real launch** — the conda/CUDA build and body-model
> requirements can only be shaken out on a live box. The guards above ensure a failed
> first run costs cents, not a runaway bill.

## License

Human3R weights are **CC-BY-NC-SA-4.0** (non-commercial). SMPL-X / SMPL remain
license-gated and research-only. Fine for a personal project; a blocker if tinydancer
ever ships commercially — see [THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md).
