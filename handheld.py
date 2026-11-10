"""
handheld.py — give a clean render the hand-held shake of the source phone clip.

Tracks background features through the SOURCE video to recover the phone's
frame-to-frame motion (shift + roll), keeps only the jitter (the raw camera
path minus a smoothed one, so deliberate drift and pans are left out), and
applies it frame-for-frame to the RENDER, zoomed in just enough that the
shake never reveals an edge. The render's audio is kept.

Run (needs opencv-python, numpy, scipy, and ffmpeg on PATH):
  python handheld.py source.mp4 render.mp4 out.mp4 [--strength 1.0] [--smooth 0.5]
                                                   [--clip 90] [--soften 0.0]
                                                   [--source-start S --loop-fade F]

The render should match the source frame-for-frame — the whole source, or the
window starting --source-start seconds in (e.g. a loop cut by loop_clip.py).
--loop-fade crossfades the last F seconds of shake into the shake just before
that window, so a looping render's camera loops seamlessly too. The source's
shake is scaled to the render's resolution.
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d


def camera_path(video):
    """Cumulative (dx, dy, dangle) of the camera per frame, in source pixels."""
    cap = cv2.VideoCapture(str(video))
    ok, prev = cap.read()
    prev = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
    steps = [(0.0, 0.0, 0.0)]
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        pts = cv2.goodFeaturesToTrack(prev, maxCorners=400, qualityLevel=0.01, minDistance=20)
        step = (0.0, 0.0, 0.0)
        if pts is not None:
            nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev, gray, pts, None)
            good = status.ravel() == 1
            # RANSAC keeps the dominant (background) motion, not the dancer's.
            M, _ = cv2.estimateAffinePartial2D(pts[good], nxt[good], method=cv2.RANSAC,
                                               ransacReprojThreshold=2.0)
            if M is not None:
                step = (M[0, 2], M[1, 2], np.arctan2(M[1, 0], M[0, 0]))
        steps.append(step)
        prev = gray
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return np.cumsum(np.array(steps), axis=0), width, fps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="Hand-held clip to take the shake from")
    ap.add_argument("render", help="Clean render to shake")
    ap.add_argument("out")
    ap.add_argument("--strength", type=float, default=1.0, help="Scale the shake")
    ap.add_argument("--smooth", type=float, default=0.5,
                    help="Seconds of camera motion counted as intentional (removed)")
    ap.add_argument("--soften", type=float, default=0.0,
                    help="Smooth the shake over this many seconds: keeps the sway, "
                         "drops frame-to-frame jerks")
    ap.add_argument("--source-start", type=float, default=0.0,
                    help="Where the render's first frame is in the source (s)")
    ap.add_argument("--loop-fade", type=float, default=0.0,
                    help="Crossfade the shake's tail into its pre-roll (s) for a loop")
    ap.add_argument("--clip", type=float, default=90,
                    help="Softly limit each shake axis to this percentile of itself, "
                         "so a whip pan or tracking glitch doesn't jolt the render")
    args = ap.parse_args()

    path, src_w, fps = camera_path(args.source)
    shake = (path - gaussian_filter1d(path, sigma=args.smooth * fps, axis=0, mode="nearest"))
    limit = np.maximum(np.percentile(np.abs(shake), args.clip, axis=0), 1e-6)
    shake = limit * np.tanh(shake / limit)               # soft clip
    if args.soften:
        shake = gaussian_filter1d(shake, sigma=args.soften * fps, axis=0, mode="nearest")
    shake *= args.strength

    cap = cv2.VideoCapture(str(args.render))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    i0 = int(round(args.source_start * fps))
    window = shake[i0:i0 + n].copy()
    fade_n = int(round(args.loop_fade * fps))
    if fade_n:
        if i0 < fade_n:
            raise SystemExit("--loop-fade needs that much source before --source-start")
        for k in range(fade_n):
            i = len(window) - fade_n + k
            x = (k + 1) / fade_n
            w_ = x * x * (3 - 2 * x)                       # smoothstep
            window[i] = (1 - w_) * window[i] + w_ * shake[i0 + i - len(window)]
    shake = window
    out_fps = cap.get(cv2.CAP_PROP_FPS) or fps
    shake[:, :2] *= w / src_w                              # source px -> render px

    # Zoom so the largest shift + roll stays inside the frame.
    reach = (np.abs(shake[:, :2]).max(axis=0)
             + np.abs(shake[:, 2]).max() * np.array([h, w]) / 2)
    zoom = 1 + 2 * max(reach[0] / w, reach[1] / h) + 0.005
    print(f"shake: max shift {np.abs(shake[:, :2]).max(axis=0).round(1)} px, "
          f"max roll {np.degrees(np.abs(shake[:, 2]).max()):.2f} deg, zoom {zoom:.3f}")

    with tempfile.TemporaryDirectory() as tmp:
        silent = Path(tmp) / "shaken.mp4"
        writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (w, h))
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            dx, dy, da = shake[min(i, len(shake) - 1)]
            M = cv2.getRotationMatrix2D((w / 2, h / 2), np.degrees(da), zoom)
            M[:, 2] += (dx, dy)
            writer.write(cv2.warpAffine(frame, M, (w, h), flags=cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_REFLECT))
            i += 1
        writer.release()
        cap.release()
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(args.render),
             "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", args.out],
            check=True,
        )
    print(f"wrote {args.out} ({i} frames)")


if __name__ == "__main__":
    main()
