"""
check_rig.py — does your character's armature have the bones needed to retarget
the WHAM_Source (SMPL-X) dance motion onto it?

Run one of:
  blender --background your_character.blend --python check_rig.py
  blender --background --python check_rig.py -- your_character.fbx
  # ...or paste into the Scripting tab of a .blend that has your character

It prints: every armature bone, a best-effort match for each SMPL joint we drive,
which REQUIRED joints are missing, a T-pose sanity note, and a ready-to-edit
BONE_MAP you can hand straight to retarget.py.
"""

import re
import sys
from pathlib import Path

import bpy

# (smpl_joint, include-tokens, exclude-tokens, sided?) — ordered head -> limbs.
SPEC = [
    ("Pelvis",     ["hips", "pelvis", "hip", "root", "cog"], ["upleg", "thigh", "femur"], False),
    ("Spine1",     ["spine", "abdomen", "back", "torso"], [],                      False),
    ("Spine2",     ["spine", "chest"],                 [],                          False),
    ("Spine3",     ["spine", "chest", "upperchest"],   [],                          False),
    ("Neck",       ["neck"],                           [],                          False),
    ("Head",       ["head"],                           ["headtop", "end", "nub"],   False),
    ("L_Collar",   ["shoulder", "clavicle", "collar"], ["humerus"],                 True),
    ("R_Collar",   ["shoulder", "clavicle", "collar"], ["humerus"],                 True),
    ("L_Shoulder", ["upperarm", "uparm", "arm", "humerus"], ["forearm", "lower", "fore"], True),
    ("R_Shoulder", ["upperarm", "uparm", "arm", "humerus"], ["forearm", "lower", "fore"], True),
    ("L_Elbow",    ["forearm", "lowerarm", "elbow", "ulna", "radius"], [],          True),
    ("R_Elbow",    ["forearm", "lowerarm", "elbow", "ulna", "radius"], [],          True),
    ("L_Wrist",    ["hand", "wrist"],                   ["finger", "thumb", "index", "middle", "ring", "pinky", "end"], True),
    ("R_Wrist",    ["hand", "wrist"],                   ["finger", "thumb", "index", "middle", "ring", "pinky", "end"], True),
    ("L_Hip",      ["upleg", "thigh", "upperleg", "femur"], [],                     True),
    ("R_Hip",      ["upleg", "thigh", "upperleg", "femur"], [],                     True),
    ("L_Knee",     ["shin", "calf", "lowerleg", "knee", "tibia", "leg"], ["upleg", "upperleg", "thigh", "femur"], True),
    ("R_Knee",     ["shin", "calf", "lowerleg", "knee", "tibia", "leg"], ["upleg", "upperleg", "thigh", "femur"], True),
    ("L_Ankle",    ["foot", "ankle"],                   ["toe", "ball"],            True),
    ("R_Ankle",    ["foot", "ankle"],                   ["toe", "ball"],            True),
    ("L_Foot",     ["toe", "ball", "toebase"],          [],                         True),
    ("R_Foot",     ["toe", "ball", "toebase"],          [],                         True),
]
CORE = {
    "Pelvis", "Spine1", "Neck", "Head",
    "L_Hip", "R_Hip", "L_Knee", "R_Knee", "L_Ankle", "R_Ankle",
    "L_Shoulder", "R_Shoulder", "L_Elbow", "R_Elbow", "L_Wrist", "R_Wrist",
}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower().split(":")[-1])


def side_of(name):
    n = name.lower().split(":")[-1]
    if "left" in n:
        return "L"
    if "right" in n:
        return "R"
    if re.search(r"[._\- ]l(\b|[0-9._\- ]|$)", n) or re.search(r"[._\-]l$", n):
        return "L"
    if re.search(r"[._\- ]r(\b|[0-9._\- ]|$)", n) or re.search(r"[._\-]r$", n):
        return "R"
    return None


def load_target(argv):
    """Import a file if given, then return the armature most likely to be the
    user's character (i.e. not our WHAM_Source)."""
    if argv:
        f = Path(argv[0]).resolve()
        ext = f.suffix.lower()
        if ext == ".blend":
            bpy.ops.wm.open_mainfile(filepath=str(f))
        elif ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=str(f))
        elif ext in (".glb", ".gltf"):
            bpy.ops.import_scene.gltf(filepath=str(f))
        else:
            raise SystemExit(f"Unsupported file type: {ext} (use .blend/.fbx/.glb)")
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not arms:
        raise SystemExit("No armature found. Is your character actually rigged? "
                         "If it's just a mesh, rig it first (e.g. Mixamo).")
    targets = [a for a in arms if a.name != "WHAM_Source"] or arms
    return targets[0]


def match_bones(bones):
    names = [b.name for b in bones]
    used = set()
    mapping = {}
    for smpl, inc, exc, sided in SPEC:
        want_side = None
        if sided:
            want_side = "L" if smpl.startswith("L_") else "R"
        best = None
        for nm in names:
            if nm in used:
                continue
            n = norm(nm)
            if not any(tok in n for tok in inc):
                continue
            if any(tok in n for tok in exc):
                continue
            if want_side and side_of(nm) != want_side:
                continue
            if want_side is None and side_of(nm) is not None:
                continue  # centerline joints (pelvis/spine/neck/head) skip L/R bones
            # prefer the shortest matching name (least likely a child/twist bone)
            if best is None or len(n) < len(norm(best)):
                best = nm
        if best:
            mapping[smpl] = best
            used.add(best)
    return mapping


def main():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    arm = load_target(argv)
    bones = arm.data.bones

    print("=" * 70)
    print(f"Armature: '{arm.name}'  ({len(bones)} bones)")
    print("=" * 70)
    print("All bones (name <- parent):")
    for b in bones:
        print(f"  - {b.name:24s} <- {b.parent.name if b.parent else '(root)'}")

    mapping = match_bones(bones)
    print("\n" + "-" * 70)
    print("SMPL joint  ->  matched target bone")
    print("-" * 70)
    missing_core = []
    for smpl, *_ in SPEC:
        tgt = mapping.get(smpl)
        flag = ""
        if tgt is None:
            tgt = "*** MISSING ***"
            if smpl in CORE:
                missing_core.append(smpl)
                flag = "  (REQUIRED)"
        print(f"  {smpl:12s} -> {tgt}{flag}")

    print("\n" + "=" * 70)
    if not missing_core:
        print("VERDICT: looks retargetable — all required joints matched.")
        print("Eyeball the mapping above; fix any wrong guesses, then use it in retarget.py.")
    else:
        print(f"VERDICT: {len(missing_core)} REQUIRED joint(s) unmatched: "
              f"{', '.join(missing_core)}")
        print("Either your rig names them unusually (edit BONE_MAP below by hand) or the")
        print("rig is missing them (re-rig — Mixamo gives a clean, complete humanoid).")

    # T-pose sanity: arms roughly horizontal => good for retargeting.
    def bone_dir_x(name):
        b = bones.get(name)
        if not b:
            return None
        v = (b.tail_local - b.head_local)
        return abs(v.x) / (v.length + 1e-9)
    lsh = bone_dir_x(mapping.get("L_Shoulder", "")) if mapping.get("L_Shoulder") else None
    if lsh is not None:
        pose = "T-pose-ish (arms out)" if lsh > 0.6 else "NOT a clean T-pose (arms down/forward)"
        print(f"Rest pose: {pose}. Retargeters calibrate best from a T-pose; if it's an")
        print("A-pose, set the source/target rest pose in the retargeter (or in retarget.py).")

    print("\n" + "-" * 70)
    print("BONE_MAP for retarget.py (copy/paste, fix any wrong lines):")
    print("-" * 70)
    print("BONE_MAP = {")
    for smpl, *_ in SPEC:
        tgt = mapping.get(smpl)
        if tgt is not None:
            print(f"    {smpl!r}: {tgt!r},")
        else:
            print(f"    # {smpl!r}: '???',   # fill in your bone name")
    print("}")


if __name__ == "__main__":
    main()
