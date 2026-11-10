# Skinning directly to the mocap skeleton (an alternative to retargeting)

Instead of retargeting the `WHAM_Source` motion onto your character's *own* rig
(`check_rig.py` → `BONE_MAP` → `retarget.py`/Rokoko), you can **skin your character
mesh directly to `WHAM_Source`** — the animated armature the BVH import already builds.
One skeleton, no bone map, no retargeter. This is the "bind the mesh to the mocap
skeleton" workflow.

## When to use which

| Character | Approach |
| --- | --- |
| **Unrigged mesh, roughly humanoid (≈SMPL) proportions** | **Direct-skin (this doc)** — much simpler |
| Unrigged but off-SMPL proportions (big head, stubby limbs), still humanoid topology | **Direct-skin after fitting the armature** (see below) |
| Already rigged (e.g. `danceguy`/`stuntguy`) | Retarget — keeps that rig; direct-skin would throw it away + re-skin |
| Non-humanoid topology (crab, quadruped, tail) | Neither — needs its own skeleton; SMPL motion doesn't map |
| Need a reusable control rig for hand-keying | Retarget |

**The load-bearing condition is proportions** — but it's *fixable*, see below. Automatic
weights only works if the `WHAM_Source` bones sit *inside* your mesh's limbs at bind time.
That's easiest when your mesh is already SMPL-proportioned — but you can also **fit the
armature to your mesh** instead of requiring the mesh to match SMPL.

### Fitting the armature to non-SMPL proportions

You are **not** stuck with SMPL's proportions. Mocap animation is stored as joint
**rotations** (+ root translation), not absolute joint positions — so you can change the
armature's rest-pose bone lengths/positions and the same rotation keyframes still drive it.
The limbs just pivot around joints placed where *your* character's joints are; the dance
survives, only the proportions change. Two levels of effort:

1. **Overall size (trivial):** uniform object-scale `WHAM_Source` so its height matches
   your mesh. Auto-weights tolerates *moderate* mismatch, so this is often enough.
2. **Per-limb proportions (fiddlier):** in Edit Mode, reposition joints (longer legs,
   shorter arms, …) to match your mesh. Keep each bone's **direction/roll** — change
   *lengths along the existing axis*; if you swing bone directions around, the stored
   rotations get reinterpreted and the pose shifts. Mind connected bones (parent tail =
   child head).

**Limits of scaling:** it adapts *proportions* within the same ~22-joint humanoid
topology. It can't add/remove joints — you can't scale a human skeleton into a crab, a
tail, or a quadruped (different skeleton → retargeting or a purpose-built rig). Big scaling
can also drift foot contact / root height (feet land in new spots since root translation is
absolute) — nudge root height or add a floor constraint. At *large* proportion differences,
fit-then-skin is essentially **manual retargeting**, so an already-rigged character still
favors the retarget path.

## Requirements

- Character is roughly SMPL-proportioned and humanoid.
- Mesh is in a **T-pose** that matches the armature's rest pose.
- You're OK with **Automatic Weights + a weight-paint cleanup pass** (armpits, crotch,
  shoulders usually need touch-up).

## Manual steps (Blender UI)

1. Open the `.blend` produced by `blender_import.py` (it holds the animated `WHAM_Source`).
2. Import your character mesh (`File → Import → FBX/glTF/OBJ`).
3. Select `WHAM_Source` → Object Data Properties → Skeleton → set **Rest Position**
   (so it shows the T-pose, not a mid-dance frame).
4. Scale/position your T-posed mesh so its limbs overlap the bones.
5. Select the **mesh first**, then shift-select the **armature** (armature = active) →
   `Ctrl+P → Armature Deform → With Automatic Weights`.
6. Set the armature back to **Pose Position** → your character dances.
7. Weight-paint cleanup where deformation pinches.

### Gotchas

- **Bind in the rest pose, not a posed frame** — armature deform is relative to the rest
  pose, so binding on frame 1 of the dance skins the mesh to a distorted pose.
- **SMPL's rest ≈ T-pose but not exactly** — arms sit a few degrees below horizontal
  (see the `SMPL_JOINTS` offsets in `run_pipeline.py`), so a *relaxed* T on your mesh
  matches best. `check_rig.py` already flags this T-vs-A distinction.
- **Scale/units** — the BVH is in cm; `blender_import.py` scales the armature on import,
  so size your mesh to the armature, not to abstract units.
- **Root translation** — the root bone carries the person's global motion through space;
  parenting means your mesh travels with it (usually what you want).

## Automate it with the Blender MCP server

The whole sequence is scriptable via `mcp__blender__execute_blender_code` against a
running Blender (MCP add-on connected). The **one step that needs a human eye (or a
fitting heuristic) is the mesh↔armature alignment/scale in step 4** — everything else is
deterministic operator calls, and MCP's screenshot/render tools make the verify-and-tweak
loop tight.

```python
import bpy

# Assumes the WHAM_Source .blend is open and your mesh is imported into the scene.
arm  = bpy.data.objects["WHAM_Source"]
mesh = bpy.data.objects["YourMesh"]          # set to your imported mesh's name

# 1. Rest position so the bind captures the T-pose, not a dance frame.
arm.data.pose_position = 'REST'
bpy.context.view_layer.update()

# 2. Rough auto-fit: scale the mesh so its height ≈ the armature's, and drop its
#    feet onto the armature root. Fine-alignment (limbs over bones) is the human/eyed
#    step — inspect with a screenshot before parenting.
#    (left as a deliberate manual/heuristic step)

# 3. Parent with automatic weights (mesh selected, armature active).
bpy.ops.object.select_all(action='DESELECT')
mesh.select_set(True)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.parent_set(type='ARMATURE_AUTO')

# 4. Back to pose position → the mesh now follows the animation.
arm.data.pose_position = 'POSE'
```

**What MCP handles vs. what a human does:**
- MCP (`execute_blender_code`): import, rest/pose toggle, `parent_set`, then the
  screenshot → check deformation → weight-paint-tweak loop (`get_screenshot_of_window_as_image`,
  `render_viewport_to_path`). Use `search_api_docs`/`get_python_api_docs` to confirm any op.
- Human (or a fitting heuristic MCP could run): the alignment/scale in step 2 — the one
  judgment call. Getting the mesh's rest T-pose to overlap the SMPL bones is what makes or
  breaks the auto-weights.

Prereqs: Blender is running with the MCP add-on connected, the `WHAM_Source` `.blend` is
open, and your character mesh is imported (or give MCP the path and let it import).

## Future helper: `skin_to_source.py`

A background script mirroring `blender_import.py`, so this becomes one command:

```bash
blender --background scene.blend --python skin_to_source.py -- character.fbx
# → import mesh, force Rest Position, rough auto-fit, parent Automatic Weights, save .blend
```

TODO — not yet written. The auto-fit heuristic (match mesh bbox height to the armature,
align feet) covers the common case; anything stylized still wants the manual alignment
above (or just use retargeting).
