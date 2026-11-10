"""
dave_dance.py — retarget a WHAM_Source clip onto D.A.V.E. and export it as a
clip-only glTF the page can play on its own davebot.glb.

Run (no .blend needed; Dave is imported from the site's .glb):
  blender -b --python blender/dave_dance.py -- \\
      source.blend DAVEBOT_GLB OUT_GLB [ACTION_NAME]
source.blend is blender_import.py's output (holds WHAM_Source). ACTION_NAME
defaults to learned_dance.

The export holds just the Armature and the one action, with the same node
names as davebot.glb, so the page can retarget the clip onto Dave by name.
--drift keeps him dancing on the spot: he dances wherever he's standing in his
room, so a dancer who travels would walk him into the furniture.
"""
import sys
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import retarget

argv = sys.argv[sys.argv.index("--") + 1:]
src_blend, dave_glb, out_glb = argv[0], argv[1], argv[2]
action_name = argv[3] if len(argv) > 3 else "learned_dance"

# 1. A clean file with the site's Dave in it, minus his own clips.
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(Path(dave_glb).resolve()))
rig = bpy.data.objects["Armature"]
for a in list(bpy.data.actions):
    bpy.data.actions.remove(a)

# 2. WHAM_Source and its action, at the source's frame rate.
with bpy.data.libraries.load(src_blend) as (data_from, data_to):
    data_to.objects = ["WHAM_Source"]
    data_to.scenes = data_from.scenes[:1]
src = data_to.objects[0]
sc = bpy.context.scene
sc.collection.objects.link(src)
sc.render.fps = data_to.scenes[0].render.fps
sc.render.fps_base = data_to.scenes[0].render.fps_base
# Only borrowed for its fps; left in it exports as an empty second glTF scene.
bpy.data.scenes.remove(data_to.scenes[0])
f0, f1 = (int(v) for v in src.animation_data.action.frame_range)
sc.frame_start, sc.frame_end = f0, f1

retarget.main(["--map", str(HERE.parent / "rigs" / "davebot_map.py"),
               "--target", "Armature", "--action", action_name,
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
print(f"DAVE-DANCE {action_name}: {f1 - f0 + 1} frames @ {sc.render.fps / sc.render.fps_base:.2f} fps "
      f"-> {out_glb}")
