"""
dance_cues.py — find the moments a character should react during a dance:
musical BEATS (from the clip's audio) and KEY POSES (where the dancer hits a
pose: fast movement that snaps to a hold, from the GVHMR motion).

Run (any machine; needs numpy, scipy, librosa, torch, and ffmpeg on PATH):
  python dance_cues.py <clip>.hmr4d_results.pt <clip audio or video> --out cues.json
      [--start SEC] [--fps 30]

--start trims the audio to where the clip (and so the motion) begins, when the
audio comes from the untrimmed source video.

Output JSON (times in seconds from the start of the dance):
  {"duration": 8.5, "tempo": 117.2, "beats": [...], "keyposes": [...]}
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks
from scipy.spatial.transform import Rotation

from run_pipeline import extract_motion


def beat_times(audio_path, start, duration):
    import librosa

    # librosa can't decode every container; let ffmpeg hand it a plain WAV.
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", str(duration),
             "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "22050", str(wav)],
            check=True,
        )
        y, sr = librosa.load(wav, sr=None)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
    return float(np.atleast_1d(tempo)[0]), librosa.frames_to_time(frames, sr=sr).tolist()


def keypose_times(poses, fps, min_gap=0.35):
    """Frames where body motion drops sharply after a burst: the dancer 'hits'."""
    body = poses[:, 1:]                                   # ignore global orient (turning)
    T, J, _ = body.shape
    R = Rotation.from_rotvec(body.reshape(-1, 3)).as_quat().reshape(T, J, 4)
    # Angular speed per joint between frames, summed over the body (rad/s).
    dots = np.abs(np.sum(R[1:] * R[:-1], axis=-1)).clip(0, 1)
    speed = (2 * np.arccos(dots)).sum(axis=1) * fps
    speed = uniform_filter1d(speed, size=max(3, int(fps * 0.1)))
    # A hit = a local minimum of speed that follows a fast stretch.
    mins, _ = find_peaks(-speed, distance=max(1, int(min_gap * fps)))
    hi = np.percentile(speed, 70)
    window = int(fps * 0.3)
    hits = [m for m in mins
            if speed[max(0, m - window):m].max(initial=0) > hi and speed[m] < 0.6 * hi]
    return [round((m + 1) / fps, 3) for m in hits]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pt", help="GVHMR hmr4d_results.pt for the clip")
    ap.add_argument("audio", help="Audio or video file with the clip's soundtrack")
    ap.add_argument("--start", type=float, default=0.0, help="Clip start within the audio (s)")
    ap.add_argument("--fps", type=float, default=30.0, help="Frame rate of the motion")
    ap.add_argument("--out", default="cues.json")
    args = ap.parse_args()

    poses, _ = extract_motion(args.pt)
    duration = len(poses) / args.fps
    tempo, beats = beat_times(args.audio, args.start, duration)
    keyposes = keypose_times(poses, args.fps)
    cues = {
        "duration": round(duration, 3),
        "tempo": round(tempo, 1),
        "beats": [round(b, 3) for b in beats],
        "keyposes": keyposes,
    }
    Path(args.out).write_text(json.dumps(cues, indent=1))
    print(f"{len(beats)} beats @ {tempo:.0f} bpm, {len(keyposes)} key poses -> {args.out}")


if __name__ == "__main__":
    main()
