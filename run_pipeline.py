"""
End-to-end: video.mp4 -> GVHMR inference -> BVH animation file.

GVHMR (https://github.com/zju3dv/GVHMR) replaced WHAM as the motion estimator:
better accuracy, no DPVO compile (SimpleVO by default), actively maintained. It
outputs SMPL-X params; this script converts them to a BVH that Blender imports as
an armature.

Usage (from inside the GVHMR/ directory, with setup done and checkpoints in place):
    python run_pipeline.py /path/to/video.mp4 [--out animation.bvh]

Output:
    - GVHMR raw output: outputs/demo/<video-name>/hmr4d_results.pt
    - BVH file:         animation.bvh  (drag into Blender, or use blender_import.py)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="Path to input mp4")
    ap.add_argument("--out", default="animation.bvh", help="Output BVH file")
    ap.add_argument("--skip-infer", action="store_true",
                    help="Skip inference, just convert existing hmr4d_results.pt")
    ap.add_argument("--moving-cam", action="store_true",
                    help="Camera moves: use SimpleVO instead of -s (static).")
    ap.add_argument("--incam", action="store_true",
                    help="Use camera-frame params instead of world-grounded.")
    ap.add_argument("--output-root", default="outputs/demo",
                    help="GVHMR output root (must match its demo.py).")
    args = ap.parse_args()

    if args.skip_infer:
        name = Path(args.video).stem
        pt = Path(args.output_root) / name / "hmr4d_results.pt"
    else:
        pt = run_gvhmr(args.video, output_root=args.output_root,
                       static_cam=not args.moving_cam)

    poses, trans = extract_motion(pt, frame="incam" if args.incam else "global")
    fps = video_fps(args.video)
    write_bvh(args.out, poses, trans, fps, SMPL_JOINTS[:poses.shape[1]])


if __name__ == "__main__":
    main()
