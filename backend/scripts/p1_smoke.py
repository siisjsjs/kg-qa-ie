"""P1 端到端冒烟脚本：覆盖规划文档 §七 P1 全部退出标准。

按顺序验证：
  1. /health 全绿
  2. POST /api/v1/init 创建空知识库目录
  3. 验证目录结构与种子文件
  4. GET /api/v1/admin/config（含可用 provider）
  5. PUT /api/v1/admin/config/llm（API key 加密落盘）
  6. GET 验证 API key 已被屏蔽
  7. POST /api/v1/admin/config/test （连通性测试触达真 provider）
  8. 二次 POST /init 应幂等

用法：
    # 1) 启 uvicorn（另一个终端）
    py -m uvicorn app.main:app --port 18765
    # 2) 跑本脚本
    py scripts/p1_smoke.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18765"


def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}


def section(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def assert_ok(cond: bool, msg: str) -> None:
    mark = "[PASS]" if cond else "[FAIL]"
    print(f"{mark} {msg}")
    if not cond:
        sys.exit(1)


def main() -> None:
    # ===== 1. /health =====
    section("1. /health")
    status, body = req("GET", "/health")
    print(f"  {status} {body}")
    assert_ok(status == 200 and body.get("status") == "ok", "health returns ok")

    # ===== 2. POST /init =====
    section("2. POST /api/v1/init")
    status, body = req("POST", "/api/v1/init")
    print(f"  {status} created={len(body.get('created', []))} skipped={len(body.get('skipped', []))}")
    print(f"  wiki_root={body.get('wiki_root')}")
    assert_ok(status == 201, "init returns 201")
    wiki_root = Path(body["wiki_root"])

    # ===== 3. 目录结构 =====
    section("3. wiki/ 目录结构")
    expected_files = [
        ".wiki-schema.md",
        "purpose.md",
        "index.md",
        "log.md",
        "overview.md",
        "wiki/_growth/gap-queue.md",
    ]
    for f in expected_files:
        exists = (wiki_root / f).exists()
        assert_ok(exists, f"file exists: {f}")

    expected_dirs = [
        "wiki/entities",
        "wiki/topics",
        "wiki/sources",
        "wiki/queries",
        "wiki/synthesis/sessions",
        "wiki/comparisons",
        "wiki/_growth",
        "raw/articles",
        "raw/zhihu",
        "raw/pdfs",
    ]
    for d in expected_dirs:
        is_dir = (wiki_root / d).is_dir()
        assert_ok(is_dir, f"dir exists: {d}")

    schema_text = (wiki_root / ".wiki-schema.md").read_text(encoding="utf-8")
    assert_ok("工业工程知识库" in schema_text, "schema.md contains IE marker")

    # ===== 4. GET /admin/config =====
    section("4. GET /api/v1/admin/config")
    status, body = req("GET", "/api/v1/admin/config")
    print(f"  {status} llm.provider={body['llm']['provider']} embedding.provider={body['embedding']['provider']}")
    print(f"  available_llm_providers={body['available_llm_providers']}")
    assert_ok(status == 200, "config GET 200")
    assert_ok(
        {"deepseek", "anthropic", "ollama", "openai_compat"}.issubset(set(body["available_llm_providers"])),
        "all 4 LLM providers registered",
    )

    # ===== 5. PUT /admin/config/llm =====
    section("5. PUT /api/v1/admin/config/llm")
    status, body = req(
        "PUT",
        "/api/v1/admin/config/llm",
        {
            "provider": "deepseek",
            "api_key": "sk-test-placeholder",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "temperature": 0.2,
            "max_tokens": 1024,
        },
    )
    print(f"  {status} {body}")
    assert_ok(status == 200 and body.get("ok"), "llm PUT ok")

    # ===== 6. API key 被屏蔽 =====
    section("6. GET 后 API key 屏蔽")
    status, body = req("GET", "/api/v1/admin/config")
    masked = body["llm"]["api_key"]
    print(f"  llm.api_key={masked!r}")
    assert_ok("*" in masked and masked.startswith("sk-F"), "api_key masked")
    assert_ok(body["llm"]["model"] == "deepseek-chat", "model persisted")

    # ===== 7. provider test =====
    section("7. POST /api/v1/admin/config/test (llm)")
    status, body = req("POST", "/api/v1/admin/config/test", {"target": "llm"})
    print(f"  {status} {body}")
    assert_ok(status == 200, "test endpoint 200")
    assert_ok(body.get("latency_ms") is not None, "real http request issued (latency present)")
    # 假 key 必然返回 ok=False，但 latency 必有 → 证明请求真发出去了
    assert_ok(body["ok"] is False, "fake key correctly rejected by real provider")
    assert_ok("401" in (body.get("error") or "") or "Authentication" in (body.get("error") or ""), "401 from real DeepSeek API")

    # ===== 8. /init 幂等 =====
    section("8. POST /init 二次调用幂等")
    status, body = req("POST", "/api/v1/init")
    print(f"  {status} created={len(body.get('created', []))} skipped={len(body.get('skipped', []))}")
    assert_ok(status == 201, "second init still 201")
    assert_ok(len(body["created"]) == 0, "no new files created")
    assert_ok(".wiki-schema.md" in body["skipped"], "schema.md correctly skipped")

    # ===== 全部通过 =====
    print()
    print("=" * 60)
    print("ALL P1 SMOKE CHECKS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
