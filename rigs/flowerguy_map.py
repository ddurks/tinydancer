# Bone map for flowerguy_fullbody.armature, generated/verified via check_rig.py.
# Use:  blender --background your_scene.blend --python blender/retarget.py -- --map rigs/flowerguy_map.py
#
# This rig vs. stuntguy (danceguy_map.py):
#  - REQUIRES a 'pelvis' bone added as the common root, with 'back', 'hip.l' and
#    'hip.r' re-parented under it (Keep Offset, disconnected). flowerguy ships with
#    three separate roots (back / hip.l / hip.r); without a shared pelvis, retarget.py
#    only translates the torso and the legs stay anchored -> the mesh tears apart on
#    any root motion. With the pelvis added, Pelvis carries global translation to the
#    whole body; 'back' then handles torso rotation only.
#  - No collar/clavicle bones (arm.l/.r parent straight to back) -> L/R_Collar omitted.
#    Minor: SMPL-X shoulder motion just rides on the upper-arm bone instead.
#  - No toe bones -> L_Foot/R_Foot omitted (minor).
#  - Upper arm is 'arm.l/.r' (stuntguy used 'humerus.l/.r').
BONE_MAP = {
    'Pelvis':     'pelvis',     # <-- bone you add; common root over back + both hips
    'Spine1':     'back',
    'Neck':       'neck',
    'Head':       'head',
    'L_Shoulder': 'arm.l',
    'R_Shoulder': 'arm.r',
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
