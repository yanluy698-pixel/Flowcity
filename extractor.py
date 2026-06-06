"""
FlowCity Stage 2 - Constraint Extractor

Goal:
Natural language user input -> LLM -> structured JSON -> basic schema validation.

This script does not contain API keys. Put local config in stage2/.env.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import intent_taxonomy
import demand_profile
import planning_policy


ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema.json"
PROMPT_PATH = ROOT / "prompt.md"
EXAMPLES_PATH = ROOT / "examples.json"
ENV_PATH = ROOT / ".env"

# 默认使用 DeepSeek 的 OpenAI 兼容接口；具体 Key 不写在代码里。
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_LLM_RETRIES = 2
PROMPT_EXAMPLE_IDS = (
    "family_half_day",
    "pursuing_date_low_budget",
    "xianyang_to_xian_city_trip",
)


def load_dotenv(path: Path = ENV_PATH) -> None:
    """读取同目录 .env 中的 KEY=VALUE 配置，避免把 API Key 写进代码。"""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        # 如果系统环境变量里已经有同名配置，就优先使用系统环境变量。
        os.environ.setdefault(key, value)


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_chat_completions_url(base_url: str) -> str:
    """
    DeepSeek's OpenAI-compatible base_url is https://api.deepseek.com.
    urllib needs the concrete chat completions endpoint.
    """
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def _compact_prompt_schema(value: Any) -> Any:
    """Keep validation structure while dropping verbose documentation fields."""
    if isinstance(value, list):
        return [_compact_prompt_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    ignored = {"$schema", "$id", "title", "description"}
    return {
        key: _compact_prompt_schema(item)
        for key, item in value.items()
        if key not in ignored
    }


def _compact_prompt_examples(examples: dict[str, Any]) -> dict[str, Any]:
    selected = [
        item
        for item in examples.get("examples", [])
        if item.get("id") in PROMPT_EXAMPLE_IDS
    ]
    return {"examples": selected}


def build_prompt(user_input: str) -> str:
    # 默认只发送紧凑 schema 和代表性 few-shot，完整资料仍用于本地校验和可选调试。
    prompt_template = load_text(PROMPT_PATH)
    schema = load_json(SCHEMA_PATH)
    examples = load_json(EXAMPLES_PATH)

    use_full_prompt = os.getenv("FLOWCITY_LLM_FULL_PROMPT", "false").lower() == "true"
    prompt_schema = schema if use_full_prompt else _compact_prompt_schema(schema)
    prompt_examples = examples if use_full_prompt else _compact_prompt_examples(examples)
    compact_schema = json.dumps(prompt_schema, ensure_ascii=False, separators=(",", ":"))
    compact_examples = json.dumps(prompt_examples, ensure_ascii=False, separators=(",", ":"))

    prompt = prompt_template.replace("{{USER_INPUT}}", user_input)
    return (
        prompt
        + "\n\n## schema.json\n\n"
        + compact_schema
        + "\n\n## examples.json\n\n"
        + compact_examples
    )


def get_config() -> dict[str, Any]:
    load_dotenv()

    # 支持 DEEPSEEK_API_KEY，也兼容更通用的 FLOWCITY_LLM_API_KEY。
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("FLOWCITY_LLM_API_KEY")
    model = os.getenv("FLOWCITY_LLM_MODEL", DEFAULT_MODEL)
    base_url = os.getenv("FLOWCITY_LLM_BASE_URL", DEFAULT_BASE_URL)
    max_tokens = int(os.getenv("FLOWCITY_LLM_MAX_TOKENS", "4096"))
    json_output = os.getenv("FLOWCITY_LLM_JSON_OUTPUT", "true").lower() == "true"
    retries = int(os.getenv("FLOWCITY_LLM_RETRIES", str(DEFAULT_LLM_RETRIES)))

    if not api_key:
        raise RuntimeError(
            "Missing API key. Fill DEEPSEEK_API_KEY in stage2/.env before running without --dry-run."
        )

    return {
        "api_key": api_key,
        "model": model,
        "url": normalize_chat_completions_url(base_url),
        "max_tokens": max_tokens,
        "json_output": json_output,
        "retries": retries,
    }


def call_llm(
    prompt: str,
    *,
    max_tokens: int | None = None,
    timeout_seconds: float = 60,
    retries: int | None = None,
) -> str:
    config = get_config()
    request_retries = config["retries"] if retries is None else max(0, retries)
    start_time = time.perf_counter()
    print(
        "[FlowCity][LLM] request start "
        f"model={config['model']} url={config['url']} prompt_chars={len(prompt)}",
        flush=True,
    )

    # 这里使用 OpenAI Chat Completions 兼容格式访问 DeepSeek。
    payload: dict[str, Any] = {
        "model": config["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict JSON extraction engine. "
                    "Return only one valid JSON object. Do not return Markdown."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens or config["max_tokens"],
    }

    # DeepSeek JSON Output：要求模型返回合法 JSON 字符串，适合阶段二结构化抽取。
    if config["json_output"]:
        payload["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        config["url"],
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    last_error: Exception | None = None
    for attempt in range(request_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            # 4xx usually means bad request/auth/config; retrying just burns time.
            if 400 <= exc.code < 500:
                raise RuntimeError(f"LLM request failed: HTTP {exc.code}\n{body}") from exc
            last_error = RuntimeError(f"LLM request failed: HTTP {exc.code}\n{body}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc

        if attempt < request_retries:
            time.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError(f"LLM request failed after retries: {last_error}") from last_error

    content = data["choices"][0]["message"].get("content")
    if not content:
        raise RuntimeError(
            "LLM returned empty content. DeepSeek JSON Output may occasionally do this; retry or adjust the prompt."
        )
    elapsed = time.perf_counter() - start_time
    print(
        "[FlowCity][LLM] request done "
        f"elapsed={elapsed:.2f}s response_chars={len(content)}",
        flush=True,
    )
    return content


def parse_json_object(text: str) -> dict[str, Any]:
    # 容错处理：如果模型意外包了一层 ```json 代码块，也尽量剥掉后解析。
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    return json.loads(stripped)


LOW_COST_PHRASES = (
    "不想花钱",
    "少花钱",
    "预算越低越好",
    "低成本",
    "省钱",
    "便宜点",
    "预算少一点",
)

FREE_PREFERENCE_PHRASES = (
    "最好免费",
    "优先免费",
    "尽量免费",
)

EXPLICIT_ZERO_BUDGET_PATTERNS = (
    r"预算\s*0\s*元?",
    r"零预算",
    r"一分钱(?:都)?不(?:能|想)?花",
    r"完全免费",
    r"必须免费",
    r"只能免费",
    r"只要免费",
)
CHILD_ACCOMPANY_PATTERNS = (
    r"带[^，。,.]{0,12}(孩子|小孩|娃|宝宝)",
    r"陪[^，。,.]{0,12}(孩子|小孩|娃|宝宝)",
)
SPOUSE_PHRASES = ("老婆", "老公", "媳妇", "妻子", "丈夫", "爱人")


def _has_low_cost_intent(text: str) -> bool:
    return any(phrase in text for phrase in LOW_COST_PHRASES)


def _has_free_preference(text: str) -> bool:
    return any(phrase in text for phrase in FREE_PREFERENCE_PHRASES)


def _has_explicit_zero_budget(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in EXPLICIT_ZERO_BUDGET_PATTERNS)


def _has_child_accompany_intent(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in CHILD_ACCOMPANY_PATTERNS)


def _append_assumption(result: dict[str, Any], note: str) -> None:
    assumptions = result.setdefault("assumptions", [])
    if isinstance(assumptions, list) and note not in assumptions:
        assumptions.append(note)


def _normalize_child_accompanying_adult(result: dict[str, Any], raw_input: str) -> None:
    """Stabilize a narrow Chinese-language inference: children being "brought" imply an adult."""
    if not _has_child_accompany_intent(raw_input):
        return
    people = result.get("people")
    if not isinstance(people, dict):
        return
    children = people.get("children")
    if not isinstance(children, list) or not children:
        return

    seniors = people.get("seniors") if isinstance(people.get("seniors"), list) else []
    inferred_adults = 2 if any(phrase in raw_input for phrase in SPOUSE_PHRASES) else 1
    adults = people.get("adults")
    if not isinstance(adults, int) or adults <= 0:
        people["adults"] = inferred_adults
        adults = inferred_adults

    total = people.get("total")
    if not isinstance(total, int) or total <= 0:
        people["total"] = adults + len(children) + len(seniors)

    child_count_text = f"{len(children)} 小" if len(children) > 1 else "1 小"
    _append_assumption(result, f"用户表达带孩子出行，未额外说明同行人数时按 {people['adults']} 大 {child_count_text} 理解。")


def _is_sparse_or_drifted(result: dict[str, Any], raw_input: str) -> bool:
    text = json.dumps(result, ensure_ascii=False)
    if any(keyword in text for keyword in ("乱码", "无法识别", "无法理解", "不完整输入")):
        return True
    signal_count = 0
    if re.search(r"\d+\s*个|[一二三四五六七八九十]\s*个|带.*孩子|老婆|女生|男生|大学生|朋友", raw_input):
        people = result.get("people") if isinstance(result.get("people"), dict) else {}
        if people.get("total") or people.get("children") or people.get("relationship"):
            signal_count += 1
    if re.search(r"周[六日天末]|今晚|下午|中午|晚上|\d+\s*点", raw_input):
        window = result.get("timeWindow") if isinstance(result.get("timeWindow"), dict) else {}
        if window.get("dateText") or window.get("startTime") or window.get("endTime"):
            signal_count += 1
    if re.search(r"预算|人均|以内|不超过|\d+\s*元", raw_input):
        budget = result.get("budget") if isinstance(result.get("budget"), dict) else {}
        if budget.get("maxTotal") is not None or budget.get("perPerson") is not None:
            signal_count += 1
    if re.search(r"曲江|钟楼|咸阳|秦都|渭水|长安大学|交大|西北大学|陕师大", raw_input):
        location = result.get("location") if isinstance(result.get("location"), dict) else {}
        if location.get("startPoint") or location.get("preferredArea") or location.get("originPoints"):
            signal_count += 1
    return signal_count <= 1 and len(raw_input) >= 20


def _time_from_raw(raw_input: str) -> dict[str, Any]:
    date_text = None
    if "今晚" in raw_input:
        date_text = "今晚"
    elif "周六" in raw_input:
        date_text = "周六"
    elif "周天" in raw_input or "周日" in raw_input:
        date_text = "周天"
    elif "周末" in raw_input:
        date_text = "周末"

    start_time = None
    end_time = None
    range_match = re.search(r"(\d{1,2})\s*点\s*(?:到|至|-|~)\s*(\d{1,2})\s*点", raw_input)
    if range_match:
        start_hour = int(range_match.group(1))
        end_hour = int(range_match.group(2))
        if "下午" in raw_input and start_hour < 12:
            start_hour += 12
        if end_hour < start_hour:
            end_hour += 12
        start_time = f"{start_hour:02d}:00"
        end_time = f"{end_hour:02d}:00"
    elif "6点半" in raw_input or "六点半" in raw_input:
        start_time = "18:30"
    else:
        for match in re.finditer(r"(\d{1,2})\s*点", raw_input):
            if raw_input[match.end(): match.end() + 1] == "前":
                continue
            hour = int(match.group(1))
            if ("下午" in raw_input or ("到7点" in raw_input and hour < 8)) and hour < 12:
                hour += 12
            start_time = f"{hour:02d}:00"
            break
    if end_time:
        pass
    elif "10点前" in raw_input or "十点前" in raw_input:
        end_time = "22:00"
    elif "7点前" in raw_input or "七点前" in raw_input:
        end_time = "19:00"
    elif "6点前" in raw_input or "六点前" in raw_input:
        end_time = "18:00"
    if start_time is None and end_time and "下午" in raw_input:
        start_time = "13:00"
    return {
        "dateText": date_text,
        "startTime": start_time,
        "endTime": end_time,
        "durationHours": None,
        "isFlexible": start_time is None or end_time is None,
    }


def _minutes_from_time_text(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{1,2}):(\d{2})$", value.strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _has_explicit_time_range(raw_input: str) -> bool:
    return bool(
        re.search(r"\d{1,2}\s*点\s*(?:到|至|-|~)\s*\d{1,2}\s*点", raw_input)
        or any(keyword in raw_input for keyword in ("玩到", "逛到", "待到", "坐到", "结束前", "之前", "前回"))
    )


def _people_from_raw(raw_input: str) -> dict[str, Any]:
    children = []
    child_match = re.search(r"(\d{1,2})\s*岁", raw_input)
    if child_match:
        children = [{"age": int(child_match.group(1))}]
    total = None
    if match := re.search(r"(\d+)\s*个(?:人|大学生|男生|朋友)?", raw_input):
        total = int(match.group(1))
    elif "三" in raw_input and "男生" in raw_input:
        total = 3
    elif "四" in raw_input and ("大学生" in raw_input or "同学" in raw_input or "朋友" in raw_input):
        total = 4
    elif children and "老婆" in raw_input:
        total = 3
    elif "女生" in raw_input:
        total = 2

    relationship = None
    if children:
        relationship = "family"
    elif "喜欢" in raw_input or "好感" in raw_input:
        relationship = "pursuing"
    elif total and total >= 3:
        relationship = "friends"
    elif "大学生" in raw_input or "同学" in raw_input or "朋友" in raw_input or "男生" in raw_input or "citywalk" in raw_input:
        relationship = "friends"

    adults = None
    if total:
        adults = max(0, total - len(children))
    return {
        "total": total,
        "adults": adults,
        "children": children,
        "seniors": [],
        "relationship": relationship,
        "specialNeeds": [],
    }


def _budget_from_raw(raw_input: str) -> dict[str, Any]:
    per_person = None
    max_total = None
    if match := re.search(r"人均\s*(\d+)", raw_input):
        per_person = int(match.group(1))
    elif match := re.search(r"(\d+)\s*以内", raw_input):
        value = int(match.group(1))
        if "人均" in raw_input:
            per_person = value
        else:
            max_total = value
    elif match := re.search(r"预算(?:别超过|不超过|少一点|)?\s*(\d+)", raw_input):
        max_total = int(match.group(1))
    elif match := re.search(r"总预算\s*(\d+)", raw_input):
        max_total = int(match.group(1))
    return {
        "maxTotal": max_total,
        "perPerson": per_person,
        "currency": "CNY",
        "flexibility": "strict" if max_total or per_person else "unknown",
    }


def _location_from_raw(raw_input: str) -> dict[str, Any]:
    start_point = None
    preferred_area = None
    origin_points: list[dict[str, str]] = []

    if "熙地港" in raw_input:
        preferred_area = "熙地港"
    elif "小寨" in raw_input or "赛格" in raw_input:
        preferred_area = "小寨"
    elif "高新" in raw_input or "大都荟" in raw_input:
        preferred_area = "高新"
    elif "钟楼" in raw_input:
        preferred_area = "钟楼"
    elif any(keyword in raw_input for keyword in ("大雁塔", "大唐不夜城", "曲江大悦城")):
        preferred_area = "曲江"
    elif "咸阳秦都" in raw_input or "秦都站" in raw_input or "渭水校区" in raw_input:
        preferred_area = "西安市区"

    if "曲江池" in raw_input:
        start_point = "曲江池附近"
        if preferred_area is None:
            preferred_area = "曲江"
    elif "钟楼地铁站" in raw_input:
        start_point = "钟楼地铁站集合" if "地铁站" in raw_input else None
        if preferred_area is None:
            preferred_area = "钟楼"
    elif "小寨" in raw_input or "赛格" in raw_input:
        start_point = "小寨附近" if "附近" in raw_input else None
        if preferred_area is None:
            preferred_area = "小寨"
    elif "高新" in raw_input or "大都荟" in raw_input:
        start_point = "高新附近" if "附近" in raw_input else None
        if preferred_area is None:
            preferred_area = "高新"
    elif "咸阳秦都" in raw_input or "秦都站" in raw_input:
        start_point = "咸阳秦都站附近"
        if preferred_area is None:
            preferred_area = "西安市区"
    elif "渭水校区" in raw_input:
        start_point = "长安大学渭水校区"
        if preferred_area is None:
            preferred_area = "西安市区"
    if "长安大学" in raw_input and "西安交大" in raw_input and "西北大学" in raw_input and "陕师大" in raw_input:
        origin_points = [
            {"label": "同伴1", "point": "长安大学"},
            {"label": "同伴2", "point": "西安交大"},
            {"label": "同伴3", "point": "西北大学"},
            {"label": "同伴4", "point": "陕师大"},
        ]
    cross_city = "咸阳" in raw_input
    return {
        "startPoint": start_point,
        "originPoints": origin_points,
        "preferredArea": preferred_area,
        "crossCityIntent": {"enabled": cross_city, "fromCity": "咸阳" if cross_city else None, "toCity": "西安" if cross_city else None},
        "maxTravelMinutes": None,
        "transportPreference": "public_transport" if "地铁" in raw_input or origin_points else "walk" if "citywalk" in raw_input else None,
        "distancePreference": "别太远" if "别太远" in raw_input else "别走太多路" if "别走太多路" in raw_input else None,
    }


def _preferences_from_raw(raw_input: str) -> dict[str, list[str]]:
    activity_types: list[str] = []
    food_tags: list[str] = []
    experience_tags: list[str] = []
    avoid_tags: list[str] = []
    keyword_map = [
        ("亲子", activity_types, "亲子"),
        ("孩子", activity_types, "儿童友好"),
        ("想玩", activity_types, "城市景点"),
        ("我要玩", activity_types, "城市景点"),
        ("景点", activity_types, "城市景点"),
        ("看电影", activity_types, "电影"),
        ("电影票", activity_types, "电影"),
        ("电影", activity_types, "电影"),
        ("影院", activity_types, "电影"),
        ("逛一下", activity_types, "citywalk"),
        ("逛", activity_types, "citywalk"),
        ("citywalk", activity_types, "citywalk"),
        ("小吃", food_tags, "小吃"),
        ("火锅", food_tags, "火锅"),
        ("烤肉", food_tags, "烤肉"),
        ("烧烤", food_tags, "烧烤"),
        ("大排档", food_tags, "大排档"),
        ("晚饭", food_tags, "晚餐"),
        ("晚餐", food_tags, "晚餐"),
        ("吃饭", food_tags, "正餐"),
        ("吃完晚饭", experience_tags, "饭后附近散步"),
        ("吃完饭", experience_tags, "饭后附近散步"),
        ("饭后", experience_tags, "饭后附近散步"),
        ("再转", experience_tags, "饭后附近散步"),
        ("减脂", food_tags, "低脂"),
        ("不油腻", food_tags, "低脂"),
        ("清淡", food_tags, "清淡"),
        ("坐下来聊", experience_tags, "能坐下聊天"),
        ("少走路", experience_tags, "少走路"),
        ("公平", experience_tags, "预算公平"),
        ("公共交通", experience_tags, "公共交通可达"),
        ("关系", experience_tags, "低压力"),
        ("喜欢", experience_tags, "轻约会"),
        ("别太正式", avoid_tags, "太正式"),
        ("排太久", avoid_tags, "排队久"),
        ("别把时间都花在路上", avoid_tags, "通勤太久"),
    ]
    for keyword, target, tag in keyword_map:
        if keyword in raw_input and tag not in target:
            target.append(tag)
    return {
        "activityTypes": activity_types,
        "foodTags": food_tags,
        "experienceTags": experience_tags,
        "avoidTags": avoid_tags,
    }


def _avoid_terms_from_raw(raw_input: str) -> list[str]:
    terms: list[str] = []
    patterns = [
        r"不想去([^，。,！!？?\s]+)",
        r"不要([^，。,！!？?\s]+)",
        r"别去([^，。,！!？?\s]+)",
        r"避开([^，。,！!？?\s]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, raw_input):
            term = match.group(1).strip("，。,！!？? 的了吧")
            if term and term not in terms:
                terms.append(term)
    return terms


def _hard_constraints_from_raw(raw_input: str) -> list[str]:
    constraints: list[str] = []
    if any(keyword in raw_input for keyword in ("晚饭", "晚餐", "吃饭")):
        constraints.append("必须安排正餐/晚饭，预算优先保证餐饮")
    if any(keyword in raw_input for keyword in ("吃完晚饭", "吃完饭", "饭后", "再转", "转一会")):
        constraints.append("晚饭后需要安排附近轻松散步或短暂停留")
    for term in _avoid_terms_from_raw(raw_input):
        constraints.append(f"避开用户明确不想去的地点或商圈：{term}")
    return constraints


def _repair_sparse_result(result: dict[str, Any], raw_input: str) -> dict[str, Any]:
    result["rawInput"] = raw_input
    result["scene"] = {
        "primaryType": "family" if "孩子" in raw_input else "couple" if "喜欢" in raw_input or "好感" in raw_input else "friends" if any(word in raw_input for word in ("朋友", "男生", "大学生", "同学", "citywalk")) or re.search(r"\d+\s*个人", raw_input) else "open",
        "confidence": 0.72,
        "tags": [],
    }
    result["timeWindow"] = _time_from_raw(raw_input)
    result["people"] = _people_from_raw(raw_input)
    result["budget"] = _budget_from_raw(raw_input)
    result["location"] = _location_from_raw(raw_input)
    result["preferences"] = _preferences_from_raw(raw_input)
    result["constraints"] = {
        "hard": _hard_constraints_from_raw(raw_input),
        "soft": [],
        "dynamic": ["活动余票状态", "餐厅座位状态", "实时排队时间", "确认前状态变化"],
    }
    result["potentialConflicts"] = []
    result["expectedOutput"] = {
        "planFormat": "timeline_plan",
        "mustInclude": ["时间轴", "活动安排", "餐饮安排", "路线/通勤", "预算估算", "风险提示"],
    }
    result["assumptions"] = ["LLM 抽取结果过空，已用本地规则从用户原文补全关键字段。"]
    result["clarificationQuestions"] = []
    return result


def _merge_list_tags(existing: Any, additions: list[str]) -> list[str]:
    merged = list(existing) if isinstance(existing, list) else []
    for item in additions:
        if item not in merged:
            merged.append(item)
    return merged


def _compact_evidence(raw_input: str, keywords: list[str]) -> list[str]:
    evidence: list[str] = []
    for keyword in keywords:
        if keyword in raw_input and keyword not in evidence:
            evidence.append(keyword)
    return evidence


def _infer_social_intent(result: dict[str, Any], raw_input: str) -> dict[str, Any]:
    social = result.get("socialIntent") if isinstance(result.get("socialIntent"), dict) else {}
    text_parts = [
        raw_input,
        str(result.get("scene", {}).get("primaryType") or ""),
        str(result.get("people", {}).get("relationship") or ""),
        " ".join(str(item) for item in result.get("scene", {}).get("tags", [])),
        " ".join(str(item) for item in result.get("people", {}).get("specialNeeds", [])),
        " ".join(str(item) for item in result.get("preferences", {}).get("activityTypes", [])),
        " ".join(str(item) for item in result.get("preferences", {}).get("foodTags", [])),
        " ".join(str(item) for item in result.get("preferences", {}).get("experienceTags", [])),
        " ".join(str(item) for item in result.get("constraints", {}).get("hard", [])),
        " ".join(str(item) for item in result.get("constraints", {}).get("soft", [])),
    ]
    text = " ".join(text_parts)
    preferred: list[str] = []
    avoid: list[str] = []
    evidence: list[str] = []
    sub_scenario = str(social.get("subScenario") or social.get("sub_scenario") or "")

    def add(values: list[str], *items: str) -> None:
        for item in items:
            if item and item not in values:
                values.append(item)

    valid_primary = {
        "light_date",
        "deep_talk",
        "group_bonding",
        "tourist_sightseeing",
        "family_care",
        "casual_meetup",
        "unknown",
    }
    primary = str(social.get("primary") or "unknown")
    if primary not in valid_primary:
        primary = "unknown"

    cross_city = result.get("location", {}).get("crossCityIntent", {})
    is_xianyang_trip = (
        bool(cross_city.get("enabled"))
        and "咸阳" in str(cross_city.get("fromCity") or "")
        and "西安" in str(cross_city.get("toCity") or "")
    )
    inferred_primary, inferred_sub_scenario, inferred_evidence = intent_taxonomy.infer_primary_and_sub_scenario(
        text,
        scene_type=str(result.get("scene", {}).get("primaryType") or "open"),
        relationship=str(result.get("people", {}).get("relationship") or ""),
        cross_city=is_xianyang_trip,
        existing_primary=primary,
    )
    primary = inferred_primary
    sub_scenario = sub_scenario or inferred_sub_scenario
    evidence.extend(inferred_evidence)

    add(preferred, *[str(item) for item in social.get("preferredVibes", []) if item])
    add(avoid, *[str(item) for item in social.get("avoidVibes", []) if item])
    add(evidence, *[str(item) for item in social.get("evidence", []) if item])
    if not evidence and raw_input:
        evidence.append(raw_input[:40])

    inferred = {
        "primary": primary,
        "subScenario": sub_scenario or intent_taxonomy.default_sub_scenario(primary),
        "preferredVibes": preferred,
        "avoidVibes": avoid,
        "evidence": evidence[:6],
    }
    for key in ("confidence", "explicitPreferredVibes", "explicitAvoidVibes", "limits", "profileSource"):
        if key in social:
            inferred[key] = social[key]
    return intent_taxonomy.complete_social_intent(inferred, raw_input)


def _augment_missing_from_raw(result: dict[str, Any], raw_input: str) -> None:
    raw_time = _time_from_raw(raw_input)
    time_window = result.setdefault("timeWindow", {})
    if isinstance(time_window, dict):
        for key in ("dateText", "startTime", "endTime"):
            if not time_window.get(key) and raw_time.get(key):
                time_window[key] = raw_time[key]
        if (
            time_window.get("startTime")
            and time_window.get("startTime") == time_window.get("endTime")
            and "前" in raw_input
            and raw_time.get("startTime")
        ):
            time_window["startTime"] = raw_time["startTime"]
        if time_window.get("startTime") and time_window.get("endTime"):
            time_window["isFlexible"] = bool(time_window.get("isFlexible") and False)
        start_minutes = _minutes_from_time_text(time_window.get("startTime"))
        end_minutes = _minutes_from_time_text(time_window.get("endTime"))
        if (
            start_minutes is not None
            and end_minutes is not None
            and end_minutes <= start_minutes
            and not _has_explicit_time_range(raw_input)
        ):
            time_window["endTime"] = None
            time_window["durationHours"] = None
            time_window["isFlexible"] = True

    raw_people = _people_from_raw(raw_input)
    people = result.setdefault("people", {})
    if isinstance(people, dict):
        for key in ("total", "adults", "relationship"):
            if not people.get(key) and raw_people.get(key):
                people[key] = raw_people[key]
        if not people.get("children") and raw_people.get("children"):
            people["children"] = raw_people["children"]

    raw_budget = _budget_from_raw(raw_input)
    budget = result.setdefault("budget", {})
    if isinstance(budget, dict):
        for key in ("maxTotal", "perPerson"):
            if budget.get(key) is None and raw_budget.get(key) is not None:
                budget[key] = raw_budget[key]
        if budget.get("maxTotal") is not None or budget.get("perPerson") is not None:
            budget["flexibility"] = "strict"

    raw_location = _location_from_raw(raw_input)
    location = result.setdefault("location", {})
    if isinstance(location, dict):
        for key in ("startPoint", "preferredArea", "transportPreference", "distancePreference"):
            if not location.get(key) and raw_location.get(key):
                location[key] = raw_location[key]
        if not location.get("originPoints") and raw_location.get("originPoints"):
            location["originPoints"] = raw_location["originPoints"]
        cross_city = location.get("crossCityIntent")
        if isinstance(cross_city, dict) and raw_location.get("crossCityIntent", {}).get("enabled"):
            location["crossCityIntent"] = raw_location["crossCityIntent"]

    raw_preferences = _preferences_from_raw(raw_input)
    preferences = result.setdefault("preferences", {})
    if isinstance(preferences, dict):
        for key, additions in raw_preferences.items():
            preferences[key] = _merge_list_tags(preferences.get(key), additions)
        avoid_tags = preferences.setdefault("avoidTags", [])
        if isinstance(avoid_tags, list):
            for term in _avoid_terms_from_raw(raw_input):
                tag = f"避开:{term}"
                if tag not in avoid_tags:
                    avoid_tags.append(tag)

    constraints = result.setdefault("constraints", {})
    if isinstance(constraints, dict):
        hard = constraints.setdefault("hard", [])
        if isinstance(hard, list):
            for item in _hard_constraints_from_raw(raw_input):
                if item not in hard:
                    hard.append(item)

    scene = result.setdefault("scene", {})
    if isinstance(scene, dict):
        raw_scene = _repair_sparse_result({}, raw_input)["scene"]["primaryType"]
        if scene.get("primaryType") in {None, "open"} and raw_scene != "open":
            scene["primaryType"] = raw_scene
            scene["confidence"] = max(float(scene.get("confidence") or 0), 0.72)


def _normalize_scene_primary_type(result: dict[str, Any], raw_input: str) -> None:
    """Keep scene.primaryType as a broad people/context enum, not a social-intent key."""
    scene = result.setdefault("scene", {})
    if not isinstance(scene, dict):
        result["scene"] = {"primaryType": "open", "confidence": 0.5, "tags": []}
        return

    allowed = {"family", "couple", "friends", "solo", "elderly", "open"}
    primary = str(scene.get("primaryType") or "").strip()
    if primary in allowed:
        return

    raw_scene = _repair_sparse_result({}, raw_input)["scene"]["primaryType"]
    aliases = {
        "family_care": "family",
        "parent_child": "family",
        "kid_care": "family",
        "light_date": "couple",
        "deep_talk": "couple" if any(word in raw_input for word in ("对象", "约会", "女生", "男生", "喜欢", "好感")) else "open",
        "group_bonding": "friends",
        "friend_group": "friends",
        "friends_trip": "friends",
        "senior_care": "elderly",
        "tourist_sightseeing": raw_scene if raw_scene != "open" else "open",
        "local_food_hunt": raw_scene if raw_scene != "open" else "open",
        "casual_meetup": raw_scene if raw_scene != "open" else "open",
        "unknown": "open",
    }
    scene["primaryType"] = aliases.get(primary, raw_scene if raw_scene in allowed else "open")
    try:
        confidence = float(scene.get("confidence") or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5
    scene["confidence"] = min(confidence, 0.78)
    tags = scene.setdefault("tags", [])
    if isinstance(tags, list) and primary and primary not in tags:
        tags.append(primary)


def _normalize_expected_output(result: dict[str, Any]) -> None:
    expected = result.setdefault("expectedOutput", {})
    if not isinstance(expected, dict):
        result["expectedOutput"] = {
            "planFormat": "timeline_plan",
            "mustInclude": ["时间轴", "活动安排", "餐饮安排", "路线/通勤", "预算估算", "风险提示"],
        }
        return
    if expected.get("planFormat") not in {"timeline_plan", "options_comparison", "open"}:
        expected["planFormat"] = "timeline_plan"
    allowed_must_include = {
        "时间轴",
        "活动安排",
        "餐饮安排",
        "路线/通勤",
        "预算估算",
        "推荐理由",
        "风险提示",
        "预约/排队状态",
        "备选方案",
        "分享文案",
    }
    values = expected.get("mustInclude")
    if not isinstance(values, list):
        values = []
    kept = [str(item) for item in values if str(item) in allowed_must_include]
    for item in ("时间轴", "预算估算", "风险提示"):
        if item not in kept:
            kept.append(item)
    expected["mustInclude"] = kept


def _normalize_required_schema_defaults(result: dict[str, Any]) -> None:
    people = result.setdefault("people", {})
    if not isinstance(people, dict):
        people = {}
        result["people"] = people
    people.setdefault("total", None)
    people.setdefault("adults", None)
    people.setdefault("children", [])
    people.setdefault("seniors", [])
    people.setdefault("relationship", None)
    people.setdefault("specialNeeds", [])

    budget = result.setdefault("budget", {})
    if not isinstance(budget, dict):
        budget = {}
        result["budget"] = budget
    budget.setdefault("maxTotal", None)
    budget.setdefault("perPerson", None)
    budget["currency"] = "CNY"
    if budget.get("flexibility") not in {"strict", "flexible", "unknown"}:
        budget["flexibility"] = "unknown"

    location = result.setdefault("location", {})
    if not isinstance(location, dict):
        location = {}
        result["location"] = location
    location.setdefault("startPoint", None)
    location.setdefault("originPoints", [])
    location.setdefault("preferredArea", None)
    cross_city = location.get("crossCityIntent")
    if not isinstance(cross_city, dict):
        cross_city = {}
    cross_city.setdefault("enabled", False)
    cross_city.setdefault("fromCity", None)
    cross_city.setdefault("toCity", None)
    location["crossCityIntent"] = cross_city
    location.setdefault("maxTravelMinutes", None)
    if location.get("transportPreference") not in {"walk", "taxi", "public_transport", "drive", "no_preference", None}:
        location["transportPreference"] = None
    location.setdefault("transportPreference", None)
    location.setdefault("distancePreference", None)

    preferences = result.setdefault("preferences", {})
    if not isinstance(preferences, dict):
        preferences = {}
        result["preferences"] = preferences
    preferences.setdefault("activityTypes", [])
    preferences.setdefault("foodTags", [])
    preferences.setdefault("experienceTags", [])
    preferences.setdefault("avoidTags", [])

    constraints = result.setdefault("constraints", {})
    if not isinstance(constraints, dict):
        constraints = {}
        result["constraints"] = constraints
    constraints.setdefault("hard", [])
    constraints.setdefault("soft", [])
    constraints.setdefault("dynamic", [])


def _shift_evening_clock(value: Any) -> Any:
    if not isinstance(value, str) or ":" not in value:
        return value
    hour_text, minute_text = value.split(":", 1)
    if not hour_text.isdigit():
        return value
    hour = int(hour_text)
    if 1 <= hour <= 11:
        return f"{hour + 12:02d}:{minute_text}"
    return value


def _normalize_evening_clock(result: dict[str, Any], raw_input: str) -> None:
    time_window = result.get("timeWindow")
    if not isinstance(time_window, dict):
        return
    date_text = str(time_window.get("dateText") or "")
    evening_text = raw_input + " " + date_text
    if not any(keyword in evening_text for keyword in ("今晚", "晚上", "晚饭", "晚餐", "夜里")):
        return
    if any(keyword in raw_input for keyword in ("上午", "早上", "清早", "凌晨")):
        return
    time_window["startTime"] = _shift_evening_clock(time_window.get("startTime"))
    time_window["endTime"] = _shift_evening_clock(time_window.get("endTime"))


def _normalize_generic_home_origin(result: dict[str, Any], raw_input: str) -> None:
    if not any(keyword in raw_input for keyword in ("从家", "家出发", "家里出发", "回家", "到家")):
        return
    location = result.setdefault("location", {})
    if not isinstance(location, dict):
        return
    if not location.get("startPoint"):
        location["startPoint"] = "家附近"


def normalize_structured_demand(result: dict[str, Any], fallback_raw_input: str | None = None) -> dict[str, Any]:
    """
    Normalize known model drift after extraction.

    Product decision: "不想花钱 / 少花钱 / 预算越低越好" is a low-cost
    preference, and "最好免费 / 优先免费" is a free preference. Neither is
    a strict CNY 0 budget unless the user explicitly says zero budget or
    must be free.
    """
    raw_input = str(fallback_raw_input or result.get("rawInput") or "")
    if fallback_raw_input and _is_sparse_or_drifted(result, fallback_raw_input):
        result = _repair_sparse_result(result, fallback_raw_input)
    elif fallback_raw_input:
        result["rawInput"] = fallback_raw_input
        _augment_missing_from_raw(result, fallback_raw_input)
    _normalize_scene_primary_type(result, raw_input)
    _normalize_expected_output(result)
    _normalize_required_schema_defaults(result)
    _normalize_evening_clock(result, raw_input)
    _normalize_generic_home_origin(result, raw_input)
    _normalize_child_accompanying_adult(result, raw_input)
    result["socialIntent"] = _infer_social_intent(result, raw_input)

    has_low_cost_intent = _has_low_cost_intent(raw_input)
    has_free_preference = _has_free_preference(raw_input)
    if not (has_low_cost_intent or has_free_preference) or _has_explicit_zero_budget(raw_input):
        demand_profile.ensure_demand_profile(result)
        result["planningPolicy"] = planning_policy.schema_planning_policy(result, raw_input)
        return result

    budget = result.get("budget")
    if not isinstance(budget, dict):
        demand_profile.ensure_demand_profile(result)
        result["planningPolicy"] = planning_policy.schema_planning_policy(result, raw_input)
        return result

    if budget.get("maxTotal") == 0:
        budget["maxTotal"] = None
    if budget.get("perPerson") == 0:
        budget["perPerson"] = None
    if budget.get("maxTotal") is None and budget.get("perPerson") is None:
        budget["flexibility"] = "flexible"

    preferences = result.get("preferences")
    if isinstance(preferences, dict):
        experience_tags = preferences.setdefault("experienceTags", [])
        if isinstance(experience_tags, list) and "低成本" not in experience_tags:
            experience_tags.append("低成本")

    constraints = result.get("constraints")
    if isinstance(constraints, dict):
        hard = constraints.setdefault("hard", [])
        soft = constraints.setdefault("soft", [])
        if isinstance(hard, list):
            moved: list[str] = []
            kept: list[Any] = []
            for item in hard:
                item_text = str(item)
                if any(keyword in item_text for keyword in ("不想花钱", "少花钱", "尽量不花钱", "优先免费", "最好免费", "尽量免费", "低成本")):
                    moved.append(item_text)
                else:
                    kept.append(item)
            if moved:
                constraints["hard"] = kept
                if isinstance(soft, list):
                    for item in moved:
                        if item not in soft:
                            soft.append(item)
        if isinstance(soft, list) and "优先低成本/免费候选，但不等于严格预算 0" not in soft:
            soft.append("优先低成本/免费候选，但不等于严格预算 0")

    demand_profile.ensure_demand_profile(result)
    result["planningPolicy"] = planning_policy.schema_planning_policy(result, raw_input)
    return result


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True


def _validate_schema(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    """Validate the JSON Schema subset used by schema.json."""
    errors: list[str] = []

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_type_matches(value, item) for item in allowed_types):
            errors.append(f"{path}: expected {'/'.join(allowed_types)}, got {_json_type(value)}")
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is not in enum {schema['enum']!r}")

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: value {value!r} does not equal const {schema['const']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value {value!r} is below minimum {schema['minimum']!r}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value {value!r} is above maximum {schema['maximum']!r}")

    if isinstance(value, str) and "pattern" in schema:
        if not re.match(schema["pattern"], value):
            errors.append(f"{path}: value {value!r} does not match pattern {schema['pattern']!r}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for field in schema.get("required", []):
            if field not in value:
                errors.append(f"{path}.{field}: missing required field")

        if schema.get("additionalProperties") is False:
            allowed = set(properties)
            for field in value:
                if field not in allowed:
                    errors.append(f"{path}.{field}: unexpected field")

        for field, child_schema in properties.items():
            if field in value:
                errors.extend(_validate_schema(value[field], child_schema, f"{path}.{field}"))

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(_validate_schema(item, schema["items"], f"{path}[{index}]"))

    return errors


def basic_validate(result: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """
    Validate model output against schema.json.

    This small built-in validator covers the schema keywords used in this
    project: type, required, properties, additionalProperties, enum, const,
    minimum, maximum, pattern, and items.
    """
    return _validate_schema(result, schema, "$")


def extract_structured_demand(user_input: str, *, attempts: int = 2) -> dict[str, Any]:
    prompt = build_prompt(user_input)
    schema = load_json(SCHEMA_PATH)
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        retry_prompt = prompt
        if attempt:
            retry_prompt += (
                "\n\n上一轮输出无法解析或未通过 schema 校验。"
                "这次必须只返回一个完整 JSON object，不要省略任何必需字段，不要输出 Markdown。"
            )
        try:
            response_text = call_llm(retry_prompt)
            result = parse_json_object(response_text)
            result = normalize_structured_demand(result, user_input)
            errors = basic_validate(result, schema)
            if errors:
                raise ValueError("Stage 2 validation failed: " + "; ".join(errors))
            return result
        except Exception as exc:  # noqa: BLE001 - keep extraction robust across model failures.
            last_error = exc
            if attempt + 1 >= max(1, attempts):
                break
            print(f"[FlowCity][LLM] extraction retry after {type(exc).__name__}: {exc}", flush=True)
    assert last_error is not None
    raise last_error


def main() -> int:
    parser = argparse.ArgumentParser(description="FlowCity Stage 2 constraint extractor")
    parser.add_argument("--input", required=True, help="User natural-language input")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the final prompt without calling the LLM",
    )
    args = parser.parse_args()

    # dry-run 用来学习和调试：只看最终 Prompt，不真正请求模型。
    prompt = build_prompt(args.input)
    if args.dry_run:
        print(prompt)
        return 0

    # 正式运行：请求模型 -> 解析 JSON -> 用 schema 做基础校验。
    result = extract_structured_demand(args.input)
    errors = basic_validate(result, load_json(SCHEMA_PATH))

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        print("\nValidation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
