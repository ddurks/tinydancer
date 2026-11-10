"""
cowboy_dance.py — retarget a WHAM_Source clip onto the Cowboy Advance cowboy
and export it as a clip-only glTF the game can play on its own cowboy.glb.

Run (no .blend needed; the cowboy is imported from the game's .glb):
  blender -b --python blender/cowboy_dance.py -- \\
      source.blend COWBOY_GLB OUT_GLB [ACTION_NAME] [FIRST LAST]
source.blend is blender_import.py's output (holds WHAM_Source); FIRST/LAST trim
to those source (video) frames. ACTION_NAME defaults to cowboy_dance.

The export holds just the CowboyRig armature and the one action, with the same
node names as cowboy.glb, so a Bevy AnimationClip loaded from it drives the
cowboy scene directly. The scene's fps is set to the source's, so keys land on
the video's own timing (no retime).
"""
import sys
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import retarget

argv = sys.argv[sys.argv.index("--") + 1:]
src_blend, cowboy_glb, out_glb = argv[0], argv[1], argv[2]
action_name = argv[3] if len(argv) > 3 else "cowboy_dance"

# 1. A clean file with the game's cowboy in it.
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(Path(cowboy_glb).resolve()))
rig = bpy.data.objects["CowboyRig"]

# 2. WHAM_Source and its action, at the source's frame rate.
with bpy.data.libraries.load(src_blend) as (data_from, data_to):
    data_to.objects = ["WHAM_Source"]
    data_to.scenes = data_from.scenes[:1]
src = data_to.objects[0]
sc = bpy.context.scene
sc.collection.objects.link(src)
sc.render.fps = data_to.scenes[0].render.fps
sc.render.fps_base = data_to.scenes[0].render.fps_base
# Only borrowed for its fps. Left in, it exports as a second, empty glTF scene,
# which Bevy's loader rejects ("missing field `nodes`").
bpy.data.scenes.remove(data_to.scenes[0])
f0, f1 = (int(v) for v in src.animation_data.action.frame_range)
if len(argv) > 5:
    f0, f1 = max(f0, int(argv[4])), min(f1, int(argv[5]))
sc.frame_start, sc.frame_end = f0, f1

retarget.main(["--map", str(HERE.parent / "rigs" / "cowboy_map.py"),
               "--target", "CowboyRig", "--action", action_name,
               "--root-scale", "auto", "--ground", "--face-front", "--drift", "1.5"])

# 3. Shift the keys to start at frame 0, so the clip starts at t = 0.
act = rig.animation_data.action
for layer in act.layers:
    for strip in layer.strips:
        for bag in strip.channelbags:
            for fc in bag.fcurves:
                for kp in fc.keyframe_points:
                    for p in (kp.co, kp.handle_left, kp.handle_right):
                        p.x -= f0
                fc.update()
sc.frame_start, sc.frame_end = 0, f1 - f0

# 4. Export only the armature and only this action.
for a in list(bpy.data.actions):
    if a != act:
        bpy.data.actions.remove(a)
for o in list(bpy.data.objects):
    if o != rig:
        bpy.data.objects.remove(o, do_unlink=True)
bpy.ops.object.select_all(action="DESELECT")
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.export_scene.gltf(
    filepath=str(Path(out_glb).resolve()),
    export_format="GLB",
    use_selection=True,
    export_animations=True,
    export_animation_mode="ACTIONS",
    export_skins=True,
)
print(f"COWBOY-DANCE {action_name}: {f1 - f0 + 1} frames @ {sc.render.fps / sc.render.fps_base:.2f} fps "
      f"-> {out_glb}")
