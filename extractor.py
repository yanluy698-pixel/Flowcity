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
import sys
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
    }


def call_llm(prompt: str) -> str:
    config = get_config()

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

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM request failed: HTTP {exc.code}\n{body}") from exc

    content = data["choices"][0]["message"].get("content")
    if not content:
        raise RuntimeError(
            "LLM returned empty content. DeepSeek JSON Output may occasionally do this; retry or adjust the prompt."
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


def basic_validate(result: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """
    阶段二的轻量校验。
    现在先检查顶层字段和 scene 关键字段；后续可替换为完整 JSON Schema 校验器。
    """
    errors: list[str] = []

    required = schema.get("required", [])
    for field in required:
        if field not in result:
            errors.append(f"Missing top-level field: {field}")

    allowed = set(schema.get("properties", {}).keys())
    for field in result:
        if field not in allowed:
            errors.append(f"Unexpected top-level field: {field}")

    scene = result.get("scene")
    if isinstance(scene, dict):
        primary_type = scene.get("primaryType")
        allowed_scene_types = schema["properties"]["scene"]["properties"]["primaryType"]["enum"]
        if primary_type not in allowed_scene_types:
            errors.append(f"Invalid scene.primaryType: {primary_type}")

        confidence = scene.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append("scene.confidence must be a number between 0 and 1")
    elif "scene" in result:
        errors.append("scene must be an object")

    return errors


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
