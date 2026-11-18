"""诱发条件与固定场景表。

条件按效价×唤醒四象限 + 中性划分（见 Docs/研究设计_FEA时序情绪识别与早期预测_2026-11-18.md §3.2），
不按 7 类：愤怒 / 厌恶很难用场景稳定诱发，7 类只留在自评里。

场景是**固定表**而不是 DeepSeek 动态生成：刺激要能复现、能写进论文方法部分。
每个场景两段文字——
  scene   全景内容（Qwen-Image 全景用，不写动作）
  motion  视频里什么在动（Wan I2V 用）；镜头一律固定，避免 VR 晕动
全部不出现人物（全景模型画人容易畸形，畸形本身会诱发厌恶，混淆条件）。
"""

CONDITIONS = [
    {"key": "HVHA", "zh": "高效价高唤醒", "ja": "快・高覚醒", "en": "Pleasant, high arousal",
     "valence": "high", "arousal": "high"},
    {"key": "HVLA", "zh": "高效价低唤醒", "ja": "快・低覚醒", "en": "Pleasant, low arousal",
     "valence": "high", "arousal": "low"},
    {"key": "LVHA", "zh": "低效价高唤醒", "ja": "不快・高覚醒", "en": "Unpleasant, high arousal",
     "valence": "low", "arousal": "high"},
    {"key": "LVLA", "zh": "低效价低唤醒", "ja": "不快・低覚醒", "en": "Unpleasant, low arousal",
     "valence": "low", "arousal": "low"},
    {"key": "NEU", "zh": "中性", "ja": "中立", "en": "Neutral",
     "valence": "mid", "arousal": "low"},
]
CONDITION_KEYS = [c["key"] for c in CONDITIONS]

SCENES = {
    "HVHA": [
        {"zh": "夜晚港口烟花大会",
         "scene": "a harbor at night during a grand fireworks festival, colorful fireworks filling the sky, reflections on the water, glowing lanterns along the pier",
         "motion": "fireworks bursting and sparkling in the sky, shimmering reflections on the water"},
        {"zh": "热闹的游乐园",
         "scene": "a bright amusement park on a sunny day, a colorful carousel and a ferris wheel, balloons and flags, candy stalls",
         "motion": "carousel slowly rotating, flags and balloons fluttering in the wind"},
        {"zh": "山顶日出云海",
         "scene": "a mountain summit at sunrise above a sea of clouds, golden sunlight bursting over the horizon, vivid sky",
         "motion": "clouds flowing below, sunlight rays shimmering, birds flying across the sky"},
        {"zh": "热带瀑布与彩虹",
         "scene": "a lush tropical jungle with a huge waterfall and a bright rainbow, vivid green plants and flowers",
         "motion": "waterfall pouring and splashing, mist drifting, leaves swaying"},
        {"zh": "极光下的雪原",
         "scene": "a snowy field under a spectacular green and purple aurora, starry sky, frozen lake",
         "motion": "aurora curtains waving and dancing across the sky, stars twinkling"},
        {"zh": "节日夜市灯笼",
         "scene": "a festive night market street decorated with hundreds of glowing red lanterns and confetti, colorful stalls",
         "motion": "lanterns swaying gently, confetti falling, lights twinkling"},
        {"zh": "花海热气球",
         "scene": "a vast flower field in full bloom with many colorful hot air balloons rising into a clear blue sky",
         "motion": "hot air balloons rising slowly, flowers swaying in the breeze"},
        {"zh": "海豚跃出的海面",
         "scene": "open turquoise ocean on a sunny day seen from a sailboat deck, splashing waves, seagulls",
         "motion": "waves rolling and splashing, dolphins leaping out of the water, seagulls flying"},
    ],
    "HVLA": [
        {"zh": "黄昏海滩",
         "scene": "a quiet tropical beach at sunset, soft golden light, palm trees, calm turquoise sea",
         "motion": "gentle waves lapping on the shore, palm leaves swaying softly"},
        {"zh": "春日草甸",
         "scene": "a peaceful green meadow with wildflowers on a mild spring day, soft white clouds, distant hills",
         "motion": "grass and flowers swaying gently in the breeze, clouds drifting slowly"},
        {"zh": "日式庭园锦鲤池",
         "scene": "a tranquil japanese garden with a koi pond, stone lanterns, maple trees, wooden bridge",
         "motion": "koi fish swimming slowly, water rippling softly, maple leaves drifting down"},
        {"zh": "清晨湖畔",
         "scene": "a calm lake at dawn surrounded by pine forest, light mist over the mirror-like water",
         "motion": "mist drifting slowly over the water, gentle ripples"},
        {"zh": "暖阳书房",
         "scene": "a cozy wooden reading room with warm afternoon sunlight, bookshelves, plants, a soft armchair",
         "motion": "sheer curtains moving gently in the breeze, dust particles floating in the sunlight"},
        {"zh": "竹林小径",
         "scene": "a serene bamboo forest path with soft dappled sunlight, moss covered stones",
         "motion": "bamboo leaves swaying gently, sunlight flickering softly through the leaves"},
        {"zh": "雪后木屋",
         "scene": "a quiet wooden cabin interior with a fireplace, a window view of a snowy forest, warm light",
         "motion": "fire flickering softly in the fireplace, snow falling gently outside the window"},
        {"zh": "薰衣草田",
         "scene": "a lavender field stretching to the horizon under a soft evening sky, a lone tree",
         "motion": "lavender swaying slowly in the breeze, clouds drifting"},
    ],
    "LVHA": [
        {"zh": "雷暴海面",
         "scene": "a small boat deck in a violent stormy sea at night, huge dark waves, black clouds, lightning",
         "motion": "lightning flashing, huge waves crashing, heavy rain pouring"},
        {"zh": "废弃医院走廊",
         "scene": "a dark abandoned hospital corridor with peeling walls, broken equipment, a single flickering light",
         "motion": "the light flickering on and off, dust drifting, a door slowly swinging"},
        {"zh": "森林大火",
         "scene": "a forest engulfed by a raging wildfire at dusk, red orange flames, thick smoke, burning trees",
         "motion": "flames spreading and roaring, smoke billowing, embers flying"},
        {"zh": "悬崖边缘",
         "scene": "the edge of a very high narrow cliff ledge above a deep foggy canyon, strong wind, dark clouds",
         "motion": "fog swirling in the canyon, small rocks falling, clouds racing"},
        {"zh": "深夜废弃地铁站",
         "scene": "a deserted subway station at midnight, broken lights, graffiti, dark tunnel entrance",
         "motion": "lights flickering, papers blowing across the floor, a train light approaching from the tunnel"},
        {"zh": "龙卷风逼近",
         "scene": "an open farmland under a dark green sky with a massive tornado approaching, debris in the air",
         "motion": "tornado spinning and approaching, debris flying, grass whipping in the wind"},
        {"zh": "坍塌的城市街道",
         "scene": "a city street after an earthquake, collapsed buildings, cracked road, smoke, sirens light",
         "motion": "dust and smoke rising, sparks falling from broken wires, red emergency lights flashing"},
        {"zh": "暗夜密林",
         "scene": "a dense dark forest at night, twisted trees, thick fog, faint moonlight",
         "motion": "fog creeping between trees, branches swaying, shadows moving"},
    ],
    "LVLA": [
        {"zh": "雨中墓园",
         "scene": "an old cemetery on a grey rainy day, weathered gravestones, bare trees, overcast sky",
         "motion": "light rain falling steadily, puddles rippling"},
        {"zh": "废弃的游乐场",
         "scene": "an abandoned rusty playground in a grey drizzle, empty swings, faded colors, overcast sky",
         "motion": "an empty swing moving slightly, drizzle falling"},
        {"zh": "空荡的病房",
         "scene": "an empty dim hospital room with a single bed, grey light from a window, withered flowers on a table",
         "motion": "curtains drifting slowly, rain streaks running down the window"},
        {"zh": "冬日荒原",
         "scene": "a desolate frozen wasteland in winter, dead trees, grey sky, a broken wooden fence",
         "motion": "light snow drifting, dead grass trembling in a cold wind"},
        {"zh": "雨夜空站台",
         "scene": "an empty rural train platform at night in the rain, a single dim lamp, wet benches",
         "motion": "rain falling under the lamp light, puddles rippling"},
        {"zh": "搬空的旧房间",
         "scene": "an empty old apartment room after moving out, bare walls, dusty floor, dim cloudy daylight",
         "motion": "dust floating in dim light, a curtain moving faintly"},
        {"zh": "阴天枯湖",
         "scene": "a dried up lake bed with cracked earth under a heavy overcast sky, a stranded old boat",
         "motion": "grey clouds drifting slowly, dust blowing lightly"},
        {"zh": "落叶的空公园",
         "scene": "an empty park in late autumn on a gloomy day, bare trees, scattered fallen leaves, empty benches",
         "motion": "fallen leaves drifting slowly, bare branches swaying"},
    ],
    "NEU": [
        {"zh": "普通办公室",
         "scene": "an ordinary modern office with desks, chairs, computers and fluorescent lights, daytime",
         "motion": "subtle ambient motion, a ceiling fan turning slowly"},
        {"zh": "白天停车场",
         "scene": "an empty outdoor parking lot on a mild cloudy day, painted lines, a few parked cars",
         "motion": "clouds drifting slowly"},
        {"zh": "图书馆阅览室",
         "scene": "a quiet library reading room with long tables, chairs and bookshelves, even lighting",
         "motion": "subtle ambient motion, light dust floating"},
        {"zh": "住宅区街道",
         "scene": "a plain residential street on an overcast afternoon, houses, sidewalk, street lamps",
         "motion": "tree leaves moving slightly, clouds drifting slowly"},
        {"zh": "会议室",
         "scene": "an empty meeting room with a long table, chairs, a whiteboard and window blinds, daytime",
         "motion": "window blinds moving slightly"},
        {"zh": "超市货架",
         "scene": "an empty supermarket aisle with neatly arranged shelves of products, bright even lighting",
         "motion": "subtle ambient motion"},
        {"zh": "大学走廊",
         "scene": "a university hallway with lockers, notice boards and windows, daytime",
         "motion": "subtle ambient motion, light shifting softly"},
        {"zh": "仓库内部",
         "scene": "the inside of a clean warehouse with metal shelves and cardboard boxes, daylight from skylights",
         "motion": "subtle ambient motion, light dust floating"},
    ],
}

# 360 LoRA 的触发前缀（与 qwen360_pano 工作流示例一致）
PANO_PREFIX = "360 panorama, equirectangular projection, full spherical, seamless, "
PANO_NEGATIVE = ("polar distortion, poles warping, pinched top, pinched bottom, blurry, low quality, "
                 "lowres, watermark, text, people, person, human, deformed")

# Wan 官方推荐的负面提示词 + 防镜头运动 / 防人物
VIDEO_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，"
                  "低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，"
                  "毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走, "
                  "camera movement, zoom, pan, shaky camera, people, person")


def condition(key: str) -> dict:
    for c in CONDITIONS:
        if c["key"] == key:
            return c
    raise KeyError(key)


def pano_prompt(cond_key: str, scene_idx: int) -> str:
    return PANO_PREFIX + SCENES[cond_key][scene_idx]["scene"]


def video_prompt(cond_key: str, scene_idx: int) -> str:
    s = SCENES[cond_key][scene_idx]
    return (f"{s['scene']}. {s['motion']}. static camera, fixed viewpoint, no camera movement, "
            "360 degree equirectangular panorama")


def hit(cond_key: str, valence: int, arousal: int) -> bool:
    """一次自评是否落在该条件的目标区域（操纵检验）。SAM 1–9：≥6 高，≤4 低；中性要求效价 4–6 且唤醒 ≤4。"""
    c = condition(cond_key)
    if c["valence"] == "mid":
        return 4 <= valence <= 6 and arousal <= 4
    v_ok = valence >= 6 if c["valence"] == "high" else valence <= 4
    a_ok = arousal >= 6 if c["arousal"] == "high" else arousal <= 4
    return v_ok and a_ok


def options() -> dict:
    return {
        "conditions": CONDITIONS,
        "scenes": {k: [{"idx": i, "zh": s["zh"], "scene": s["scene"], "motion": s["motion"]}
                       for i, s in enumerate(v)] for k, v in SCENES.items()},
    }
