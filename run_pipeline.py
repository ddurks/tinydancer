"""
End-to-end: video.mp4 -> motion inference -> BVH animation file.

Two interchangeable Stage-1 backends produce the same (poses, trans) that the
shared BVH writer consumes:

  --backend gvhmr   (default, validated): GVHMR's 4-stage chain
      (https://github.com/zju3dv/GVHMR). Run from inside the GVHMR/ dir.
  --backend human3r (spike, see HUMAN3R.md): Human3R's single feed-forward model
      (https://github.com/fanegg/Human3R). Run from inside the Human3R/ dir.

Both emit SMPL-X params (pelvis + 21 body joints = 22), which this script converts
to a BVH that Blender imports as an armature. The BVH writer is backend-agnostic.

Usage:
    # GVHMR (from inside GVHMR/):
    python run_pipeline.py /path/to/video.mp4 [--out animation.bvh]
    # Human3R (from inside Human3R/):
    python run_pipeline.py /path/to/video.mp4 --backend human3r [--out animation.bvh]

Output:
    - GVHMR:   outputs/demo/<name>/hmr4d_results.pt   (single .pt)
    - Human3R: outputs/human3r/<name>/smpl/*.npz      (per-frame files)
    - BVH file: animation.bvh  (drag into Blender, or use blender_import.py)
"""

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np


# --- SMPL/SMPL-X joint skeleton -----------------------------------------------
# (joint_name, parent_index, rest-pose offset_from_parent_xyz_meters).
# Indices 0..21 (Pelvis..R_Wrist) ARE the SMPL-X body joints (pelvis = global
# orient + 21 body_pose joints). Indices 22..23 (hands) only exist for plain
# SMPL; for SMPL-X output the skeleton is sliced to 22 joints and the wrists
# become leaf bones. Offsets are approximate T-pose values — Blender re-derives
# bone lengths on import, so they only need to be roughly right for retargeting.
SMPL_JOINTS = [
    ("Pelvis",       -1, (0.0,      0.0,     0.0)),
    ("L_Hip",         0, (0.058,   -0.082,   0.0)),
    ("R_Hip",         0, (-0.060,  -0.090,   0.0)),
    ("Spine1",        0, (-0.004,   0.124,  -0.038)),
    ("L_Knee",        1, (0.043,   -0.386,   0.008)),
    ("R_Knee",        2, (-0.045,  -0.383,   0.008)),
    ("Spine2",        3, (0.004,    0.138,   0.026)),
    ("L_Ankle",       4, (-0.015,  -0.426,  -0.037)),
    ("R_Ankle",       5, (0.025,   -0.439,  -0.034)),
    ("Spine3",        6, (-0.002,   0.056,   0.003)),
    ("L_Foot",        7, (-0.001,  -0.062,   0.122)),
    ("R_Foot",        8, (-0.004,  -0.064,   0.130)),
    ("Neck",          9, (-0.014,   0.210,  -0.034)),
    ("L_Collar",      9, (0.071,    0.114,  -0.019)),
    ("R_Collar",      9, (-0.082,   0.113,  -0.018)),
    ("Head",         12, (0.014,    0.088,   0.050)),
    ("L_Shoulder",   13, (0.114,    0.046,  -0.009)),
    ("R_Shoulder",   14, (-0.114,   0.046,  -0.013)),
    ("L_Elbow",      16, (0.260,   -0.025,  -0.026)),
    ("R_Elbow",      17, (-0.260,  -0.022,  -0.034)),
    ("L_Wrist",      18, (0.250,   -0.005,   0.000)),
    ("R_Wrist",      19, (-0.252,  -0.001,  -0.005)),
    ("L_Hand",       20, (0.084,   -0.012,  -0.007)),
    ("R_Hand",       21, (-0.082,  -0.010,  -0.009)),
]


def axis_angle_to_euler_zyx(aa):
    """Convert (..., 3) axis-angle -> (..., 3) ZYX Euler degrees for BVH."""
    aa = np.asarray(aa).reshape(-1, 3)
    theta = np.linalg.norm(aa, axis=-1, keepdims=True)
    safe = np.where(theta < 1e-8, 1.0, theta)
    k = aa / safe
    s = np.sin(theta)
    c = np.cos(theta)
    C = 1.0 - c
    x, y, z = k[..., 0:1], k[..., 1:2], k[..., 2:3]
    R = np.zeros(aa.shape[:1] + (3, 3))
    R[:, 0, 0] = (c + x * x * C).squeeze(-1)
    R[:, 0, 1] = (x * y * C - z * s).squeeze(-1)
    R[:, 0, 2] = (x * z * C + y * s).squeeze(-1)
    R[:, 1, 0] = (y * x * C + z * s).squeeze(-1)
    R[:, 1, 1] = (c + y * y * C).squeeze(-1)
    R[:, 1, 2] = (y * z * C - x * s).squeeze(-1)
    R[:, 2, 0] = (z * x * C - y * s).squeeze(-1)
    R[:, 2, 1] = (z * y * C + x * s).squeeze(-1)
    R[:, 2, 2] = (c + z * z * C).squeeze(-1)

    # ZYX Euler from rotation matrix.
    sy = np.sqrt(R[:, 0, 0] ** 2 + R[:, 1, 0] ** 2)
    singular = sy < 1e-6
    rx = np.where(singular,
                  np.arctan2(-R[:, 1, 2], R[:, 1, 1]),
                  np.arctan2(R[:, 2, 1], R[:, 2, 2]))
    ry = np.arctan2(-R[:, 2, 0], sy)
    rz = np.where(singular, 0.0, np.arctan2(R[:, 1, 0], R[:, 0, 0]))
    return np.degrees(np.stack([rz, ry, rx], axis=-1))


def write_bvh(path, poses, trans, fps, joints=SMPL_JOINTS):
    """
    poses: (T, J, 3) axis-angle pose params (joint 0 = global orient).
    trans: (T, 3) root translation in meters.
    joints: list of (name, parent_idx, offset) of length J.
    """
    T = poses.shape[0]
    J = poses.shape[1]
    joints = joints[:J]
    assert len(joints) == J, f"{len(joints)} joints in skeleton but poses have {J}"

    # BVH uses centimeters by convention; SMPL is meters.
    SCALE = 100.0
    children = {i: [] for i in range(J)}
    for i, (_, parent, _) in enumerate(joints):
        if parent >= 0:
            children[parent].append(i)

    lines = ["HIERARCHY"]
    # BVH motion rows list channels in the order joints appear in the
    # hierarchy (depth-first), not in SMPL index order — record that order.
    order = []

    def emit(idx, depth):
        order.append(idx)
        name, parent, off = joints[idx]
        ind = "  " * depth
        head = "ROOT" if parent < 0 else "JOINT"
        lines.append(f"{ind}{head} {name}")
        lines.append(f"{ind}{{")
        lines.append(f"{ind}  OFFSET {off[0]*SCALE:.6f} {off[1]*SCALE:.6f} {off[2]*SCALE:.6f}")
        if parent < 0:
            lines.append(f"{ind}  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation")
        else:
            lines.append(f"{ind}  CHANNELS 3 Zrotation Yrotation Xrotation")
        if children[idx]:
            for c in children[idx]:
                emit(c, depth + 1)
        else:
            lines.append(f"{ind}  End Site")
            lines.append(f"{ind}  {{")
            lines.append(f"{ind}    OFFSET 0.000000 0.000000 0.000000")
            lines.append(f"{ind}  }}")
        lines.append(f"{ind}}}")

    emit(0, 0)
    lines.append("MOTION")
    lines.append(f"Frames: {T}")
    lines.append(f"Frame Time: {1.0/fps:.6f}")

    eulers = axis_angle_to_euler_zyx(poses.reshape(-1, 3)).reshape(T, J, 3)
    trans_cm = trans * SCALE
    for t in range(T):
        row = [f"{trans_cm[t, 0]:.6f}", f"{trans_cm[t, 1]:.6f}", f"{trans_cm[t, 2]:.6f}"]
        for j in order:
            row.extend(f"{v:.6f}" for v in eulers[t, j])
        lines.append(" ".join(row))

    Path(path).write_text("\n".join(lines))
    print(f"Wrote {path}  ({T} frames, {J} joints @ {fps:.2f} fps)")


def video_fps(video_path, default=30.0):
    """Read frame rate from the source video via OpenCV; fall back to default."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        if fps and fps > 0:
            return float(fps)
    except Exception:
        pass
    return default


def run_gvhmr(video_path, output_root="outputs/demo", static_cam=True):
    """Invoke GVHMR's demo. Returns path to hmr4d_results.pt.

    demo.py writes to <output_root>/<video-stem>/hmr4d_results.pt.
    -s (static_cam) skips visual odometry — correct when the camera doesn't move
    and the fastest, most robust path. Drop it (static_cam=False) for moving cams,
    which uses the built-in SimpleVO (still no DPVO compile needed).
    """
    video_path = Path(video_path).resolve()
    name = video_path.stem
    root = Path(output_root)
    cmd = [
        sys.executable, "tools/demo/demo.py",
        "--video", str(video_path),
        "--output_root", str(root),
    ]
    if static_cam:
        cmd.append("-s")
    print(f"Running: {' '.join(cmd)}")
    # demo.py saves hmr4d_results.pt BEFORE its rendering step. Rendering builds an
    # SMPL preview mesh (make_smplx("smpl")) and crashes if only SMPL-X is present —
    # but we only need the .pt, not the preview. So don't treat a non-zero exit as
    # fatal; the real success check is whether the .pt was written.
    subprocess.run(cmd)
    pt = root / name / "hmr4d_results.pt"
    if not pt.exists():
        raise FileNotFoundError(
            f"Expected {pt} — GVHMR did not produce output. "
            f"Check the demo.py log above for errors."
        )
    return pt


def extract_motion(pt_path, frame="global"):
    """Load GVHMR's hmr4d_results.pt -> (poses [T,J,3], trans [T,3]).

    frame: 'global' (world-grounded, gravity-aligned — the default and the point
    of GVHMR) or 'incam' (camera frame). J is 22 for SMPL-X (pelvis + 21 body
    joints) or 24 for plain SMPL; detected from body_pose width.
    """
    import torch
    data = torch.load(str(pt_path), map_location="cpu", weights_only=False)

    key = f"smpl_params_{frame}"
    params = data.get(key) or data["smpl_params_global"]

    def to_np(x):
        return x.detach().cpu().numpy() if hasattr(x, "detach") else np.asarray(x)

    global_orient = to_np(params["global_orient"]).reshape(-1, 3)        # (T,3)
    Tn = global_orient.shape[0]
    body_pose = to_np(params["body_pose"]).reshape(Tn, -1, 3)            # (T,J-1,3)
    transl = to_np(params["transl"]).reshape(-1, 3)                      # (T,3)

    poses = np.concatenate([global_orient[:, None, :], body_pose], axis=1)  # (T,J,3)
    return poses, transl


# --- Human3R backend ----------------------------------------------------------
# Human3R (https://github.com/fanegg/Human3R) is a single feed-forward model that
# replaces GVHMR's YOLO->ViTPose->HMR2->GVHMR chain. Its output format differs from
# GVHMR's (see HUMAN3R.md for the full mapping and the open validation items):
#   * writes ONE .npz PER FRAME under <out_dir>/smpl/{f:06d}.npz (not one .pt)
#   * pose is a single stacked axis-angle array rotvec (N, 53, 3) per frame, where
#     N = people detected that frame and 53 = SMPL-X joints minus the 2 eyeballs:
#         [0]      global_orient
#         [1:22]   21 body joints        <- we keep these (+ pelvis = 22 total)
#         [22:37]  left hand  (dropped)
#         [37:52]  right hand (dropped)
#         [52]     jaw        (dropped)
#   * saved params are CAMERA frame; world-grounding is reconstructed from the c2w
#     pose in the sibling <out_dir>/camera/{f:06d}.npz (frame="world", below).
# Keeping pelvis + 21 body = 22 joints makes the result drop straight into
# write_bvh() with SMPL_JOINTS[:22] — the same skeleton the GVHMR path emits.
HUMAN3R_BODY_JOINTS = 22  # pelvis + 21 body joints


def _axis_angle_to_matrix(aa):
    """(N,3) axis-angle -> (N,3,3) rotation matrices (Rodrigues)."""
    aa = np.asarray(aa, dtype=float).reshape(-1, 3)
    n = aa.shape[0]
    theta = np.linalg.norm(aa, axis=-1)
    safe = np.where(theta < 1e-8, 1.0, theta)
    k = aa / safe[:, None]
    K = np.zeros((n, 3, 3))
    K[:, 0, 1], K[:, 0, 2] = -k[:, 2], k[:, 1]
    K[:, 1, 0], K[:, 1, 2] = k[:, 2], -k[:, 0]
    K[:, 2, 0], K[:, 2, 1] = -k[:, 1], k[:, 0]
    s = np.sin(theta)[:, None, None]
    c = np.cos(theta)[:, None, None]
    return np.eye(3)[None] + s * K + (1.0 - c) * (K @ K)


def _matrix_to_axis_angle(R):
    """(N,3,3) rotation matrices -> (N,3) axis-angle.

    Uses the standard trace/skew formula. The theta≈pi case (antipodal axis) is
    left to the small-angle branch's degradation — rare for a per-frame root
    orientation, but a known limitation flagged in HUMAN3R.md.
    """
    R = np.asarray(R, dtype=float).reshape(-1, 3, 3)
    tr = np.clip((R[:, 0, 0] + R[:, 1, 1] + R[:, 2, 2] - 1.0) / 2.0, -1.0, 1.0)
    theta = np.arccos(tr)
    axis = np.stack([R[:, 2, 1] - R[:, 1, 2],
                     R[:, 0, 2] - R[:, 2, 0],
                     R[:, 1, 0] - R[:, 0, 1]], axis=-1)
    small = theta < 1e-6
    denom = np.where(small, 1.0, 2.0 * np.sin(theta))
    aa = axis / denom[:, None] * theta[:, None]
    aa[small] = 0.0
    return aa


def run_human3r(video_path, output_root="outputs/human3r",
                model_path="src/human3r_896L.pth"):
    """Invoke Human3R's demo.py with --save. Returns the per-clip output dir.

    demo.py writes per-frame files straight into --output_dir (no per-video
    subfolder), so we give each clip its own <output_root>/<stem> dir to avoid
    collisions. Requires the checkpoint + SMPL-X body model in place (setup_human3r.sh).
    """
    video_path = Path(video_path).resolve()
    out_dir = Path(output_root) / video_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "demo.py",
        "--model_path", str(model_path),
        "--seq_path", str(video_path),
        "--size", "512",
        "--subsample", "1",
        "--use_ttt3r",
        "--vis_threshold", "2",
        "--downsample_factor", "1",
        "--reset_interval", "100",
        "--save",                       # REQUIRED — without it nothing is written
        "--output_dir", str(out_dir),
    ]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)
    if not any((out_dir / "smpl").glob("*.npz")):
        raise FileNotFoundError(
            f"Expected per-frame files in {out_dir/'smpl'} — Human3R produced no "
            f"output. Check the demo.py log above (did it run with --save?)."
        )
    return out_dir


def extract_motion_human3r(output_dir, frame="world", person="auto"):
    """Aggregate Human3R per-frame smpl/*.npz -> (poses [T,22,3] aa, trans [T,3]).

    frame:
        'world'  — gravity-aligned (Human3R's headline output). Reconstructed by
                   applying each frame's camera-to-world (c2w) pose to the root
                   orientation and translation. NOT yet validated on a real
                   sequence — see HUMAN3R.md; use 'camera' if it looks wrong.
        'camera' — the raw saved params. Correct as-is for a static camera up to a
                   constant global transform (which Blender/retargeting absorb).
    person:
        'auto'   — per frame, take the highest-`scores` detection. Robust for the
                   single-subject clips this pipeline targets; collapses
                   multi-person scenes to one track (with a warning). Cross-frame
                   identity tracking is NOT handled yet.
        <int>    — always take that row index.
    """
    output_dir = Path(output_dir)
    files = sorted((output_dir / "smpl").glob("*.npz"))
    if not files:
        raise FileNotFoundError(f"No smpl/*.npz under {output_dir}")

    cam_dir = output_dir / "camera"
    poses, trans = [], []
    multiperson, skipped, warned_no_cam = 0, 0, False

    for f in files:
        d = np.load(f, allow_pickle=True)
        rot = np.asarray(d["rotvec"]).reshape(-1, 53, 3)         # (N,53,3)
        tr = np.asarray(d["transl"]).reshape(-1, 3)              # (N,3)
        n = rot.shape[0]
        if n == 0:
            skipped += 1
            continue
        if n > 1:
            multiperson += 1

        if isinstance(person, int):
            i = min(max(person, 0), n - 1)
        elif "scores" in d.files:
            i = int(np.argmax(np.asarray(d["scores"]).reshape(-1)))
        else:
            i = 0

        body = rot[i, :HUMAN3R_BODY_JOINTS].copy()               # (22,3)
        transl = tr[i].copy()                                    # (3,)

        if frame == "world":
            cam_f = cam_dir / f.name
            if cam_f.exists():
                c2w = np.asarray(np.load(cam_f, allow_pickle=True)["pose"]).reshape(4, 4)
                Rw = c2w[:3, :3]
                body[0] = _matrix_to_axis_angle(Rw @ _axis_angle_to_matrix(body[0:1])[0])[0]
                transl = Rw @ transl + c2w[:3, 3]
            elif not warned_no_cam:
                print("WARNING: frame='world' but no camera/*.npz found; "
                      "using camera-frame params instead.")
                warned_no_cam = True

        poses.append(body)
        trans.append(transl)

    if multiperson:
        print(f"WARNING: {multiperson}/{len(files)} frames had >1 person; collapsed "
              f"to a single subject (person={person!r}). Verify the chosen track.")
    if skipped:
        print(f"NOTE: {skipped} frame(s) had no detected person and were skipped.")
    if not poses:
        raise RuntimeError(f"No usable frames extracted from {output_dir}")

    return np.stack(poses, axis=0), np.stack(trans, axis=0)      # (T,22,3), (T,3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="Path to input mp4")
    ap.add_argument("--out", default="animation.bvh", help="Output BVH file")
    ap.add_argument("--backend", choices=["gvhmr", "human3r"], default="gvhmr",
                    help="Motion estimator: gvhmr (validated default) or human3r "
                         "(single-model spike; see HUMAN3R.md).")
    ap.add_argument("--skip-infer", action="store_true",
                    help="Skip inference, just convert the already-saved output.")
    ap.add_argument("--incam", action="store_true",
                    help="Use camera-frame params instead of world-grounded.")
    ap.add_argument("--output-root", default=None,
                    help="Model output root. Defaults per backend "
                         "(outputs/demo for gvhmr, outputs/human3r for human3r).")
    # GVHMR-only
    ap.add_argument("--moving-cam", action="store_true",
                    help="[gvhmr] Camera moves: use SimpleVO instead of -s (static).")
    # Human3R-only
    ap.add_argument("--human3r-model", default="src/human3r_896L.pth",
                    help="[human3r] Checkpoint path.")
    ap.add_argument("--person", default="auto",
                    help="[human3r] Subject in multi-person clips: 'auto' "
                         "(highest score) or an integer row index.")
    args = ap.parse_args()

    name = Path(args.video).stem

    if args.backend == "gvhmr":
        output_root = args.output_root or "outputs/demo"
        if args.skip_infer:
            pt = Path(output_root) / name / "hmr4d_results.pt"
        else:
            pt = run_gvhmr(args.video, output_root=output_root,
                           static_cam=not args.moving_cam)
        poses, trans = extract_motion(pt, frame="incam" if args.incam else "global")
    else:  # human3r
        output_root = args.output_root or "outputs/human3r"
        person = int(args.person) if str(args.person).lstrip("-").isdigit() else args.person
        if args.skip_infer:
            out_dir = Path(output_root) / name
        else:
            out_dir = run_human3r(args.video, output_root=output_root,
                                  model_path=args.human3r_model)
        poses, trans = extract_motion_human3r(
            out_dir, frame="camera" if args.incam else "world", person=person)

    fps = video_fps(args.video)
    write_bvh(args.out, poses, trans, fps, SMPL_JOINTS[:poses.shape[1]])


if __name__ == "__main__":
    main()
