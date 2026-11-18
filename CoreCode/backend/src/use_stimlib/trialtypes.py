"""试次模板：按刺激的 trial_class 决定一个试次各阶段多长、评什么（刺激重建方案 B.3）。

    immersive  注视 fixation_s → 刺激 stimulus_s（或刺激自带 duration_s）→ 完整 SAM → 恢复 recovery_s
               （时长全部取自实验流程的设置，即原来的图片线行为）
    event      注视随机 2–4 s（按 运行种子+试次 确定）→ 刺激 duration_s（默认 12）→ 快评（12 s 超时）→ 恢复 6 s
    mimicry    注视 2 s → 片段 duration_s（默认 8）→ 快评（10 s 超时）→ 恢复 3 s

protocol.json 的 "templates" 可以覆盖：{"event": {"stimulus_s": 10, "fixation_s": [3, 5]}, ...}；
值为 None 的键表示「沿用实验流程设置」。评分方式以刺激自身的 rating 为准（full / quick / none），
none 时跳过评分阶段。高唤醒延长恢复、FEA 覆盖率与重看对所有模板都生效（在 playback 里）。
"""
import random

KEYS = ("fixation_s", "stimulus_s", "rating_timeout_s", "recovery_s")

TEMPLATES = {
    "immersive": {"fixation_s": None, "stimulus_s": None, "rating_timeout_s": None, "recovery_s": None},
    "event": {"fixation_s": [2.0, 4.0], "stimulus_s": 12.0, "rating_timeout_s": 12.0, "recovery_s": 6.0},
    "mimicry": {"fixation_s": 2.0, "stimulus_s": 8.0, "rating_timeout_s": 10.0, "recovery_s": 3.0},
}
RATING_EST_S = {"full": 15.0, "quick": 4.0, "none": 0.0}     # 估算整场时长用


def template(trial_class: str, overrides: dict | None = None) -> dict:
    base = dict(TEMPLATES.get(trial_class) or TEMPLATES["immersive"])
    base.update({k: v for k, v in ((overrides or {}).get(trial_class) or {}).items() if k in KEYS})
    return base


def clean_overrides(raw) -> dict:
    """校验 protocol.json 的 templates：只认已知类别与键；fixation_s 可以是 [lo, hi]。"""
    out = {}
    for cls, d in (raw or {}).items():
        if cls not in TEMPLATES or not isinstance(d, dict):
            raise ValueError(f"templates 里的类别只能是 {' / '.join(TEMPLATES)}")
        o = {}
        for k, v in d.items():
            if k not in KEYS:
                raise ValueError(f"templates.{cls} 只能设 {', '.join(KEYS)}")
            if v is None:
                o[k] = None
            elif k == "fixation_s" and isinstance(v, (list, tuple)):
                lo, hi = sorted(max(0.0, min(3600.0, float(x))) for x in v[:2])
                o[k] = [lo, hi]
            else:
                o[k] = max(0.0, min(3600.0, float(v)))
        out[cls] = o
    return out


def _fix(v, rng) -> float:
    if isinstance(v, (list, tuple)):
        return round(rng.uniform(float(v[0]), float(v[1])), 3)
    return float(v)


def plan(stim: dict, st: dict, seed: int = 0, trial: int = 0) -> dict:
    """一个试次各阶段时长：{fixation_s, stimulus_s, rating, rating_timeout_s(None = 一直等), recovery_s}。

    st 是本场设置（SEQ_DEFAULTS + 实验流程），模板里为 None 的键沿用它。
    """
    cls = stim.get("trial_class") or "immersive"
    t = template(cls, st.get("templates"))
    rng = random.Random(f"{seed}:{trial}:fix")
    fix = st["fixation_s"] if t["fixation_s"] is None else _fix(t["fixation_s"], rng)
    dur = float(stim.get("duration_s") or 0)
    if dur <= 0:
        dur = st["stimulus_s"] if t["stimulus_s"] is None else float(t["stimulus_s"])
    rating = stim.get("rating") or "full"
    rto = st["rating_timeout_s"] if t["rating_timeout_s"] is None else float(t["rating_timeout_s"])
    rec = st["recovery_s"] if t["recovery_s"] is None else float(t["recovery_s"])
    return {"trial_class": cls, "fixation_s": float(fix), "stimulus_s": float(dur), "rating": rating,
            "rating_timeout_s": float(rto) or None, "recovery_s": float(rec)}


def estimate_s(stim: dict, st: dict) -> float:
    """单个试次的大致时长（随机注视取中值，评分按 RATING_EST_S，有超时的取较小者）。"""
    p = plan(stim, st)
    t = template(p["trial_class"], st.get("templates"))
    if isinstance(t["fixation_s"], (list, tuple)):
        p["fixation_s"] = (float(t["fixation_s"][0]) + float(t["fixation_s"][1])) / 2
    r = RATING_EST_S.get(p["rating"], 15.0)
    if p["rating_timeout_s"]:
        r = min(r, p["rating_timeout_s"])
    return p["fixation_s"] + p["stimulus_s"] + r + p["recovery_s"]
