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


ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema.json"
PROMPT_PATH = ROOT / "prompt.md"
EXAMPLES_PATH = ROOT / "examples.json"
ENV_PATH = ROOT / ".env"

# 默认使用 DeepSeek 的 OpenAI 兼容接口；具体 Key 不写在代码里。
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_LLM_RETRIES = 2


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


def build_prompt(user_input: str) -> str:
    # 把 prompt 模板、schema 规则、few-shot 示例拼在一起发给模型。
    # schema.json 规定输出结构，examples.json 告诉模型正确样例长什么样。
    prompt_template = load_text(PROMPT_PATH)
    schema = load_json(SCHEMA_PATH)
    examples = load_json(EXAMPLES_PATH)

    compact_schema = json.dumps(schema, ensure_ascii=False, indent=2)
    compact_examples = json.dumps(examples, ensure_ascii=False, indent=2)

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


def call_llm(prompt: str) -> str:
    config = get_config()
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
        "max_tokens": config["max_tokens"],
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
    for attempt in range(config["retries"] + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
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

        if attempt < config["retries"]:
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


def normalize_structured_demand(result: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize known model drift after extraction.

    Product decision: "不想花钱 / 少花钱 / 预算越低越好" is a low-cost
    preference, and "最好免费 / 优先免费" is a free preference. Neither is
    a strict CNY 0 budget unless the user explicitly says zero budget or
    must be free.
    """
    raw_input = str(result.get("rawInput") or "")
    _normalize_child_accompanying_adult(result, raw_input)

    has_low_cost_intent = _has_low_cost_intent(raw_input)
    has_free_preference = _has_free_preference(raw_input)
    if not (has_low_cost_intent or has_free_preference) or _has_explicit_zero_budget(raw_input):
        return result

    budget = result.get("budget")
    if not isinstance(budget, dict):
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
    response_text = call_llm(prompt)
    result = parse_json_object(response_text)
    result = normalize_structured_demand(result)
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
