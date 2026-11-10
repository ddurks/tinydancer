"""
loop_clip.py — cut a dance into a seamless, beat-aligned loop.

Picks the window of --beats beats (on the clip's beat grid) whose end pose is
closest to its start pose, then crossfades the last --fade seconds of motion
into the moments just before the window starts, so the final frame flows
straight into the first (rotations slerped, root position blended). Optionally
cuts the matching audio with the same seam crossfade, and writes the window's
beat / key-pose cues shifted to start at 0.

Run (needs numpy, scipy, torch, soundfile, librosa; ffmpeg on PATH):
  python loop_clip.py <clip>.hmr4d_results.pt cues.json --out <clip>_loop.bvh
      [--beats 8] [--fade 0.4] [--fps N]
      [--audio source.mp4 --audio-start 5.0 --audio-out loop.m4a]
      [--cues-out loop_cues.json] [--window-out window.json]

cues.json comes from dance_cues.py. --audio-start is where the clip begins in
the audio file (as for dance_cues.py --start).
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from run_pipeline import extract_motion, video_fps, write_bvh


def beat_grid(beats, duration):
    """Detected beats extended at a steady period to cover the whole clip."""
    period = float(np.median(np.diff(beats)))
    first = beats[0] - period * np.floor(beats[0] / period)
    return np.arange(first, duration, period), period


def seam_cost(poses, trans, i0, i1):
    """How far the pose at the window's end is from its start."""
    a = Rotation.from_rotvec(poses[i1, 1:])
    b = Rotation.from_rotvec(poses[i0, 1:])
    joints = (a.inv() * b).magnitude().sum()
    travel = np.linalg.norm(trans[i1, [0, 2]] - trans[i0, [0, 2]])
    return joints + 2.0 * travel


def smoothstep(x):
    return x * x * (3 - 2 * x)


def crossfade_tail(poses, trans, i0, i1, fade_n):
    """Window frames [i0, i1), its last fade_n frames blended into the frames
    just before i0, so frame i1-1 becomes frame i0-1 and the loop is seamless."""
    p = poses[i0:i1].copy()
    t = trans[i0:i1].copy()
    n = i1 - i0
    for k in range(fade_n):
        i = n - fade_n + k                     # index within the window
        w = smoothstep((k + 1) / fade_n)
        src = i0 + i - n                       # matching frame before the window
        for j in range(p.shape[1]):
            ra = Rotation.from_rotvec(p[i, j])
            rb = Rotation.from_rotvec(poses[src, j])
            p[i, j] = (ra * Rotation.from_rotvec(w * (ra.inv() * rb).as_rotvec())).as_rotvec()
        t[i] = (1 - w) * t[i] + w * trans[src]
    return p, t


def loop_audio(audio, start, length, fade, out):
    import librosa
    import soundfile as sf

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        pre_s = min(fade, start)               # can't fade in from before the audio starts
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start - pre_s),
                        "-t", str(length + pre_s), "-i", str(audio), "-vn", str(wav)], check=True)
        y, sr = librosa.load(wav, sr=None, mono=False)
        y = np.atleast_2d(y)
        f = int(round(pre_s * sr))
        n = int(round(length * sr))
        if y.shape[1] < f + n:
            raise SystemExit(f"Audio ends {(f + n - y.shape[1]) / sr:.2f}s before the loop does.")
        pre, body = y[:, :f], y[:, f:f + n].copy()
        # Equal-power crossfade of the tail into the audio just before the start.
        if f:
            w = np.sin(np.linspace(0, np.pi / 2, f))
            body[:, -f:] = body[:, -f:] * np.cos(np.linspace(0, np.pi / 2, f)) + pre * w
        loop_wav = Path(tmp) / "loop.wav"
        sf.write(loop_wav, body.T, sr)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(loop_wav),
                        "-c:a", "aac", "-b:a", "192k", str(out)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pt")
    ap.add_argument("cues", help="cues.json from dance_cues.py (for the beat grid)")
    ap.add_argument("--out", required=True, help="Looped BVH")
    ap.add_argument("--beats", type=int, default=8, help="Loop length in beats")
    ap.add_argument("--fade", type=float, default=0.4, help="Seam crossfade (s)")
    ap.add_argument("--fps", type=float, help="Default: read from --audio if it's a video")
    ap.add_argument("--audio")
    ap.add_argument("--audio-start", type=float, default=0.0)
    ap.add_argument("--audio-out")
    ap.add_argument("--cues-out")
    ap.add_argument("--window-out")
    args = ap.parse_args()

    if not args.fps:
        args.fps = video_fps(args.audio, default=None) if args.audio else None
        if not args.fps:
            args.fps = 30.0
            print("WARNING: no frame rate from --audio; assuming 30 fps "
                  "(pass --fps if the clip isn't 30).")
    poses, trans = extract_motion(args.pt)
    cues = json.loads(Path(args.cues).read_text())
    duration = len(poses) / args.fps
    grid, period = beat_grid(cues["beats"], duration)
    length = args.beats * period
    fade_n = int(round(args.fade * args.fps))

    best = None
    for t0 in grid:
        i0 = int(round(t0 * args.fps))
        i1 = int(round((t0 + length) * args.fps))
        if i0 - fade_n < 0 or i1 >= len(poses):
            continue                           # need pre-roll to fade into, and the end frame
        cost = seam_cost(poses, trans, i0, i1)
        print(f"  window {t0:5.2f}-{t0 + length:5.2f}s  seam cost {cost:6.2f}")
        if best is None or cost < best[0]:
            best = (cost, t0, i0, i1)
    if best is None:
        raise SystemExit(f"Clip too short for a {args.beats}-beat loop with a {args.fade}s fade.")
    _, t0, i0, i1 = best
    print(f"Loop: {t0:.3f}-{t0 + length:.3f}s ({args.beats} beats @ {60 / period:.1f} bpm, "
          f"{i1 - i0} frames)")

    p, t = crossfade_tail(poses, trans, i0, i1, fade_n)
    write_bvh(args.out, p, t, args.fps)

    if args.audio and args.audio_out:
        loop_audio(args.audio, args.audio_start + i0 / args.fps, (i1 - i0) / args.fps,
                   args.fade, args.audio_out)
        print(f"Wrote {args.audio_out}")
    if args.cues_out:
        shift = lambda ts: [round(x - t0, 3) for x in ts if t0 <= x < t0 + length]
        Path(args.cues_out).write_text(json.dumps({
            "duration": round((i1 - i0) / args.fps, 3),
            "tempo": round(60 / period, 1),
            "beats": [round(b - t0, 3) for b in grid if t0 <= b < t0 + length - 1e-3],
            "keyposes": shift(cues["keyposes"]),
        }, indent=1))
        print(f"Wrote {args.cues_out}")
    if args.window_out:
        Path(args.window_out).write_text(json.dumps(
            {"start": round(i0 / args.fps, 4), "end": round(i1 / args.fps, 4),
             "fade": args.fade}, indent=1))
        print(f"Wrote {args.window_out}")


if __name__ == "__main__":
    main()
