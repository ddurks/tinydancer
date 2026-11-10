# Bone map for the Cowboy Advance cowboy (../cowboyadvance/assets/models/cowboy.glb,
# armature "CowboyRig"; no source .blend, so import the .glb into Blender).
# Use:  blender --background scene.blend --python blender/retarget.py -- \
#           --map rigs/cowboy_map.py --target CowboyRig --action cowboy_dance \
#           --root-scale auto --ground --face-front --drift 1.5
#
# Notes on this rig's structure:
#  - T-pose rest, faces -Y, plain FK (no IK constraints). `root` sits at the
#    floor; Pelvis drives `hips`, which also takes the root translation.
#  - Three-bone spine: Spine1 -> spine, Spine3 -> chest; Spine2 isn't mapped.
#  - Big stylized head: `hat` hangs off `head` and isn't mapped, so it rides
#    along at its rest offset.
#  - Collar bones exist (shoulder.*) and take L/R_Collar.
BONE_MAP = {
    'Pelvis':     'hips',
    'Spine1':     'spine',
    'Spine3':     'chest',
    'Neck':       'neck',
    'Head':       'head',
    'L_Collar':   'shoulder.L',
    'R_Collar':   'shoulder.R',
    'L_Shoulder': 'arm_upper.L',
    'R_Shoulder': 'arm_upper.R',
    'L_Elbow':    'arm_lower.L',
    'R_Elbow':    'arm_lower.R',
    'L_Wrist':    'hand.L',
    'R_Wrist':    'hand.R',
    'L_Hip':      'leg_upper.L',
    'R_Hip':      'leg_upper.R',
    'L_Knee':     'leg_lower.L',
    'R_Knee':     'leg_lower.R',
    'L_Ankle':    'foot.L',
    'R_Ankle':    'foot.R',
}
