"""EvidencePool:Agent 循环的证据编号与引用解析(改造计划 §6.1)。

- KB chunk 编 ``E1..En``(跨轮次接续:预取一批,search_kb 工具再补,编号连续)
- Web 结果编 ``W1..Wn``
- ``render_for_prompt()`` 输出证据区块文本;``resolve(ref)`` 做引用解析
"""

from __future__ import annotations


class EvidencePool:
    def __init__(self) -> None:
        self._kb: list[dict] = []  # 每项含 ref/chunk_id/slug/title/kind/heading/text/line_start/line_end
        self._kb_ids: set[str] = set()
        self._web: list[dict] = []  # 每项含 ref/url/title/content
        self._web_urls: set[str] = set()

    # ---- KB chunks ----

    def add_chunks(self, chunks: list[dict]) -> list[dict]:
        """并入 chunk 候选(按 chunk_id 去重),返回本次新增项(已带 ref)。"""
        added: list[dict] = []
        for c in chunks or []:
            cid = str(c.get("chunk_id") or "")
            if not cid or cid in self._kb_ids:
                continue
            self._kb_ids.add(cid)
            item = dict(c)
            item["ref"] = f"E{len(self._kb) + 1}"
            self._kb.append(item)
            added.append(item)
        return added

    def add_web_results(self, results: list[dict]) -> list[dict]:
        """并入 web 搜索结果(按 url 去重),返回本次新增项(已带 ref)。"""
        added: list[dict] = []
        for r in results or []:
            url = str(r.get("url") or "").strip()
            if not url or url in self._web_urls:
                continue
            self._web_urls.add(url)
            item = {
                "ref": f"W{len(self._web) + 1}",
                "url": url,
                "title": str(r.get("title") or url),
                "content": str(r.get("content") or ""),
            }
            self._web.append(item)
            added.append(item)
        return added

    # ---- 读取 ----

    @property
    def kb_items(self) -> list[dict]:
        return list(self._kb)

    @property
    def web_items(self) -> list[dict]:
        return list(self._web)

    def is_empty(self) -> bool:
        return not self._kb and not self._web

    def resolve(self, ref: str) -> dict | None:
        """按编号解析证据项。ref 形如 'E3' / 'W1',大小写不敏感。"""
        r = str(ref or "").strip().upper()
        if r.startswith("E"):
            try:
                idx = int(r[1:]) - 1
            except ValueError:
                return None
            return self._kb[idx] if 0 <= idx < len(self._kb) else None
        if r.startswith("W"):
            try:
                idx = int(r[1:]) - 1
            except ValueError:
                return None
            return self._web[idx] if 0 <= idx < len(self._web) else None
        return None

    def slug_exists_in_pool(self, slug: str) -> bool:
        return any(c.get("slug") == slug for c in self._kb)

    # ---- prompt 渲染 ----

    @staticmethod
    def render_chunk(item: dict) -> str:
        heading = item.get("heading") or "引言"
        lines = ""
        if item.get("line_start") and item.get("line_end"):
            lines = f"(行 {item['line_start']}-{item['line_end']})"
        return (
            f"[{item['ref']}] [[{item.get('slug')}]] · {item.get('title') or item.get('slug')}"
            f" · {heading} {lines}\n{item.get('text') or ''}"
        )

    @staticmethod
    def render_web(item: dict) -> str:
        return f"[{item['ref']}] {item.get('title')} — {item.get('url')}\n{item.get('content') or ''}"

    def render_for_prompt(self, *, items: list[dict] | None = None) -> str:
        """渲染证据区块。items 传入时只渲染这批(工具增量回填用),否则渲染全部。"""
        if items is not None:
            parts = [
                self.render_web(it) if str(it.get("ref", "")).startswith("W") else self.render_chunk(it)
                for it in items
            ]
            return "\n\n".join(parts) if parts else "(无新增证据)"
        parts = [self.render_chunk(it) for it in self._kb]
        parts.extend(self.render_web(it) for it in self._web)
        return "\n\n".join(parts) if parts else "(无相关素材)"
