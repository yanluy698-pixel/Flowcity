"""Semantic intent taxonomy for FlowCity.

This module is intentionally local and deterministic. The LLM chooses a
primary intent and sub-scenario; this taxonomy expands that choice into stable
Chinese tags and weights for Stage 3 scoring.
"""

from __future__ import annotations

from typing import Any


EXPLICIT_PREFERENCE_BOOST = 18.0
EXPLICIT_AVOID_PENALTY = -24.0

UNKNOWN_PRIMARIES = {"unknown", "casual_meetup"}

SUB_SCENARIOS = {
    "general",
    "first_meet",
    "romantic_step",
    "interactive_date",
    "bestie_tea",
    "brother_vent",
    "business_casual",
    "active_carnival",
    "brain_battle",
    "night_feast",
    "kid_care",
    "kid_energy_drain",
    "senior_care",
    "family_reunion",
    "landmark_checkin",
    "local_food_hunt",
    "casual",
    "unknown",
}


INTENT_TAXONOMY: dict[str, dict[str, Any]] = {
    "light_date.general": {
        "preferred": ["自然不尴尬", "低压力", "轻约会"],
        "avoid": ["尴尬正式"],
        "weights": {"自然不尴尬": 7, "低压力": 6, "轻约会": 6},
        "avoidWeights": {"尴尬正式": -8},
        "limits": {"physicalIntensityMax": "level_medium", "noiseMax": "moderate"},
    },
    "light_date.first_meet": {
        "preferred": ["自然不尴尬", "低压力", "得体有格调", "明亮宽敞", "轻约会"],
        "avoid": ["尴尬正式", "过度昏暗", "高噪高动", "快餐简餐"],
        "weights": {"自然不尴尬": 8, "低压力": 6, "得体有格调": 6, "明亮宽敞": 4, "轻约会": 6},
        "avoidWeights": {"尴尬正式": -10, "过度昏暗": -6, "高噪高动": -8, "快餐简餐": -7},
        "limits": {"physicalIntensityMax": "level_light", "noiseMax": "moderate"},
    },
    "light_date.romantic_step": {
        "preferred": ["浪漫轻松", "暧昧微醺", "精致出片", "私密感", "轻约会"],
        "avoid": ["灯火通明", "排队久", "快餐简餐", "高噪高动"],
        "weights": {"浪漫轻松": 9, "暧昧微醺": 7, "精致出片": 6, "私密感": 6, "轻约会": 6},
        "avoidWeights": {"灯火通明": -6, "排队久": -8, "快餐简餐": -8, "高噪高动": -8},
        "limits": {"physicalIntensityMax": "level_light", "noiseMax": "moderate"},
    },
    "light_date.interactive_date": {
        "preferred": ["高互动", "新潮打卡", "自然不尴尬", "话题感", "轻约会"],
        "avoid": ["死板无聊", "高难度挫败", "无法交流"],
        "weights": {"高互动": 8, "新潮打卡": 5, "自然不尴尬": 7, "话题感": 6, "轻约会": 5},
        "avoidWeights": {"死板无聊": -7, "高难度挫败": -7, "无法交流": -10},
        "limits": {"physicalIntensityMax": "level_medium", "noiseMax": "moderate"},
    },
    "deep_talk.general": {
        "preferred": ["可坐下聊天", "低压力"],
        "avoid": ["无法聊天"],
        "weights": {"可坐下聊天": 8, "低压力": 6},
        "avoidWeights": {"无法聊天": -12},
        "limits": {"physicalIntensityMax": "level_light", "noiseMax": "moderate"},
    },
    "deep_talk.bestie_tea": {
        "preferred": ["安静慢聊", "精致出片", "治愈放松", "低压力", "可坐下聊天"],
        "avoid": ["烟雾缭绕", "催翻台", "高噪重口", "无法聊天"],
        "weights": {"安静慢聊": 10, "精致出片": 5, "治愈放松": 6, "低压力": 6, "可坐下聊天": 8},
        "avoidWeights": {"烟雾缭绕": -8, "催翻台": -8, "高噪重口": -8, "无法聊天": -12},
        "limits": {"physicalIntensityMax": "level_zero", "noiseMax": "quiet"},
    },
    "deep_talk.brother_vent": {
        "preferred": ["低压力", "放松舒缓", "不拘束", "烟火气", "兄弟局风气", "可坐下聊天"],
        "avoid": ["过度精致", "高档尴尬", "菜量少"],
        "weights": {"低压力": 8, "放松舒缓": 6, "不拘束": 6, "烟火气": 5, "兄弟局风气": 6, "可坐下聊天": 6},
        "avoidWeights": {"过度精致": -6, "高档尴尬": -8, "菜量少": -5},
        "limits": {"physicalIntensityMax": "level_zero", "noiseMax": "moderate"},
    },
    "deep_talk.business_casual": {
        "preferred": ["安静慢聊", "高端得体", "私密防打扰", "设施便利", "可坐下聊天"],
        "avoid": ["儿童吵闹", "网红排队", "大声喧哗", "高噪高动"],
        "weights": {"安静慢聊": 10, "高端得体": 7, "私密防打扰": 8, "设施便利": 5, "可坐下聊天": 7},
        "avoidWeights": {"儿童吵闹": -8, "网红排队": -8, "大声喧哗": -9, "高噪高动": -9},
        "limits": {"physicalIntensityMax": "level_zero", "noiseMax": "quiet"},
    },
    "group_bonding.general": {
        "preferred": ["适合多人", "不拘束"],
        "avoid": [],
        "weights": {"适合多人": 8, "不拘束": 6},
        "avoidWeights": {},
        "limits": {"physicalIntensityMax": "level_medium", "noiseMax": "moderate"},
    },
    "group_bonding.active_carnival": {
        "preferred": ["高噪互动", "痛快释放", "兄弟局风气", "身体解压", "高互动"],
        "avoid": ["文艺安静", "过度斯文", "坐着发呆"],
        "weights": {"高噪互动": 8, "痛快释放": 8, "兄弟局风气": 7, "身体解压": 7, "高互动": 7},
        "avoidWeights": {"文艺安静": -7, "过度斯文": -6, "坐着发呆": -6},
        "limits": {"physicalIntensityMax": "level_high", "noiseMax": "noisy"},
    },
    "group_bonding.brain_battle": {
        "preferred": ["高互动", "沉浸感", "话题感", "团队协作", "安静慢聊"],
        "avoid": ["体力暴晒", "无法深度交流", "快餐打卡"],
        "weights": {"高互动": 8, "沉浸感": 7, "话题感": 6, "团队协作": 7, "安静慢聊": 4},
        "avoidWeights": {"体力暴晒": -8, "无法深度交流": -8, "快餐打卡": -5},
        "limits": {"physicalIntensityMax": "level_medium", "noiseMax": "moderate"},
    },
    "group_bonding.night_feast": {
        "preferred": ["烟火气", "兄弟局风气", "高噪互动", "性价比极高", "适合多人"],
        "avoid": ["安静高档", "分量极少", "催促限时"],
        "weights": {"烟火气": 8, "兄弟局风气": 7, "高噪互动": 6, "性价比极高": 7, "适合多人": 7},
        "avoidWeights": {"安静高档": -7, "分量极少": -7, "催促限时": -6},
        "limits": {"physicalIntensityMax": "level_zero", "noiseMax": "noisy"},
    },
    "family_care.general": {
        "preferred": ["老少咸宜", "安全性高"],
        "avoid": ["危险器械"],
        "weights": {"老少咸宜": 8, "安全性高": 8},
        "avoidWeights": {"危险器械": -12},
        "limits": {"physicalIntensityMax": "level_medium", "noiseMax": "moderate"},
    },
    "family_care.kid_care": {
        "preferred": ["亲子适龄", "亲子照顾", "安全性高"],
        "avoid": ["儿童不适龄", "危险器械"],
        "weights": {"亲子适龄": 10, "亲子照顾": 8, "安全性高": 8},
        "avoidWeights": {"儿童不适龄": -14, "危险器械": -12},
        "limits": {"physicalIntensityMax": "level_medium", "noiseMax": "moderate"},
    },
    "family_care.kid_energy_drain": {
        "preferred": ["亲子适龄", "亲子照顾", "释放精力", "安全性高", "宽敞无障"],
        "avoid": ["深度静音", "危险器械", "拥挤暴晒", "无处落座", "高噪高动"],
        "weights": {"亲子适龄": 10, "亲子照顾": 9, "释放精力": 8, "安全性高": 8, "宽敞无障": 6},
        "avoidWeights": {"深度静音": -6, "危险器械": -12, "拥挤暴晒": -10, "无处落座": -8, "高噪高动": -5},
        "limits": {"physicalIntensityMax": "level_medium", "noiseMax": "moderate"},
    },
    "family_care.senior_care": {
        "preferred": ["低体力友好", "少折腾", "少排队免等", "服务温和", "可坐下聊天"],
        "avoid": ["大排长龙", "爬山登高", "网红噪杂", "新潮难懂", "高噪高动"],
        "weights": {"低体力友好": 10, "少折腾": 9, "少排队免等": 8, "服务温和": 6, "可坐下聊天": 5},
        "avoidWeights": {"大排长龙": -10, "爬山登高": -12, "网红噪杂": -8, "新潮难懂": -6, "高噪高动": -8},
        "limits": {"physicalIntensityMax": "level_light", "noiseMax": "moderate"},
    },
    "family_care.family_reunion": {
        "preferred": ["家庭温馨", "宽敞舒适", "高端得体", "老少咸宜", "适合多人"],
        "avoid": ["逼仄局促", "西餐分餐", "重口刺激"],
        "weights": {"家庭温馨": 8, "宽敞舒适": 7, "高端得体": 5, "老少咸宜": 9, "适合多人": 7},
        "avoidWeights": {"逼仄局促": -8, "西餐分餐": -5, "重口刺激": -5},
        "limits": {"physicalIntensityMax": "level_light", "noiseMax": "moderate"},
    },
    "tourist_sightseeing.general": {
        "preferred": ["路线清晰", "城市可达"],
        "avoid": ["通勤内耗太高"],
        "weights": {"路线清晰": 7, "城市可达": 7},
        "avoidWeights": {"通勤内耗太高": -8},
        "limits": {"physicalIntensityMax": "level_medium", "noiseMax": "moderate"},
    },
    "tourist_sightseeing.landmark_checkin": {
        "preferred": ["游客地标", "西安特色风貌", "精致出片", "文化底蕴", "城市记忆点"],
        "avoid": ["毫无特色", "钻写字楼网吧", "通勤内耗太高"],
        "weights": {"游客地标": 10, "西安特色风貌": 8, "精致出片": 5, "文化底蕴": 7, "城市记忆点": 8},
        "avoidWeights": {"毫无特色": -8, "钻写字楼网吧": -10, "通勤内耗太高": -8},
        "limits": {"physicalIntensityMax": "level_medium", "noiseMax": "moderate"},
    },
    "tourist_sightseeing.local_food_hunt": {
        "preferred": ["本地市井老饕", "烟火气", "性价比极高", "地道风味", "西安特色风貌"],
        "avoid": ["网红连锁", "西式摆盘", "价格虚高"],
        "weights": {"本地市井老饕": 9, "烟火气": 7, "性价比极高": 6, "地道风味": 8, "西安特色风貌": 6},
        "avoidWeights": {"网红连锁": -6, "西式摆盘": -5, "价格虚高": -8},
        "limits": {"physicalIntensityMax": "level_light", "noiseMax": "moderate"},
    },
}


PRIMARY_DEFAULT_SUB_SCENARIO = {
    "light_date": "general",
    "deep_talk": "general",
    "group_bonding": "general",
    "tourist_sightseeing": "general",
    "family_care": "general",
    "casual_meetup": "casual",
    "unknown": "unknown",
}


INTENT_AUDIENCE_TAGS = {
    "light_date": ["轻约会"],
    "deep_talk": ["可坐下聊天", "安静慢聊"],
    "group_bonding": ["朋友多人", "适合多人"],
    "tourist_sightseeing": ["游客"],
    "family_care": ["亲子", "家庭", "老少咸宜"],
}

PRIMARY_KEYWORD_RULES = [
    ("family_care", ("孩子", "带娃", "亲子", "老人", "爸妈", "长辈", "父母")),
    ("deep_talk", ("坐下来聊", "坐下聊", "聊一会", "聊天", "深聊", "慢聊", "安静聊")),
    ("light_date", ("暧昧", "喜欢的女生", "喜欢的人", "追求", "约会", "轻约会", "对象", "不油腻", "不要太正式")),
    ("tourist_sightseeing", ("咸阳", "进西安", "西安市区", "景点", "地标", "钟楼", "城墙", "大雁塔", "大唐不夜城", "游客")),
    ("group_bonding", ("朋友", "同学", "兄弟", "男生", "多人", "聚会", "桌游", "台球", "密室")),
]

SCENE_PRIMARY_RULES = {
    "family": "family_care",
    "elderly": "family_care",
    "couple": "light_date",
    "friends": "group_bonding",
}

RELATIONSHIP_PRIMARY_RULES = {
    "ambiguous": "light_date",
    "pursuing": "light_date",
    "friends": "group_bonding",
    "family": "family_care",
}

SUB_SCENARIO_KEYWORD_RULES = {
    "family_care": [
        ("senior_care", ("老人", "爸妈", "父母", "长辈", "少走路", "走不动")),
        ("family_reunion", ("团聚", "全家", "一家人", "聚餐")),
        ("kid_energy_drain", ("放电", "释放精力", "消耗精力", "玩到累", "跑跳", "蹦床")),
        ("kid_care", ("孩子", "带娃", "亲子", "小孩", "宝宝")),
        ("general", ()),
    ],
    "deep_talk": [
        ("business_casual", ("商务", "客户", "谈事", "灵感", "工作")),
        ("brother_vent", ("兄弟", "哥们", "死党")),
        ("bestie_tea", ("闺蜜", "密友", "姐妹", "喝茶")),
        ("general", ()),
    ],
    "light_date": [
        ("romantic_step", ("微醺", "升温", "私密", "浪漫")),
        ("interactive_date", ("手作", "互动", "桌游", "一起玩")),
        ("first_meet", ("第一次", "初次", "刚认识", "第一次见")),
        ("general", ()),
    ],
    "tourist_sightseeing": [
        ("local_food_hunt", ("小吃", "老街", "本地寻味", "地道风味", "泡馍", "凉皮")),
        ("landmark_checkin", ("地标", "打卡", "第一次来", "景点")),
        ("general", ()),
    ],
    "group_bonding": [
        ("active_carnival", ("释放", "运动", "流汗", "发泄", "蹦床", "攀岩")),
        ("night_feast", ("喝酒", "大排档", "夜宵", "烟火聚餐")),
        ("brain_battle", ("桌游", "密室", "烧脑", "解谜", "协作")),
        ("general", ()),
    ],
}

EVIDENCE_KEYWORDS = {
    "family_care": ("孩子", "带娃", "亲子", "老人", "爸妈", "长辈", "父母", "体力", "少走路", "不想走太多路", "不能爬楼"),
    "deep_talk": ("坐下来聊", "坐下聊", "聊一会", "聊天", "深聊", "慢聊", "安静聊"),
    "light_date": ("暧昧", "喜欢的女生", "喜欢的人", "追求", "约会", "轻约会", "对象", "不油腻", "不要太正式"),
    "tourist_sightseeing": ("咸阳", "进西安", "西安市区", "景点", "地标", "钟楼", "城墙", "大雁塔", "大唐不夜城", "游客"),
    "group_bonding": ("朋友", "同学", "兄弟", "男生", "多人", "聚会", "桌游", "台球", "密室"),
}

TAG_ALIASES = {
    "大排档": ["大排档", "市井大排档", "烟火气", "烧烤"],
    "市井大排档": ["市井大排档", "大排档", "烟火气", "烧烤"],
    "烤肉": ["烤肉", "烧烤", "烟火气"],
    "火锅": ["火锅", "重口刺激", "烟火气"],
    "复古": ["复古情调", "得体有格调"],
    "安静": ["安静慢聊", "低压力"],
    "不太安静": ["自然不尴尬", "高互动"],
    "聊天": ["可坐下聊天", "安静慢聊"],
    "少走路": ["低体力友好", "少折腾"],
    "不累": ["低体力友好", "少折腾"],
    "软和": ["服务温和", "低体力友好"],
    "清淡": ["清淡健康"],
    "减脂": ["清淡健康"],
}

TAG_SCOPES = {
    "清淡健康": {"restaurant"},
    "重口刺激": {"restaurant"},
    "快餐简餐": {"restaurant"},
    "大排档": {"restaurant"},
    "市井大排档": {"restaurant"},
    "烤肉": {"restaurant"},
    "烧烤": {"restaurant"},
    "火锅": {"restaurant"},
    "自然观察": {"activity"},
    "释放精力": {"activity"},
    "危险器械": {"activity"},
    "儿童不适龄": {"activity"},
}

ADDITIONAL_SEMANTIC_TAGS = {
    "自然观察",
}

OPERATIONAL_CONSTRAINT_HINTS = (
    "预算",
    "人均",
    "价格",
    "便宜",
    "成本",
    "花钱",
    "太远",
    "附近",
    "距离",
    "路程",
    "同商圈",
    "步行",
    "地铁",
    "打车",
    "开车",
    "排队",
    "等位",
    "预约",
    "有座",
    "时间",
    "分钟",
    "小时",
    "营业",
    "余票",
    "门票",
    "返程",
    "回家",
)


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def expand_tag_aliases(values: list[str]) -> list[str]:
    expanded: list[str] = []
    for value in values:
        text = str(value)
        expanded.append(text)
        expanded.extend(TAG_ALIASES.get(text, []))
    return unique(expanded)


def tag_applies_to_kind(tag: str, kind: str) -> bool:
    scopes = TAG_SCOPES.get(tag)
    return not scopes or kind in scopes


def known_semantic_tags() -> set[str]:
    tags = set(ADDITIONAL_SEMANTIC_TAGS)
    tags.update(TAG_ALIASES)
    tags.update(TAG_SCOPES)
    for aliases in TAG_ALIASES.values():
        tags.update(aliases)
    for profile in INTENT_TAXONOMY.values():
        tags.update(profile.get("preferred", []))
        tags.update(profile.get("avoid", []))
        tags.update(profile.get("weights", {}))
        tags.update(profile.get("avoidWeights", {}))
    for audience_tags in INTENT_AUDIENCE_TAGS.values():
        tags.update(audience_tags)
    return tags


def semantic_only(values: list[str]) -> list[str]:
    registry = known_semantic_tags()
    return unique(
        [
            tag
            for tag in values
            if tag
            and tag in registry
            and not any(hint in tag for hint in OPERATIONAL_CONSTRAINT_HINTS)
        ]
    )


def taxonomy_key(primary: str, sub_scenario: str | None) -> str | None:
    if primary in UNKNOWN_PRIMARIES:
        return None
    scenario = sub_scenario or PRIMARY_DEFAULT_SUB_SCENARIO.get(primary)
    key = f"{primary}.{scenario}"
    if key in INTENT_TAXONOMY:
        return key
    fallback = PRIMARY_DEFAULT_SUB_SCENARIO.get(primary)
    key = f"{primary}.{fallback}"
    return key if key in INTENT_TAXONOMY else None


def default_sub_scenario(primary: str) -> str:
    return PRIMARY_DEFAULT_SUB_SCENARIO.get(primary, "unknown")


def _has_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def sub_scenario_keywords(primary: str, sub_scenario: str) -> tuple[str, ...]:
    for candidate, keywords in SUB_SCENARIO_KEYWORD_RULES.get(primary, []):
        if candidate == sub_scenario:
            return keywords
    return ()


def allowed_sub_scenarios(primary: str) -> set[str]:
    configured = {candidate for candidate, _ in SUB_SCENARIO_KEYWORD_RULES.get(primary, [])}
    return configured or {default_sub_scenario(primary)}


def evidenced_sub_scenario(primary: str, text: str) -> tuple[str, list[str]]:
    for candidate, keywords in SUB_SCENARIO_KEYWORD_RULES.get(primary, []):
        evidence = [keyword for keyword in keywords if keyword in text]
        if evidence:
            return candidate, evidence
    return default_sub_scenario(primary), []


def infer_primary_and_sub_scenario(
    text: str,
    *,
    scene_type: str | None = None,
    relationship: str | None = None,
    cross_city: bool = False,
    existing_primary: str = "unknown",
) -> tuple[str, str, list[str]]:
    primary = existing_primary if existing_primary in PRIMARY_DEFAULT_SUB_SCENARIO else "unknown"
    for candidate, keywords in PRIMARY_KEYWORD_RULES:
        if _has_keyword(text, keywords):
            primary = candidate
            break
    if primary == "unknown" and cross_city:
        primary = "tourist_sightseeing"
    if primary == "unknown" and relationship in RELATIONSHIP_PRIMARY_RULES:
        primary = RELATIONSHIP_PRIMARY_RULES[str(relationship)]
    if primary == "unknown" and scene_type in SCENE_PRIMARY_RULES:
        primary = SCENE_PRIMARY_RULES[str(scene_type)]
    if primary == "unknown" and scene_type not in {None, "open"}:
        primary = "casual_meetup"

    sub_scenario = default_sub_scenario(primary)
    for candidate, keywords in SUB_SCENARIO_KEYWORD_RULES.get(primary, []):
        if not keywords or _has_keyword(text, keywords):
            sub_scenario = candidate
            break

    evidence = [keyword for keyword in EVIDENCE_KEYWORDS.get(primary, ()) if keyword in text]
    if not evidence and scene_type not in {None, "open"}:
        evidence.append(f"scene={scene_type}")
    return primary, sub_scenario, evidence[:6]


def extract_explicit_tags(raw_input: str) -> tuple[list[str], list[str]]:
    text = str(raw_input or "")
    preferred: list[str] = []
    avoid: list[str] = []

    positive_rules = {
        "大排档": ("大排档", "市井大排档", "接地气"),
        "烤肉": ("烤肉", "烧烤"),
        "火锅": ("火锅",),
        "复古情调": ("复古", "怀旧"),
        "自然不尴尬": ("不尴尬", "不要太正式", "别太正式"),
        "清淡健康": ("清淡", "减脂", "少油", "低脂"),
        "安静慢聊": ("安静聊", "坐下来聊", "聊一会", "深聊"),
        "低体力友好": ("不累", "少走路", "走不动", "带父母", "带老人"),
        "少排队免等": ("别排队", "少排队", "不排队"),
        "游客地标": ("地标", "景点", "经典", "第一次来"),
    }
    avoid_rules = {
        "高噪高动": ("别太吵", "不要太吵", "不想吵", "太吵"),
        "尴尬正式": ("不要太正式", "别太正式", "太正式"),
        "过度消耗体力": ("不想累", "别太累", "不累", "少走路"),
        "排队久": ("别排队", "少排队", "不排队"),
        "快餐简餐": ("不想快餐", "不要快餐"),
        "重口刺激": ("不要重口", "别太辣", "清淡"),
    }

    for tag, keywords in positive_rules.items():
        if any(keyword in text for keyword in keywords):
            preferred.append(tag)
    for tag, keywords in avoid_rules.items():
        if any(keyword in text for keyword in keywords):
            avoid.append(tag)

    if "不太安静" in text or "不要太安静" in text:
        preferred.append("自然不尴尬")
        avoid.append("寂静无声")
    if "特别爱" in text or "喜欢" in text or "想吃" in text:
        if "大排档" in text or "接地气" in text:
            preferred.extend(["大排档", "市井大排档", "烟火气"])
        if "烤肉" in text:
            preferred.extend(["烤肉", "烧烤"])

    return expand_tag_aliases(unique(preferred)), expand_tag_aliases(unique(avoid))


def complete_social_intent(social: dict[str, Any], raw_input: str) -> dict[str, Any]:
    primary = str(social.get("primary") or "unknown")
    if primary not in PRIMARY_DEFAULT_SUB_SCENARIO:
        primary = "unknown"

    requested_sub_scenario = str(
        social.get("subScenario") or social.get("sub_scenario") or default_sub_scenario(primary)
    )
    if requested_sub_scenario not in allowed_sub_scenarios(primary):
        requested_sub_scenario = default_sub_scenario(primary)
    requested_keywords = sub_scenario_keywords(primary, requested_sub_scenario)
    requested_evidence = [keyword for keyword in requested_keywords if keyword in raw_input]
    inferred_sub_scenario, inferred_evidence = evidenced_sub_scenario(primary, raw_input)
    if requested_evidence:
        sub_scenario = requested_sub_scenario
        sub_scenario_evidence = requested_evidence
    elif inferred_evidence:
        sub_scenario = inferred_sub_scenario
        sub_scenario_evidence = inferred_evidence
    else:
        sub_scenario = default_sub_scenario(primary)
        sub_scenario_evidence = []

    raw_explicit_preferred, raw_explicit_avoid = extract_explicit_tags(raw_input)
    explicit_preferred = semantic_only(
        [
            *[str(item) for item in social.get("explicitPreferredVibes", []) if item],
            *raw_explicit_preferred,
        ]
    )
    explicit_avoid = semantic_only(
        [
            *[str(item) for item in social.get("explicitAvoidVibes", []) if item],
            *raw_explicit_avoid,
        ]
    )

    key = taxonomy_key(primary, sub_scenario)
    taxonomy = INTENT_TAXONOMY.get(key or "", {})
    default_preferred = [] if key is None else list(taxonomy.get("preferred", []))
    default_avoid = [] if key is None else list(taxonomy.get("avoid", []))

    existing_preferred = [str(item) for item in social.get("preferredVibes", []) if item]
    existing_avoid = [str(item) for item in social.get("avoidVibes", []) if item]
    trusted_profile_preferred = [tag for tag in existing_preferred if tag in default_preferred]
    trusted_profile_avoid = [tag for tag in existing_avoid if tag in default_avoid]
    preferred = unique([*default_preferred, *trusted_profile_preferred, *explicit_preferred])
    avoid = unique([*default_avoid, *trusted_profile_avoid, *explicit_avoid])

    explicit_preferred_set = set(expand_tag_aliases(explicit_preferred))
    explicit_avoid_set = set(expand_tag_aliases(explicit_avoid))
    preferred = [tag for tag in preferred if tag not in explicit_avoid_set]
    avoid = [tag for tag in avoid if tag not in explicit_preferred_set]

    return {
        **social,
        "primary": primary,
        "subScenario": sub_scenario,
        "subScenarioEvidence": sub_scenario_evidence,
        "subScenarioSource": "explicit_evidence" if sub_scenario_evidence else "safe_default",
        "preferredVibes": preferred,
        "avoidVibes": avoid,
        "explicitPreferredVibes": explicit_preferred,
        "explicitAvoidVibes": explicit_avoid,
        "limits": taxonomy.get("limits", {}) if key else {},
        "confidence": social.get("confidence", 0.72 if primary not in UNKNOWN_PRIMARIES else 0.5),
        "profileSource": "llm+taxonomy" if key else "llm+explicit",
        "taxonomyKey": key,
    }


def semantic_weights(social: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    primary = str(social.get("primary") or "unknown")
    sub_scenario = str(social.get("subScenario") or default_sub_scenario(primary))
    key = taxonomy_key(primary, sub_scenario)
    taxonomy = INTENT_TAXONOMY.get(key or "", {})
    positive: dict[str, float] = {
        str(tag): float(weight) for tag, weight in taxonomy.get("weights", {}).items()
    }
    negative: dict[str, float] = {
        str(tag): float(weight) for tag, weight in taxonomy.get("avoidWeights", {}).items()
    }
    for tag in expand_tag_aliases([str(item) for item in social.get("explicitPreferredVibes", []) if item]):
        positive[tag] = max(positive.get(tag, 0), EXPLICIT_PREFERENCE_BOOST)
        negative.pop(tag, None)
    for tag in expand_tag_aliases([str(item) for item in social.get("explicitAvoidVibes", []) if item]):
        negative[tag] = min(negative.get(tag, 0), EXPLICIT_AVOID_PENALTY)
        positive.pop(tag, None)
    return positive, negative
