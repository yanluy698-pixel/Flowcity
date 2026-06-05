"""Download and warm the optional local FlowCity embedding model."""

from __future__ import annotations

import argparse
import os


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.getenv("FLOWCITY_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"))
    parser.add_argument("--cache-dir", default=os.getenv("FLOWCITY_EMBEDDING_CACHE_DIR"))
    args = parser.parse_args()

    from fastembed import TextEmbedding

    kwargs = {"model_name": args.model}
    if args.cache_dir:
        kwargs["cache_dir"] = args.cache_dir
    model = TextEmbedding(**kwargs)
    list(model.embed(["FlowCity 本地向量模型初始化完成"]))
    print(f"Embedding model ready: {args.model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
