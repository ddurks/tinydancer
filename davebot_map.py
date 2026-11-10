# Bone map for davebot (../dave/body/blender/davebot.blend, armature "Armature").
# Use:  blender --background scene.blend --python retarget.py -- \
#           --map davebot_map.py --action dance --root-scale auto --ground
#
# Notes on this rig's structure:
#  - Three parentless roots share the pelvis point: "back" (spine chain) and
#    "hip.l"/"hip.r" (leg chains). Pelvis drives all three so the legs stay
#    attached to the torso; each also gets the root translation.
#  - Two spine bones: Spine1/2 aren't mapped, "upperback" follows Spine3 (chest).
#  - No wrist bone: the four hand.* bones hang off radius, so L/R_Wrist drives
#    all four and the hand turns as one piece. Fingers aren't driven.
#  - Legs are short for the body (hip height 1.11 of ~4 units), hence
#    --root-scale auto (leg-length ratio) and --ground.
#  - tibia IK constraints ("gait_ik_preview") are muted in the file; leave them
#    muted or they override the retargeted legs.
BONE_MAP = {
    'Pelvis':     ['back', 'hip.l', 'hip.r'],
    'Spine3':     'upperback',
    'Neck':       'neck',
    'Head':       'head',
    'L_Collar':   'shoulder.l',
    'R_Collar':   'shoulder.r',
    'L_Shoulder': 'humerus.l',
    'R_Shoulder': 'humerus.r',
    'L_Elbow':    'radius.l',
    'R_Elbow':    'radius.r',
    'L_Wrist':    ['hand.pinkie.l', 'hand.middle.l', 'hand.index.l', 'hand.thumb.l'],
    'R_Wrist':    ['hand.pinkie.r', 'hand.middle.r', 'hand.index.r', 'hand.thumb.r'],
    'L_Hip':      'femur.l',
    'R_Hip':      'femur.r',
    'L_Knee':     'tibia.l',
    'R_Knee':     'tibia.r',
    'L_Ankle':    'foot.l',
    'R_Ankle':    'foot.r',
    'L_Foot':     'toe.l',
    'R_Foot':     'toe.r',
}
