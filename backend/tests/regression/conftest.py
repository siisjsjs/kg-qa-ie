"""QA 对抗回归基线的 fixture 与门禁。

这套测试打真实远程 LLM:14 问跑完要几分钟且花真钱,因此默认整体 skip,
只有显式 RUN_QA_REGRESSION=1 才收集执行。

不污染状态的三重保证:
  1. persist=False → persistor.maybe_persist 直接返回 None,不写 wiki/queries/
  2. session_id=None → 不写 qa_message / 不触发 auto_summarize
  3. monkeypatch append_gap → gap_flag 命中时不写 MySQL gap_queue 与 _growth/gap-queue.md
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

ENV_FLAG = "RUN_QA_REGRESSION"
ENV_IDS = "QA_REGRESSION_IDS"
ENV_USER_ID = "QA_REGRESSION_USER_ID"

HERE = Path(__file__).resolve().parent
QUESTION_SET = HERE / "questions_qa_adversarial.jsonl"
ARTIFACT_DIR = HERE / "_artifacts"


def _enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "qa_regression: 打真实 LLM 的 14 问对抗基线,需 RUN_QA_REGRESSION=1 才跑",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """默认给本目录所有测试打 marker 并 skip,普通 pytest 调用不会触发付费请求。"""
    if _enabled():
        return
    skip = pytest.mark.skip(reason=f"需要 {ENV_FLAG}=1 才运行(打真实 LLM,耗时数分钟)")
    for item in items:
        if HERE in Path(str(item.fspath)).parents or Path(str(item.fspath)).parent == HERE:
            item.add_marker(pytest.mark.qa_regression)
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolated_cache():
    """覆盖根 conftest 的同名 autouse fixture。

    根 fixture 把 wiki_root/cache_dir 指到 tmp_path,而基线必须打真实知识库与真实索引,
    所以这里显式退化成 no-op。
    """
    yield


@dataclass
class QuestionResult:
    """单问的完整 SSE 事件流 + 派生视图。"""

    qid: str
    scenario: str
    question: str
    in_domain: bool
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    duration_s: float = 0.0
    error: str | None = None
    gap_calls: list[dict[str, Any]] = field(default_factory=list)

    def of_type(self, event: str) -> list[dict[str, Any]]:
        return [data for name, data in self.events if name == event]

    def stage(self, name: str) -> dict[str, Any] | None:
        for data in self.of_type("progress"):
            if data.get("stage") == name:
                return data
        return None

    @property
    def types(self) -> list[str]:
        return [name for name, _ in self.events]

    @property
    def route(self) -> str | None:
        stage = self.stage("route")
        return stage.get("route") if stage else None

    @property
    def kind(self) -> str | None:
        # Agentic 链路里 kind 随 citations 事件携带;chitchat 也一样。
        events = self.of_type("citations")
        if events and events[-1].get("kind"):
            return events[-1]["kind"]
        return None

    @property
    def score_event(self) -> dict[str, Any]:
        events = self.of_type("score")
        return events[-1] if events else {}

    @property
    def score(self) -> float | None:
        raw = self.score_event.get("score")
        return float(raw) if raw is not None else None

    @property
    def tier(self) -> str | None:
        return self.score_event.get("tier")

    @property
    def gap_flag(self) -> bool | None:
        raw = self.score_event.get("gap_flag")
        return bool(raw) if raw is not None else None

    @property
    def reasons(self) -> list[str]:
        return list(self.score_event.get("reasons") or [])

    @property
    def citations(self) -> list[dict[str, Any]]:
        events = self.of_type("citations")
        return list(events[-1].get("citations") or []) if events else []

    @property
    def citation_slugs(self) -> list[str]:
        return [str(c.get("slug") or "") for c in self.citations]

    @property
    def expanded_keywords(self) -> list[str]:
        # Agentic 链路:keywords 随 stage=retrieval 携带(旧 alias_expand 已并入)
        stage = self.stage("retrieval") or self.stage("alias_expand")
        return list(stage.get("keywords") or []) if stage else []

    @property
    def paragraph_citation_coverage(self) -> float | None:
        events = self.of_type("evidence_validation")
        if not events:
            return None
        raw = events[-1].get("paragraph_citation_coverage")
        return float(raw) if raw is not None else None

    @property
    def answer(self) -> str:
        """token 全量拼接 + 末次 replace_answer。

        replace_answer 只携带被清洗后的正文,补充段可能只出现在 token 里,
        两段都并进 haystack 才能可靠判断引用是否以 [[...]] 形式渲染过。
        """
        streamed = "".join(str(d.get("text") or "") for d in self.of_type("token"))
        replaced = self.of_type("replace_answer")
        if replaced:
            streamed += "\n" + str(replaced[-1].get("text") or "")
        return streamed

    @property
    def persisted_slug(self) -> str | None:
        events = self.of_type("persist")
        return events[-1].get("persisted_query_slug") if events else None


def _load_question_set() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in QUESTION_SET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    only = os.environ.get(ENV_IDS, "").strip()
    if only:
        wanted = {part.strip().upper() for part in only.split(",") if part.strip()}
        rows = [row for row in rows if row["id"].upper() in wanted]
    return rows


async def _run_one(row: dict[str, Any], *, user_id: int) -> QuestionResult:
    """直接把 _stream_answer 当异步生成器消费,收集全量事件。"""
    from app.api.v1 import query as query_mod

    result = QuestionResult(
        qid=row["id"],
        scenario=row["scenario"],
        question=row["question"],
        in_domain=bool(row.get("in_domain")),
    )

    async def _recording_append_gap(**kwargs: Any) -> int:
        # 拦掉真实写库:只记录本次"本会写什么",供 artifact 诊断用。
        result.gap_calls.append({k: v for k, v in kwargs.items() if k != "db"})
        return -1

    started = time.perf_counter()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(query_mod, "append_gap", _recording_append_gap)
        try:
            async for chunk in query_mod._stream_answer(
                question=row["question"],
                domain_ie_override=None,
                persist_override=False,
                user_id=user_id,
                history=[],
                summary="",
                session_id=None,
            ):
                result.events.append(
                    (str(chunk.get("event")), json.loads(chunk.get("data") or "{}"))
                )
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
    result.duration_s = round(time.perf_counter() - started, 2)
    return result


async def _run_all(rows: list[dict[str, Any]], *, user_id: int) -> dict[str, QuestionResult]:
    # 串行:并发打同一个远程 LLM 既触发限流,也让延迟护栏失去意义。
    out: dict[str, QuestionResult] = {}
    for row in rows:
        out[row["id"]] = await _run_one(row, user_id=user_id)
    return out


_TABLE_KEY = pytest.StashKey[str]()

_TABLE_HEADER = (
    f"{'id':<4} {'scenario':<22} {'route':<16} {'kind':<11} "
    f"{'score':>7} {'tier':<8} {'cits':>4} {'gap':<6} {'cov':>5} {'secs':>6}"
)


def _render_table(results: dict[str, QuestionResult]) -> str:
    lines = ["", "QA 对抗回归基线结果", "-" * len(_TABLE_HEADER), _TABLE_HEADER,
             "-" * len(_TABLE_HEADER)]
    for qid in sorted(results):
        r = results[qid]
        if r.error:
            lines.append(f"{qid:<4} {r.scenario:<22} ERROR {r.error[:70]}")
            continue
        score = f"{r.score:.4f}" if r.score is not None else "-"
        cov = f"{r.paragraph_citation_coverage:.2f}" if r.paragraph_citation_coverage is not None else "-"
        lines.append(
            f"{qid:<4} {r.scenario:<22} {str(r.route or '-'):<16} {str(r.kind or '-'):<11} "
            f"{score:>7} {str(r.tier or '-'):<8} {len(r.citations):>4} "
            f"{str(r.gap_flag):<6} {cov:>5} {r.duration_s:>6.1f}"
        )
    lines.append("-" * len(_TABLE_HEADER))
    suppressed = sum(len(r.gap_calls) for r in results.values())
    lines.append(f"被拦截的 append_gap 调用: {suppressed}(未写入 gap_queue)")
    persisted = [r.qid for r in results.values() if r.persisted_slug]
    lines.append(f"落盘的 query 页: {persisted or '无'}")
    return "\n".join(lines)


def _dump_artifacts(results: dict[str, QuestionResult]) -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = ARTIFACT_DIR / f"qa-regression-{stamp}.json"
    payload = {
        "generated_at": stamp,
        "questions": [
            {
                "id": r.qid,
                "scenario": r.scenario,
                "question": r.question,
                "in_domain": r.in_domain,
                "duration_s": r.duration_s,
                "error": r.error,
                "route": r.route,
                "kind": r.kind,
                "score": r.score,
                "tier": r.tier,
                "gap_flag": r.gap_flag,
                "reasons": r.reasons,
                "citations": r.citations,
                "expanded_keywords": r.expanded_keywords,
                "paragraph_citation_coverage": r.paragraph_citation_coverage,
                "persisted_query_slug": r.persisted_slug,
                "suppressed_gap_calls": r.gap_calls,
                "answer": r.answer,
                "events": [{"event": name, "data": data} for name, data in r.events],
            }
            for r in (results[qid] for qid in sorted(results))
        ],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (ARTIFACT_DIR / f"qa-regression-{stamp}.txt").write_text(
        _render_table(results), encoding="utf-8"
    )
    return target


@pytest.fixture(scope="session")
def qa_results(request: pytest.FixtureRequest) -> dict[str, QuestionResult]:
    """整套问题只跑一次(每问都要钱),所有断言共享同一份事件流。"""
    if not _enabled():
        pytest.skip(f"需要 {ENV_FLAG}=1")
    rows = _load_question_set()
    user_id = int(os.environ.get(ENV_USER_ID, "1"))
    results = asyncio.run(_run_all(rows, user_id=user_id))
    artifact = _dump_artifacts(results)
    table = _render_table(results)
    print(table)
    print(f"原始事件已存档: {artifact}")
    request.config.stash[_TABLE_KEY] = table
    return results


def pytest_terminal_summary(terminalreporter, exitstatus, config: pytest.Config) -> None:
    """无论 -q / 捕获开关如何,跑完都把表格贴在末尾。"""
    table = config.stash.get(_TABLE_KEY, None)
    if table:
        terminalreporter.write_line(table)


def require(result: QuestionResult) -> QuestionResult:
    """断言前先确认这一问确实跑通了,避免异常被当成断言失败。"""
    if result.error:
        pytest.fail(f"{result.qid} 未跑通: {result.error}")
    return result
