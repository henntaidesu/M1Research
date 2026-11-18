"""由 Meta Quest Pro 的 70 维 FACS 面部混合形状（Face Tracking）判定 7 类情绪。

Quest Pro 不开放机内摄像头原始图像，只提供抽象的 70 维 blendshape 权重（0~1，
Meta XR Core SDK `OVRPlugin.FaceExpression2` 顺序，即 OpenXR `XrFaceExpression2FB`）。
本模块把这些权重按 FACS 动作单元（Ekman）映射到 7 类情绪。

这是**规则式基线**（无需训练即可用）；将来可换成 train_multimodal_fer_vr.ipynb
训练出的 FEA 分支模型（接口保持 classify_fea(list[70]) -> dict 即可）。
"""
from . import labels

# 70 维官方顺序与官方名：Meta XR Core SDK `OVRPlugin.FaceExpression2`（Brow_Lowerer_L=0 …
# Tongue_Retreat=69）。头显侧直接取该枚举的 ToString()，两边逐字一致。
OVR_FACE_EXPRESSIONS = [
    "Brow_Lowerer_L", "Brow_Lowerer_R", "Cheek_Puff_L", "Cheek_Puff_R", "Cheek_Raiser_L",
    "Cheek_Raiser_R", "Cheek_Suck_L", "Cheek_Suck_R", "Chin_Raiser_B", "Chin_Raiser_T", "Dimpler_L",
    "Dimpler_R", "Eyes_Closed_L", "Eyes_Closed_R", "Eyes_Look_Down_L", "Eyes_Look_Down_R",
    "Eyes_Look_Left_L", "Eyes_Look_Left_R", "Eyes_Look_Right_L", "Eyes_Look_Right_R",
    "Eyes_Look_Up_L", "Eyes_Look_Up_R", "Inner_Brow_Raiser_L", "Inner_Brow_Raiser_R", "Jaw_Drop",
    "Jaw_Sideways_Left", "Jaw_Sideways_Right", "Jaw_Thrust", "Lid_Tightener_L", "Lid_Tightener_R",
    "Lip_Corner_Depressor_L", "Lip_Corner_Depressor_R", "Lip_Corner_Puller_L",
    "Lip_Corner_Puller_R", "Lip_Funneler_LB", "Lip_Funneler_LT", "Lip_Funneler_RB",
    "Lip_Funneler_RT", "Lip_Pressor_L", "Lip_Pressor_R", "Lip_Pucker_L", "Lip_Pucker_R",
    "Lip_Stretcher_L", "Lip_Stretcher_R", "Lip_Suck_LB", "Lip_Suck_LT", "Lip_Suck_RB",
    "Lip_Suck_RT", "Lip_Tightener_L", "Lip_Tightener_R", "Lips_Toward", "Lower_Lip_Depressor_L",
    "Lower_Lip_Depressor_R", "Mouth_Left", "Mouth_Right", "Nose_Wrinkler_L", "Nose_Wrinkler_R",
    "Outer_Brow_Raiser_L", "Outer_Brow_Raiser_R", "Upper_Lid_Raiser_L", "Upper_Lid_Raiser_R",
    "Upper_Lip_Raiser_L", "Upper_Lip_Raiser_R", "Tongue_Tip_Interdental", "Tongue_Tip_Alveolar",
    "Tongue_Front_Dorsal_Palate", "Tongue_Mid_Dorsal_Palate", "Tongue_Back_Dorsal_Velar",
    "Tongue_Out", "Tongue_Retreat",
]
_IDX = {name: i for i, name in enumerate(OVR_FACE_EXPRESSIONS)}
FEA_DIM = len(OVR_FACE_EXPRESSIONS)  # 70
# 旧版（FaceExpression v1）只有前 63 维；顺序与 70 维的前 63 维相同，末尾无 7 个舌头维度。
LEGACY_FEA_DIM = 63

# Meta 官方逐项说明，原文照录自
# https://developers.meta.com/horizon/documentation/native/android/move-ref-blendshapes/
# （Blendshape Visual Reference，XrFaceExpression2FB）。左右/分区成对的项官方共用一句。
_OFFICIAL_DESCRIPTIONS = [
    (("Brow_Lowerer_L", "Brow_Lowerer_R"), "knit and lower the brow area and lower central forehead."),
    (("Cheek_Puff_L", "Cheek_Puff_R"), "fill the cheeks with air causing them to round and extend outward."),
    (("Cheek_Raiser_L", "Cheek_Raiser_R"), "tighten the outer rings of the eye orbit and squeeze the lateral eye corners."),
    (("Cheek_Suck_L", "Cheek_Suck_R"), "suck the cheeks inward and against the teeth to create a hollow effect in the cheeks."),
    (("Chin_Raiser_B", "Chin_Raiser_T"), "push the skin of the chin and the lower lip upward. When the lips are touching, the upward force from the lower lip pushes up the top lip as well."),
    (("Dimpler_L", "Dimpler_R"), "pinch the lip corners against the teeth, drawing them slightly backward and often upward in the process."),
    (("Eyes_Closed_L", "Eyes_Closed_R"), "lower the top eyelid to cover the eye."),
    (("Eyes_Look_Down_L", "Eyes_Look_Down_R"), "move the eyelid consistent with downward gaze."),
    (("Eyes_Look_Left_L", "Eyes_Look_Left_R"), "move the eyelid consistent with leftward gaze."),
    (("Eyes_Look_Right_L", "Eyes_Look_Right_R"), "move the eyelid consistent with rightward gaze."),
    (("Eyes_Look_Up_L", "Eyes_Look_Up_R"), "move the eyelid consistent with upward gaze."),
    (("Inner_Brow_Raiser_L", "Inner_Brow_Raiser_R"), "lift the medial brow and forehead area."),
    (("Jaw_Drop",), "moves the lower mandible downward and toward the neck."),
    (("Jaw_Sideways_Left",), "moves the lower mandible leftward."),
    (("Jaw_Sideways_Right",), "moves the lower mandible rightward."),
    (("Jaw_Thrust",), "projects the lower mandible forward."),
    (("Lid_Tightener_L", "Lid_Tightener_R"), "tighten the rings around the eyelids and push the lower eyelid skin toward the inner eye corners."),
    (("Lip_Corner_Depressor_L", "Lip_Corner_Depressor_R"), "draw the lip corners downward."),
    (("Lip_Corner_Puller_L", "Lip_Corner_Puller_R"), "draw the lip corners up, back, and laterally."),
    (("Lip_Funneler_LB", "Lip_Funneler_LT", "Lip_Funneler_RB", "Lip_Funneler_RT"), "fan the lips outward in a forward projection, often rounding the mouth and separating the lips."),
    (("Lip_Pressor_L", "Lip_Pressor_R"), "press the upper and lower lips against one another."),
    (("Lip_Pucker_L", "Lip_Pucker_R"), "draw the lip corners medially causing the lips to protrude in the process."),
    (("Lip_Stretcher_L", "Lip_Stretcher_R"), "draw the lip corners laterally, stretching the lips and widening the jawline."),
    (("Lip_Suck_LB", "Lip_Suck_LT", "Lip_Suck_RB", "Lip_Suck_RT"), "suck the lips toward the inside of the mouth."),
    (("Lip_Tightener_L", "Lip_Tightener_R"), "narrow or constrict each lip on a horizontal plane."),
    (("Lips_Toward",), "forces contact between top and bottom lips to keep the mouth closed regardless of the position of the jaw."),
    (("Lower_Lip_Depressor_L", "Lower_Lip_Depressor_R"), "draw the lower lip downward and slightly laterally."),
    (("Mouth_Left",), "pulls the left lip corner leftward and pushes the right side of the mouth toward the left lip corner."),
    (("Mouth_Right",), "pulls the right lip corner rightward and pushes the left side of the mouth toward the right lip corner."),
    (("Nose_Wrinkler_L", "Nose_Wrinkler_R"), "lift the sides of the nose, nostrils, and central upper lip area. Often pairs with brow lowering muscles to lower the medial brow tips."),
    (("Outer_Brow_Raiser_L", "Outer_Brow_Raiser_R"), "lift the lateral brows and forehead areas."),
    (("Upper_Lid_Raiser_L", "Upper_Lid_Raiser_R"), "pull the top eyelid up and back to widen eyes."),
    (("Upper_Lip_Raiser_L", "Upper_Lip_Raiser_R"), "lift the top lip (in a more lateral manner than nose wrinkler)."),
    (("Tongue_Tip_Interdental",), "raises the tip of the tongue to touch the top teeth like with the viseme “TH”. The tongue is visible and slightly sticks out past the teeth line."),
    (("Tongue_Tip_Alveolar",), "raises the tip of the tongue to touch the back of the top teeth like in the viseme “NN”."),
    (("Tongue_Front_Dorsal_Palate",), "presses the front part of the tongue against the palate like in the viseme “CH”."),
    (("Tongue_Mid_Dorsal_Palate",), "presses the middle of the tongue against the palate like in the viseme “DD”."),
    (("Tongue_Back_Dorsal_Velar",), "presses the back of the tongue against the palate like in the viseme “KK”."),
    (("Tongue_Out",), "sticks the tongue out."),
    (("Tongue_Retreat",), "pulls the tongue back in the throat and makes the tongue stay down like in the viseme “AA”."),
]


def _official_subject(names):
    """按官方写法拼主语：A / A and B / A, B, C, and D。"""
    up = [n.upper() for n in names]
    if len(up) <= 2:
        return " and ".join(up)
    return ", ".join(up[:-1]) + ", and " + up[-1]


# name → 官方整句（含主语，如 "BROW_LOWERER_L and BROW_LOWERER_R knit and lower …"）
OVR_FACE_EXPRESSION_DESCRIPTIONS = {
    n: _official_subject(names) + " " + text
    for names, text in _OFFICIAL_DESCRIPTIONS for n in names
}
assert list(OVR_FACE_EXPRESSION_DESCRIPTIONS) == OVR_FACE_EXPRESSIONS

# FACS 动作单元组合 → 情绪（Ekman）。用组内 blendshape 均值作为该情绪激活强度。
_AU_GROUPS = {
    "happy":    ["Cheek_Raiser_L", "Cheek_Raiser_R", "Lip_Corner_Puller_L", "Lip_Corner_Puller_R", "Dimpler_L", "Dimpler_R"],
    "sad":      ["Inner_Brow_Raiser_L", "Inner_Brow_Raiser_R", "Lip_Corner_Depressor_L", "Lip_Corner_Depressor_R", "Lower_Lip_Depressor_L", "Lower_Lip_Depressor_R"],
    "surprise": ["Inner_Brow_Raiser_L", "Inner_Brow_Raiser_R", "Outer_Brow_Raiser_L", "Outer_Brow_Raiser_R", "Upper_Lid_Raiser_L", "Upper_Lid_Raiser_R", "Jaw_Drop"],
    "fear":     ["Inner_Brow_Raiser_L", "Inner_Brow_Raiser_R", "Outer_Brow_Raiser_L", "Outer_Brow_Raiser_R", "Brow_Lowerer_L", "Brow_Lowerer_R", "Upper_Lid_Raiser_L", "Upper_Lid_Raiser_R", "Lip_Stretcher_L", "Lip_Stretcher_R"],
    "angry":    ["Brow_Lowerer_L", "Brow_Lowerer_R", "Lid_Tightener_L", "Lid_Tightener_R", "Lip_Tightener_L", "Lip_Tightener_R", "Lip_Pressor_L", "Lip_Pressor_R"],
    "disgust":  ["Nose_Wrinkler_L", "Nose_Wrinkler_R", "Upper_Lip_Raiser_L", "Upper_Lip_Raiser_R", "Lip_Corner_Depressor_L", "Lip_Corner_Depressor_R"],
}

_EN_ORDER = ["angry", "disgust", "fear", "happy", "neutral", "sad", "surprise"]


def _group_score(bs, names):
    vals = [bs[_IDX[n]] for n in names if n in _IDX]
    return sum(vals) / len(vals) if vals else 0.0


def normalize_blendshapes(blendshapes) -> list[float]:
    """校验并统一成 FEA_DIM（70）维。

    旧版头显 APK / 旧会话文件只有 LEGACY_FEA_DIM（63）维：前 63 维顺序与 70 维一致，
    末尾补 7 个 0（舌头维度未采集）后照常使用。其它维度一律拒收。
    """
    n = len(blendshapes) if isinstance(blendshapes, (list, tuple)) else None
    if n == FEA_DIM:
        return [float(x) for x in blendshapes]
    if n == LEGACY_FEA_DIM:
        return [float(x) for x in blendshapes] + [0.0] * (FEA_DIM - LEGACY_FEA_DIM)
    raise ValueError(f"blendshapes 维度应为 {FEA_DIM}（或旧版 {LEGACY_FEA_DIM}），实际 {n if n is not None else '?'}")


def classify_fea(blendshapes) -> dict:
    """blendshapes: 70 维（或旧版 63 维）0~1 浮点列表，FaceExpression2 顺序。

    返回与图像识别路径同构的 face 结构：
        {"dominant_en", "dominant"(中文), "emotions": {中文标签: 百分比}}
    """
    bs = normalize_blendshapes(blendshapes)

    scores = {emo: _group_score(bs, names) for emo, names in _AU_GROUPS.items()}
    # 中性：整体表情越弱越中性
    max_other = max(scores.values()) if scores else 0.0
    scores["neutral"] = max(0.0, 1.0 - 1.6 * max_other)

    total = sum(scores.values()) or 1.0
    emotions_zh = {}
    for en in _EN_ORDER:
        pct = round(scores.get(en, 0.0) / total * 100, 2)
        emotions_zh[labels.to_zh(en)] = pct

    dominant_en = max(_EN_ORDER, key=lambda e: scores.get(e, 0.0))
    return {
        "dominant_en": dominant_en,
        "dominant": labels.to_zh(dominant_en),
        "emotions": emotions_zh,
    }
