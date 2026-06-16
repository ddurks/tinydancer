"""
Blender helper: import the BVH and prep it for retargeting onto your own mesh.

Run from Blender's Scripting tab, OR headlessly:
    blender --background --python blender_import.py -- animation.bvh

The script:
  1. Imports the BVH with sensible scale + axis settings for SMPL output.
  2. Renames the armature to "WHAM_Source" so retargeters can find it.
  3. Leaves the scene ready for you to add your own mesh + retarget.

Retargeting onto your own humanoid:
  - Free option: install the Rokoko Studio Live Blender addon
    (https://github.com/Rokoko/rokoko-studio-live-blender) — has a built-in
    "Retarget Animation" panel. Set source = WHAM_Source, target = your rig,
    auto-build bone list, retarget.
  - Manual: add Copy Rotation constraints from each WHAM bone to the
    equivalent bone on your rig, then bake the action.
"""

import sys
from pathlib import Path

import bpy


def main(bvh_path, blend_path=None):
    bvh_path = str(Path(bvh_path).resolve())
    # Default: save a .blend right next to the BVH so it's ready to open.
    if blend_path is None:
        blend_path = str(Path(bvh_path).with_suffix(".blend"))
    else:
        blend_path = str(Path(blend_path).resolve())

    # Clean default cube + camera + light for a tidy workspace.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    bpy.ops.import_anim.bvh(
        filepath=bvh_path,
        global_scale=0.01,        # BVH is in cm, Blender scene is in meters.
        frame_start=1,
        use_fps_scale=False,
        update_scene_fps=True,
        update_scene_duration=True,
        axis_forward="-Z",
        axis_up="Y",
    )

    arm = bpy.context.selected_objects[0]
    arm.name = "WHAM_Source"
    arm.data.name = "WHAM_Source_Data"

    # Set the scene's playback range from the imported action (update_scene_duration
    # on the importer is unreliable — pin it explicitly so the timeline matches).
    if arm.animation_data and arm.animation_data.action:
        start, end = arm.animation_data.action.frame_range
        bpy.context.scene.frame_start = int(start)
        bpy.context.scene.frame_end = int(end)

    print(f"Imported {bvh_path} as armature '{arm.name}'")
    print(f"Frame range: {bpy.context.scene.frame_start} -> {bpy.context.scene.frame_end}")

    # Persist a .blend so the result can be opened directly (esp. headless runs).
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"Saved Blender file: {blend_path}")


if __name__ == "__main__":
    # Handle Blender's `--` argv separator.
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    if not argv:
        raise SystemExit(
            "Usage: blender --background --python blender_import.py -- "
            "animation.bvh [output.blend]")
    main(argv[0], argv[1] if len(argv) > 1 else None)
