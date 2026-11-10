# Bone map for bigcorpsman (~/blender/bigcorpguys.blend, armature "Rig"), the
# BIGCORP player character.
# Use it through blender/bigcorp_dance.py, which runs retarget.py with this map
# and then fixes up the IK legs (below) and plants the feet.
#
# Notes on this rig's structure:
#  - T-pose rest, faces -Y. `root` (non-deforming) sits at the floor; Pelvis
#    drives `hips`, which also takes the root translation.
#  - Three-bone spine: Spine1 -> spine, Spine3 -> chest; Spine2 isn't mapped.
#  - No collar bones: upper arms hang straight off the chest.
#  - Legs are IK-driven: shin.* has an IK constraint to ik_foot.* and foot.*
#    copies ik_foot.*'s rotation. The glTF export samples constraints, so the
#    FK legs this map keys get overridden unless ik_foot.* is keyed to follow
#    the FK feet (or the constraints are muted) before exporting.
BONE_MAP = {
    'Pelvis':     'hips',
    'Spine1':     'spine',
    'Spine3':     'chest',
    'Neck':       'neck',
    'Head':       'head',
    'L_Shoulder': 'upper_arm.L',
    'R_Shoulder': 'upper_arm.R',
    'L_Elbow':    'forearm.L',
    'R_Elbow':    'forearm.R',
    'L_Wrist':    'hand.L',
    'R_Wrist':    'hand.R',
    'L_Hip':      'thigh.L',
    'R_Hip':      'thigh.R',
    'L_Knee':     'shin.L',
    'R_Knee':     'shin.R',
    'L_Ankle':    'foot.L',
    'R_Ankle':    'foot.R',
}
