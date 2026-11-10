"""
dance_check.py — is this BVH a dance, or someone standing about?

  python motion/dance_check.py clip.bvh [--min 30]   # exit 0 = dance, 1 = not

Reads run_pipeline.py's BVH and scores how much the limbs move: for each frame,
the mean angular speed (deg/s) of the hips, knees, shoulders and elbows, then the
median of that over the clip. The median ignores a few frames of tracking jitter
and needs the movement to last most of the clip. Prints the score either way.

Measured on real takes (2026-11-10): nine dances scored 52–128; the 52 is a
slow posing dance. No non-dance take has been measured yet, so --min defaults
to 30: it only rejects someone barely moving, and is meant to be raised once
standing, talking and walking takes have been scored. It can't tell dancing from
walking or sport; the vision check that picks the clip does that.
Standard library only, so it runs on any box.
"""
import math
import statistics
import sys

LIMBS = ("L_Hip", "R_Hip", "L_Knee", "R_Knee",
         "L_Shoulder", "R_Shoulder", "L_Elbow", "R_Elbow")
MIN_SECONDS = 3.0


def read_bvh(path):
    """-> (frame_time, {joint: [(z, y, x) degrees per frame]})."""
    lines = open(path).read().splitlines()
    joints, offsets, n = [], {}, 0
    for i, line in enumerate(lines):
        words = line.split()
        if words and words[0] in ("ROOT", "JOINT"):
            joints.append(words[1])
        elif words and words[0] == "CHANNELS":
            chans = words[2:]
            offsets[joints[-1]] = (n, chans)
            n += len(chans)
        elif words and words[0] == "MOTION":
            frame_time = float(lines[i + 2].split()[-1])
            rows = [list(map(float, l.split())) for l in lines[i + 3:] if l.strip()]
            break
    rots = {}
    for j, (start, chans) in offsets.items():
        idx = [start + chans.index(c) for c in ("Zrotation", "Yrotation", "Xrotation")]
        rots[j] = [tuple(r[k] for k in idx) for r in rows]
    return frame_time, rots


def matrix(z, y, x):
    """Rotation matrix for BVH's Z-then-Y-then-X intrinsic Euler, degrees."""
    z, y, x = (math.radians(a) for a in (z, y, x))
    cz, sz, cy, sy, cx, sx = math.cos(z), math.sin(z), math.cos(y), math.sin(y), math.cos(x), math.sin(x)
    Rz = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))
    Ry = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    Rx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    mul = lambda A, B: tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    return mul(mul(Rz, Ry), Rx)


def angle_between(A, B):
    """Degrees of the rotation taking A to B."""
    trace = sum(A[k][i] * B[k][i] for i in range(3) for k in range(3))  # trace(Aᵀ B)
    return math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1) / 2))))


def score(path):
    """-> (median limb speed deg/s, seconds)."""
    frame_time, rots = read_bvh(path)
    mats = {j: [matrix(*r) for r in rots[j]] for j in LIMBS}
    frames = len(mats[LIMBS[0]])
    speeds = [statistics.fmean(angle_between(mats[j][f - 1], mats[j][f]) for j in LIMBS) / frame_time
              for f in range(1, frames)]
    return (statistics.median(speeds) if speeds else 0.0), frames * frame_time


def main(argv):
    minimum = float(argv[argv.index("--min") + 1]) if "--min" in argv else 30.0
    s, secs = score(argv[0])
    ok = s >= minimum and secs >= MIN_SECONDS
    print(f"dance_check: {s:.0f} deg/s over {secs:.1f}s (need {minimum:.0f} over {MIN_SECONDS:.0f}s) "
          f"-> {'dance' if ok else 'NOT a dance'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
