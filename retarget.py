"""
retarget.py — copy the WHAM_Source (SMPL-X) motion onto YOUR character's armature.
No addon required: it bakes the result directly as keyframes on your rig.

Run:
  blender --background your_scene.blend --python retarget.py -- --map BONE_MAP.py
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

Assumes (the easy, common case):
  * both armatures at the world origin, unrotated, uniform scale;
  * both in a T-pose rest pose (check_rig.py tells you).
If your rig is an A-pose, fix its rest pose first, or use the Rokoko addon (it
calibrates rest poses for you). Always eyeball the result and expect to clean up
foot contact / minor offsets afterward.
"""

import sys
from pathlib import Path

import bpy
from mathutils import Matrix

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


def load_map(argv):
    if "--map" in argv:
        path = Path(argv[argv.index("--map") + 1]).resolve()
        ns = {}
        exec(path.read_text(), ns)
        return ns["BONE_MAP"]
    return DEFAULT_MAP


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
    for smpl, tname in bone_map.items():
        if smpl not in src.pose.bones:
            print(f"  skip {smpl}: not in source"); continue
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

    for f in range(f0, f1 + 1):
        sc.frame_set(f)
        for smpl, tname in pairs:
            spb = src.pose.bones[smpl]
            tpb = tgt.pose.bones[tname]
            delta = spb.matrix.to_3x3() @ src_rest_inv[smpl]   # world delta from rest
            r_tgt = delta @ tgt_rest[tname]                    # apply to target rest
            m = r_tgt.to_4x4()
            m.translation = tpb.matrix.translation             # keep position...
            if smpl == root_smpl:
                m.translation = spb.matrix.translation         # ...except root: follow source
            tpb.matrix = m
            bpy.context.view_layer.update()                    # so children see this parent
            tpb.keyframe_insert("rotation_quaternion", frame=f)
            if smpl == root_smpl:
                tpb.keyframe_insert("location", frame=f)

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
