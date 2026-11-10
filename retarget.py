"""
retarget.py — copy the WHAM_Source (SMPL-X) motion onto YOUR character's armature.
No addon required: it bakes the result directly as keyframes on your rig.

Run:
  blender --background your_scene.blend --python retarget.py -- --map BONE_MAP.py [options]
where your_scene.blend contains BOTH armatures:
  * WHAM_Source  (append it from animation.blend, or import animation.bvh into the file)
  * your character's rig

BONE_MAP comes from check_rig.py (it prints a ready-to-paste `BONE_MAP = {...}`).
Save that as a .py file and pass it with --map, or edit DEFAULT_MAP below.

Method (the standard "both in T-pose" transfer): for each mapped bone, take the
source bone's rotation *relative to its rest pose* and apply that same rotation to
the target bone's rest orientation, then keyframe. The root (Pelvis) also gets the
source's translation. This is rest-pose-aware, so different proportions/bone
directions are handled — what dedicated retargeters do internally.

Options for rigs that aren't human-proportioned (e.g. davebot):
  --action NAME     key into a NEW action NAME (leaves the rig's existing actions alone)
  --root-scale auto|<float>
                    scale root travel; "auto" = target leg length / source leg length
  --ground          shift the root so the feet rest on the target's rest-pose floor
  --face-front      cancel the source's average heading over the clip, so the
                    target faces the way its rest pose faces (world-grounded HMR
                    output has an arbitrary heading)
  --face-damp K     with --face-front: also cancel fraction K (0-1) of the source's
                    turning away from that average each frame, so the body stays
                    mostly front-facing (root travel keeps its original direction)
A BONE_MAP value may be a list: one source joint drives several target bones
(e.g. a rig with separate parentless hip bones, or several hand bones).

Assumes (the easy, common case):
  * both armatures at the world origin, unrotated, uniform scale;
  * both in a T-pose rest pose (check_rig.py tells you).
If your rig is an A-pose, fix its rest pose first, or use the Rokoko addon (it
calibrates rest poses for you). Always eyeball the result and expect to clean up
foot contact / minor offsets afterward.
"""

import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

SOURCE_NAME = "WHAM_Source"

# Fallback if --map isn't given (Mixamo example). check_rig.py prints yours.
DEFAULT_MAP = {
    "Pelvis": "mixamorig:Hips",
    "Spine1": "mixamorig:Spine",
    "Spine2": "mixamorig:Spine1",
    "Spine3": "mixamorig:Spine2",
    "Neck": "mixamorig:Neck",
    "Head": "mixamorig:Head",
    "L_Collar": "mixamorig:LeftShoulder",
    "R_Collar": "mixamorig:RightShoulder",
    "L_Shoulder": "mixamorig:LeftArm",
    "R_Shoulder": "mixamorig:RightArm",
    "L_Elbow": "mixamorig:LeftForeArm",
    "R_Elbow": "mixamorig:RightForeArm",
    "L_Wrist": "mixamorig:LeftHand",
    "R_Wrist": "mixamorig:RightHand",
    "L_Hip": "mixamorig:LeftUpLeg",
    "R_Hip": "mixamorig:RightUpLeg",
    "L_Knee": "mixamorig:LeftLeg",
    "R_Knee": "mixamorig:RightLeg",
    "L_Ankle": "mixamorig:LeftFoot",
    "R_Ankle": "mixamorig:RightFoot",
    "L_Foot": "mixamorig:LeftToeBase",
    "R_Foot": "mixamorig:RightToeBase",
}


def flag(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def load_map(argv):
    path = flag(argv, "--map")
    if path is None:
        return DEFAULT_MAP
    ns = {}
    exec(Path(path).resolve().read_text(), ns)
    return ns["BONE_MAP"]


def depth(bone):
    d = 0
    b = bone
    while b.parent:
        d += 1
        b = b.parent
    return d


def main(argv):
    bone_map = load_map(argv)

    src = bpy.data.objects.get(SOURCE_NAME)
    if not src:
        raise SystemExit(f"No '{SOURCE_NAME}' armature in this file. Append it from "
                         f"animation.blend, or import animation.bvh first.")
    tgts = [o for o in bpy.data.objects
            if o.type == "ARMATURE" and o.name != SOURCE_NAME]
    if not tgts:
        raise SystemExit("No target armature found alongside WHAM_Source.")
    tgt = tgts[0]
    print(f"Source: {src.name}   Target: {tgt.name}")

    # Validate the map against both rigs.
    pairs = []
    for smpl, tnames in bone_map.items():
        if smpl not in src.pose.bones:
            print(f"  skip {smpl}: not in source"); continue
        for tname in ([tnames] if isinstance(tnames, str) else tnames):
            if tname not in tgt.pose.bones:
                print(f"  skip {smpl}->{tname}: target bone missing"); continue
            pairs.append((smpl, tname))
    if not pairs:
        raise SystemExit("No valid bone pairs — check your BONE_MAP names.")
    # Parents first so each pose_bone.matrix solve sees an updated parent.
    pairs.sort(key=lambda p: depth(tgt.pose.bones[p[1]].bone))

    # Rest rotations (armature/object space), inverse cached for the source.
    src_rest_inv = {s: src.pose.bones[s].bone.matrix_local.to_3x3().inverted()
                    for s, _ in pairs}
    tgt_rest = {t: tgt.pose.bones[t].bone.matrix_local.to_3x3() for _, t in pairs}

    for pb in tgt.pose.bones:
        pb.rotation_mode = "QUATERNION"

    sc = bpy.context.scene
    f0, f1 = sc.frame_start, sc.frame_end
    root_smpl = "Pelvis" if "Pelvis" in dict(pairs) else None
    tmap = {}
    for smpl, tname in pairs:
        tmap.setdefault(smpl, []).append(tname)

    # Key into a fresh action so the rig's existing actions are untouched, and
    # switch off its NLA so other clips don't leak into what we read back.
    action_name = flag(argv, "--action")
    if action_name:
        tgt.animation_data_create()
        tgt.animation_data.action = bpy.data.actions.new(action_name)
        tgt.animation_data.use_nla = False

    # Root travel. Default: copy the source root position as-is. With
    # --root-scale, the target starts at its own rest root and moves by the
    # source's displacement from frame f0, scaled (auto = leg-length ratio).
    root_scale = flag(argv, "--root-scale")
    if root_scale == "auto":
        def leg(arm, hip, ankle):
            return (arm.pose.bones[ankle].bone.head_local - arm.pose.bones[hip].bone.head_local).length
        s_leg = leg(src, "L_Hip", "L_Ankle")
        t_leg = leg(tgt, tmap["L_Hip"][0], tmap["L_Ankle"][0])
        root_scale = t_leg / s_leg
        print(f"Root scale (auto, leg {t_leg:.3f} / {s_leg:.3f}): {root_scale:.3f}")
    elif root_scale is not None:
        root_scale = float(root_scale)
    if root_smpl and root_scale is not None:
        sc.frame_set(f0)
        src_root0 = src.pose.bones[root_smpl].matrix.translation.copy()
        tgt_root_rest = tgt.pose.bones[tmap[root_smpl][0]].bone.head_local.copy()

    # --face-front: a yaw about world Z applied to every bone's delta (and to
    # the root's travel) so the target's average forward over the clip matches
    # its rest forward. Averaging (not frame f0) keeps a dancer who starts
    # side-on facing front for most of the clip. Assumes the target rig faces
    # -Y at rest (Blender's front view).
    face = Matrix.Identity(3)
    face_damp = float(flag(argv, "--face-damp", 0))
    yaw_at = {}                              # frame -> source heading, for --face-damp
    if "--face-front" in argv and root_smpl:
        spb = src.pose.bones[root_smpl]
        f = Vector((0, 0, 0))
        for fr in range(f0, f1 + 1):
            sc.frame_set(fr)
            fwd = spb.matrix.to_3x3() @ src_rest_inv[root_smpl] @ Vector((0, -1, 0))
            fwd.z = 0
            if fwd.length > 1e-6:
                f += fwd.normalized()
                yaw_at[fr] = math.atan2(fwd.x, -fwd.y)
        yaw = math.atan2(f.x, -f.y)          # angle of f from -Y, about +Z
        face = Matrix.Rotation(-yaw, 3, "Z")
        print(f"Face front: removing {math.degrees(yaw):+.1f} deg average yaw"
              + (f", damping {face_damp:.0%} of turns" if face_damp else ""))

    def face_at(fr):
        if not face_damp or fr not in yaw_at:
            return face
        turn = math.remainder(yaw_at[fr] - yaw, math.tau)   # heading away from average
        return Matrix.Rotation(-face_damp * turn, 3, "Z") @ face

    def root_pos(spb, z_offset):
        if root_scale is None:
            p = face @ spb.matrix.translation
        else:
            p = tgt_root_rest + root_scale * (face @ (spb.matrix.translation - src_root0))
        p.z += z_offset
        return p

    # Lowest point of the target's feet, for --ground.
    foot_bones = [t for j in ("L_Ankle", "R_Ankle", "L_Foot", "R_Foot") for t in tmap.get(j, [])]

    def foot_floor(get_point):
        return min(min(get_point(t, "head").z, get_point(t, "tail").z) for t in foot_bones)

    def pose_frame(f, z_offset, key):
        sc.frame_set(f)
        face_f = face_at(f)
        for smpl, tname in pairs:
            spb = src.pose.bones[smpl]
            tpb = tgt.pose.bones[tname]
            delta = face_f @ spb.matrix.to_3x3() @ src_rest_inv[smpl]  # world delta from rest
            r_tgt = delta @ tgt_rest[tname]                    # apply to target rest
            m = r_tgt.to_4x4()
            m.translation = tpb.matrix.translation             # keep position...
            if smpl == root_smpl:
                m.translation = root_pos(spb, z_offset)        # ...except root: follow source
            tpb.matrix = m
            bpy.context.view_layer.update()                    # so children see this parent
            if key:
                tpb.keyframe_insert("rotation_quaternion", frame=f)
                if smpl == root_smpl:
                    tpb.keyframe_insert("location", frame=f)

    # --ground: a dry pass measures where the feet land, then the real pass
    # lifts/drops the whole clip so the typical lowest foot point (5th
    # percentile, robust to a stray frame) sits at the rest-pose floor.
    z_offset = 0.0
    if "--ground" in argv and root_smpl and foot_bones:
        lows = []
        for f in range(f0, f1 + 1):
            pose_frame(f, 0.0, key=False)
            lows.append(foot_floor(lambda t, end: getattr(tgt.pose.bones[t], end)))
        lows.sort()
        rest_floor = foot_floor(lambda t, end: getattr(tgt.pose.bones[t].bone, end + "_local"))
        z_offset = rest_floor - lows[len(lows) // 20]
        print(f"Ground offset: {z_offset:+.3f}")

    for f in range(f0, f1 + 1):
        pose_frame(f, z_offset, key=True)

    sc.frame_set(f0)
    print(f"Retargeted {len(pairs)} bones across frames {f0}-{f1} onto '{tgt.name}'.")

    out = bpy.data.filepath
    if out:
        bpy.ops.wm.save_mainfile()
        print(f"Saved {out}")


if __name__ == "__main__":
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    main(argv)
