"""
bigcorp_dance.py — retarget a WHAM_Source clip onto BIGCORP's player rig
("Rig" in bigcorpguys.blend) as a new action, ready for BIGCORP's
tools/export_characters.py.

Run on a COPY of bigcorpguys.blend (the file is saved in place):
  blender -b work.blend --python blender/bigcorp_dance.py -- \
      source.blend ACTION_NAME VIDEO_FPS [FIRST LAST]
source.blend is blender_import.py's output (holds WHAM_Source); FIRST/LAST trim
to those source (video) frames.

Beyond retarget.py (run with rigs/bigcorpsman_map.py, --root-scale auto
--ground --face-front --drift 1.5), this rig needs:
  * IK legs. shin.* has an IK constraint to ik_foot.* (pole knee_target.*) and
    foot.* copies ik_foot.*'s rotation; the glTF export samples constraints, so
    plain FK keys would be overridden. ik_foot.* is keyed to the foot, and
    knee_target.* is solved per frame so the IK knee lands on the FK knee (the
    rig's ~90 degree pole_angle defeats any fixed placement).
  * Foot planting. A foot near the floor and nearly still is pinned to one
    spot with its sole on the floor, and the hips come down so the legs reach.
  * Retiming from the video's fps to the file's (24).
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent

argv = sys.argv[sys.argv.index("--") + 1:]
src_blend, action_name, video_fps = argv[0], argv[1], float(argv[2])
sys.path.insert(0, str(HERE))
import retarget

sc = bpy.context.scene
game_fps = sc.render.fps
old_range = (sc.frame_start, sc.frame_end)

# 1. Bring in WHAM_Source (and its action).
with bpy.data.libraries.load(src_blend) as (data_from, data_to):
    data_to.objects = ["WHAM_Source"]
src = data_to.objects[0]
sc.collection.objects.link(src)
f0, f1 = (int(v) for v in src.animation_data.action.frame_range)
if len(argv) > 4:
    f0, f1 = max(f0, int(argv[3])), min(f1, int(argv[4]))
sc.frame_start, sc.frame_end = f0, f1
sc.render.fps = int(video_fps)       # so retarget's --drift window is in real seconds

rig = bpy.data.objects["Rig"]
pb = rig.pose.bones
legs = [(pb["shin." + s].constraints["IK"], pb["foot." + s].constraints["Copy Rotation"])
        for s in "LR"]

# 2. FK retarget with the leg constraints off, so what's keyed is what's read back.
for ik, cr in legs:
    ik.mute = cr.mute = True
retarget.main(["--map", str(HERE.parent / "rigs" / "bigcorpsman_map.py"),
               "--target", "Rig", "--action", action_name,
               "--root-scale", "auto", "--ground", "--face-front", "--drift", "1.5"])
sc.render.fps = game_fps

# 2b. Plant the feet. A foot near the floor and nearly still is in contact
#     (gaps of a couple of frames filled, blips dropped). The hips come down by
#     how far the planted feet float, smoothed, so the legs can reach the floor;
#     then each contact is pinned to one spot with the sole on the floor,
#     blended in and out, and the IK targets below follow the pinned feet.
SOLE = 0.068                 # ankle/toe height above the sole at rest
NEAR, STILL = 0.05, 0.25     # contact: sole within 5cm of the floor, moving < 0.25 m/s
EASE = 4                     # frames to blend a pin in/out


def foot_samples():
    out = {}
    for f in range(f0, f1 + 1):
        sc.frame_set(f)
        for s in "LR":
            out[f, s] = pb["foot." + s].matrix.copy()
    return out


def sole(m):
    head = m.translation
    tail = head + m.to_3x3() @ Vector((0, pb["foot.L"].bone.length, 0))
    return min(head.z, tail.z) - SOLE


def contacts(fm):
    on = {}
    for s in "LR":
        flags = []
        for f in range(f0, f1 + 1):
            a, b = fm[max(f0, f - 1), s].translation, fm[min(f1, f + 1), s].translation
            speed = (b - a).xy.length / (2 / video_fps)
            flags.append(sole(fm[f, s]) < NEAR and speed < STILL)
        for i in range(1, len(flags) - 2):           # fill 1-2 frame gaps
            if not flags[i] and flags[i - 1] and (flags[i + 1] or flags[i + 2]):
                flags[i] = True
        segs, i = [], 0
        while i < len(flags):
            if flags[i]:
                j = i
                while j + 1 < len(flags) and flags[j + 1]:
                    j += 1
                if j - i + 1 >= 4:
                    segs.append((f0 + i, f0 + j))
                i = j + 1
            else:
                i += 1
        on[s] = segs
    return on


fm = foot_samples()
segs = contacts(fm)
# Hips down by the planted feet's float, interpolated across gaps and smoothed.
float_at = {}
for s in "LR":
    for a, b in segs[s]:
        for f in range(a, b + 1):
            float_at.setdefault(f, []).append(sole(fm[f, s]))
known = sorted(float_at)
if known:
    raw = []
    for f in range(f0, f1 + 1):
        if f in float_at:
            raw.append(sum(float_at[f]) / len(float_at[f]))
        else:
            lo = max((k for k in known if k < f), default=None); hi = min((k for k in known if k > f), default=None)
            vlo = sum(float_at[lo]) / len(float_at[lo]) if lo else None
            vhi = sum(float_at[hi]) / len(float_at[hi]) if hi else None
            raw.append(vlo if vhi is None else vhi if vlo is None else vlo + (vhi - vlo) * (f - lo) / (hi - lo))
    h = int(0.25 * video_fps)
    dz = [-max(0.0, sum(raw[max(0, i - h):i + h + 1]) / len(raw[max(0, i - h):i + h + 1])) for i in range(len(raw))]
    hips = pb["hips"]
    for f in range(f0, f1 + 1):
        sc.frame_set(f)
        m = hips.matrix.copy(); m.translation.z += dz[f - f0]; hips.matrix = m
        bpy.context.view_layer.update()
        hips.keyframe_insert("location", frame=f)
    print(f"Feet: {sum(len(v) for v in segs.values())} contacts; hips lowered "
          f"{-max(dz):.3f}-{-min(dz):.3f} m (median {-sorted(dz)[len(dz)//2]:.3f})")

fm = foot_samples()
segs = contacts(fm)
target, weight = dict(fm), {}
for s in "LR":
    for a, b in segs[s]:
        xs = sorted(fm[f, s].translation.x for f in range(a, b + 1))
        ys = sorted(fm[f, s].translation.y for f in range(a, b + 1))
        anchor = Vector((xs[len(xs) // 2], ys[len(ys) // 2]))
        for f in range(max(f0, a - EASE), min(f1, b + EASE) + 1):
            w = 1.0 if a <= f <= b else 1 - min(abs(f - a), abs(f - b)) / (EASE + 1)
            w = w * w * (3 - 2 * w)
            m = fm[f, s].copy()
            pinned = m.translation.copy()
            pinned.x, pinned.y = anchor.x, anchor.y
            pinned.z -= sole(m)
            m.translation = m.translation.lerp(pinned, w)
            if w > weight.get((f, s), 0):        # overlapping ramps: the stronger pin wins
                weight[f, s] = w
                target[f, s] = m
    print(f"  {s}: {len(segs[s])} contacts covering {sum(b - a + 1 for a, b in segs[s])} frames")

# 3. Key the IK controls so the constrained legs reproduce the FK ones:
#    ik_foot.* = the FK foot; knee_target.* off the FK knee, in the plane the
#    leg bends in (the foot's forward when the leg is near straight), turned
#    about the hip-ankle axis by an offset calibrated against the IK solve —
#    the rig's pole_angle means "in front of the knee" is not where it goes.
fk = {}
for f in range(f0, f1 + 1):
    sc.frame_set(f)
    for s in "LR":
        foot, ikf = pb["foot." + s], pb["ik_foot." + s]
        hip, knee, ankle = pb["thigh." + s].head.copy(), pb["shin." + s].head.copy(), foot.head.copy()
        line = (ankle - hip).normalized()
        bend = (knee - hip) - line * (knee - hip).dot(line)
        fwd = foot.matrix.to_3x3() @ Vector((0, 1, 0))
        fwd -= line * fwd.dot(line)
        d = bend.normalized() if bend.length > 0.03 else fwd.normalized()
        fk[f, s] = (knee, line, d, bend.length)
        ikf.matrix = target[f, s]
        bpy.context.view_layer.update()
        ikf.keyframe_insert("location", frame=f)
        ikf.keyframe_insert("rotation_quaternion", frame=f)
for ik, cr in legs:
    ik.mute = cr.mute = False


def place_pole(f, s, theta):
    knee, line, d, _ = fk[f, s]
    kt = pb["knee_target." + s]
    m = kt.matrix.copy()
    m.translation = knee + (Matrix.Rotation(theta, 3, line) @ d) * 0.4
    kt.matrix = m
    bpy.context.view_layer.update()


def knee_err(f, s, theta):
    place_pole(f, s, theta)
    return (pb["shin." + s].head - fk[f, s][0]).length


def solve(f, s, start):
    """Pole angle that puts the IK knee on the FK knee: local search from the
    previous frame's answer, falling back to a full sweep if that misses."""
    best = min((knee_err(f, s, start + math.radians(a)), start + math.radians(a))
               for a in range(-30, 31, 3))
    if best[0] > 0.01:
        best = min((knee_err(f, s, math.radians(a)), math.radians(a)) for a in range(0, 360, 5))
    for step in (1.0, 0.2):
        best = min((knee_err(f, s, best[1] + math.radians(step * i)), best[1] + math.radians(step * i))
                   for i in range(-4, 5))
    return best


theta, worst = {"L": 0.0, "R": 0.0}, 0.0
for f in range(f0, f1 + 1):
    sc.frame_set(f)
    for s in "LR":
        err, theta[s] = solve(f, s, theta[s])
        worst = max(worst, err)
        place_pole(f, s, theta[s])
        kt = pb["knee_target." + s]
        kt.keyframe_insert("location", frame=f)
        kt.keyframe_insert("rotation_quaternion", frame=f)
print(f"Knee poles solved per frame; worst knee error {worst:.4f} m")

# 4. Retime from video frames to the authoring file's fps, starting at frame 1.
act = rig.animation_data.action
k = game_fps / video_fps
for layer in act.layers:
    for strip in layer.strips:
        for bag in strip.channelbags:
            for fc in bag.fcurves:
                for kp in fc.keyframe_points:
                    for p in (kp.co, kp.handle_left, kp.handle_right):
                        p.x = 1 + (p.x - f0) * k
                fc.update()
act.use_fake_user = True
act.use_frame_range = True
act.frame_start, act.frame_end = 1, 1 + (f1 - f0) * k
print(f"DANCE-ACTION {act.name}: frames 1-{act.frame_end:.1f} @ {game_fps} fps "
      f"({(f1 - f0) / video_fps:.2f}s)")

# 5. Leave the file as it was found, plus the one action.
rig.animation_data.action = None
rig.animation_data.use_nla = True
src_action, src_data = src.animation_data.action, src.data
bpy.data.objects.remove(src, do_unlink=True)
bpy.data.actions.remove(src_action)
bpy.data.armatures.remove(src_data)
sc.frame_start, sc.frame_end = old_range
bpy.ops.wm.save_mainfile()
print("DANCE-SAVED", bpy.data.filepath)
