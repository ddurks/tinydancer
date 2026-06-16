# Bone map for stuntguy.armature (anatomical naming), generated/verified via check_rig.py.
# Use:  blender --background your_scene.blend --python retarget.py -- --map stuntguy_map.py
#
# Notes on this rig's structural simplifications (see README / our chat):
#  - Root: SMPL "Pelvis" -> "back" (carries global translation + base orientation).
#    The rig has a single spine bone ("back"), so Spine1/2/3 are intentionally not
#    mapped; the torso follows the pelvis (slightly stiffer than a multi-bone spine).
#  - No toe bones -> L_Foot/R_Foot omitted (minor).
#  - Fingers/thumb are not driven (SMPL-X body export carries no finger motion anyway).
BONE_MAP = {
    'Pelvis':     'back',
    'Neck':       'neck',
    'Head':       'head',
    'L_Collar':   'shoulder.l',
    'R_Collar':   'shoulder.r',
    'L_Shoulder': 'humerus.l',
    'R_Shoulder': 'humerus.r',
    'L_Elbow':    'forearm.l',
    'R_Elbow':    'forearm.r',
    'L_Wrist':    'hand.l',
    'R_Wrist':    'hand.r',
    'L_Hip':      'femur.l',
    'R_Hip':      'femur.r',
    'L_Knee':     'tibia.l',
    'R_Knee':     'tibia.r',
    'L_Ankle':    'foot.l',
    'R_Ankle':    'foot.r',
}
