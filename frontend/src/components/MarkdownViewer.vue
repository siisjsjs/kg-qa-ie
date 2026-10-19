<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch, nextTick } from "vue";
import { useRouter } from "vue-router";
import MarkdownIt from "markdown-it";
import mdFootnote from "markdown-it-footnote";
import katex from "katex";
import {
  normalizeCitations,
  parseCitationToken,
  stripCitationFooter,
  unwrapCodeCitations,
} from "@/utils/citations";

const props = defineProps({
  source: { type: String, default: "" },
  inline: { type: Boolean, default: false },
  citationMode: { type: Boolean, default: false },
  citations: { type: Array, default: () => [] },
  // {{image:id}} 占位的图片状态 map:{id: {status, url, prompt, error}}
  images: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["imageClick"]);

const router = useRouter();
const container = ref(null);

function renderMath(latex, displayMode) {
  try {
    return katex.renderToString(latex, {
      throwOnError: false,
      displayMode,
      output: "html",
      strict: "ignore",
    });
  } catch (e) {
    return `<code class="math-error">${escapeHtml(latex)}</code>`;
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// LLM 经常输出 \[ ... \] / \( ... \) / 甚至 \\[ ... \\] (双反斜杠转义)。
// 在送进 markdown-it 之前先规范化成 $$ / $,这样后面的规则只用一套就够。
// 规则:
//   \\[ ... \\]  → $$ ... $$  (块,允许跨行)
//   \\( ... \\)  → $ ... $    (行内,不允许跨行)
//   \[ ... \]    → $$ ... $$
//   \( ... \)    → $ ... $
function normalizeMath(src) {
  if (!src) return "";
  let s = src;
  // 双反斜杠先(转义形式),否则会被单反斜杠的规则误吃
  s = s.replace(/\\\\\[([\s\S]+?)\\\\\]/g, (_, body) => `$$${body}$$`);
  s = s.replace(/\\\\\(([^\n]+?)\\\\\)/g, (_, body) => `$${body}$`);
  // 单反斜杠
  s = s.replace(/\\\[([\s\S]+?)\\\]/g, (_, body) => `$$${body}$$`);
  s = s.replace(/\\\(([^\n]+?)\\\)/g, (_, body) => `$${body}$`);
  return s;
}

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
});
md.use(mdFootnote);

// fallback:LLM 经常只写 [^1] 引用,不给 [^1]: 定义。
// markdown-it-footnote 在没有定义时不会接,这里兜底把孤立的 [^N] 渲染成上标。
md.inline.ruler.after("footnote_ref", "orphan_footnote_ref", (state, silent) => {
  const start = state.pos;
  if (state.src.charCodeAt(start) !== 0x5b /* [ */) return false;
  if (state.src.charCodeAt(start + 1) !== 0x5e /* ^ */) return false;
  const closeIdx = state.src.indexOf("]", start + 2);
  if (closeIdx < 0) return false;
  const label = state.src.slice(start + 2, closeIdx).trim();
  // 标签必须非空且不含空格/换行/方括号(避免误吃 [^a b] 这种异常)
  if (!label || /[\s\[\]]/.test(label)) return false;
  if (!silent) {
    const t = state.push("html_inline", "", 0);
    t.content = `<sup class="footnote-ref orphan">[${escapeHtml(label)}]</sup>`;
  }
  state.pos = closeIdx + 1;
  return true;
});

// source slug 形如 2026-06-10-第五章-管理事务分析,日期前缀是 slug 的一部分
// (避免章节重名碰撞),不能从文件里删。这里仅在「显示」时剥掉日期前缀,
// 链接目标(data-slug / href)仍用完整 slug,跳转与后端匹配不受影响。
function displayWikilinkLabel(target) {
  return target.replace(/^\d{4}-\d{2}-\d{2}-/, "");
}

let activeCitationNumbers = new Map();
let activeCitationMeta = new Map();

// 对比表的「来源页」格子按 prompt 只放 [[X]],独占时上标 [N] 没有可标注的宿主句子,
// 0.72em + vertical-align:super 让它看着像坏掉的字符,用户原话「来源页 [2] 这种上标
// 老是会出问题」。这里判链接是否独占所在行:表格单元格/列表项本身就是独立 inline 源,
// breaks:true 下段落内独占一行的 [[X]] 也一并覆盖。
function isStandaloneWikilink(src, start, stop) {
  const lineStart = src.lastIndexOf("\n", start - 1) + 1;
  let lineEnd = src.indexOf("\n", stop);
  if (lineEnd < 0) lineEnd = src.length;
  return !src.slice(lineStart, start).trim() && !src.slice(stop, lineEnd).trim();
}

// ===== 双链 [[X]] / [[slug#En]] / [[web#Wn]] =====
md.inline.ruler.before("link", "wikilink", (state, silent) => {
  if (
    state.src.charCodeAt(state.pos) !== 0x5b ||
    state.src.charCodeAt(state.pos + 1) !== 0x5b
  )
    return false;
  const end = state.src.indexOf("]]", state.pos + 2);
  if (end < 0) return false;
  if (!silent) {
    const raw = state.src.slice(state.pos + 2, end);
    const [target, alias] = raw.split("|").map((s) => s.trim());
    if (!target) return false;
    const t = state.push("html_inline", "", 0);
    if (props.citationMode) {
      // chunk 引用协议:编号按归一化 token(工效学#E1 / web#W1 / 工效学)首现分配
      const parsed = parseCitationToken(target);
      const key = parsed.key;
      if (!activeCitationNumbers.has(key)) {
        activeCitationNumbers.set(key, activeCitationNumbers.size + 1);
      }
      const number = activeCitationNumbers.get(key);
      const meta = activeCitationMeta.get(key) || activeCitationMeta.get(parsed.slug) || {};
      if (parsed.type === "web") {
        // 网络来源:点击外链新标签打开
        const title = meta.title || `网络来源 ${parsed.ref}`;
        const url = meta.url || "";
        t.content = `<sup class="citation-ref web"><a data-url="${escapeHtml(url)}" href="${escapeHtml(url || "#")}" title="${escapeHtml(title)}" aria-label="网络来源 ${number}：${escapeHtml(title)}">[${number}]</a></sup>`;
        state.pos = end + 2;
        return true;
      }
      const slug = encodeURIComponent(parsed.slug);
      const title = alias || meta.title || `引用来源 ${number}`;
      const kind = meta.kind || "";
      // chunk 引用带 quote 前 80 字 + heading,点击跳 wiki 页做归一化高亮定位
      const hl = parsed.type === "chunk" && meta.quote ? String(meta.quote).slice(0, 80) : "";
      const heading = parsed.type === "chunk" ? meta.heading || "" : "";
      const attrs =
        `data-slug="${escapeHtml(parsed.slug)}" data-kind="${escapeHtml(kind)}"` +
        ` data-hl="${escapeHtml(hl)}" data-heading="${escapeHtml(heading)}" href="/kb/wiki/${slug}"`;
      if (isStandaloneWikilink(state.src, state.pos, end + 2)) {
        // 独占时给可读标题;`引用来源 N` 只适合当 tooltip,当正文会把格子写成废话
        const label = alias || meta.title || displayWikilinkLabel(parsed.slug);
        t.content = `<a class="citation-link" ${attrs} title="${escapeHtml(title)}" aria-label="引用 ${number}：${escapeHtml(title)}">${escapeHtml(label)}<span class="citation-link-no">[${number}]</span></a>`;
      } else {
        t.content = `<sup class="citation-ref"><a ${attrs} title="${escapeHtml(title)}" aria-label="引用 ${number}：${escapeHtml(title)}">[${number}]</a></sup>`;
      }
    } else {
      const slug = encodeURIComponent(target);
      const label = alias || displayWikilinkLabel(target);
      t.content = `<a class="wikilink" data-slug="${escapeHtml(target)}" href="/kb/wiki/${slug}">${escapeHtml(label)}</a>`;
    }
  }
  state.pos = end + 2;
  return true;
});

// ===== {{image:id}} 占位 → 图片卡(pending 骨架 / 完成图 / 失败态) =====
md.inline.ruler.before("link", "gen_image", (state, silent) => {
  if (
    state.src.charCodeAt(state.pos) !== 0x7b /* { */ ||
    state.src.charCodeAt(state.pos + 1) !== 0x7b
  )
    return false;
  const m = /^\{\{\s*image\s*:\s*([A-Za-z0-9_-]+)\s*\}\}/.exec(state.src.slice(state.pos));
  if (!m) return false;
  const id = m[1];
  if (!/^img_[a-f0-9]{12}$/.test(id)) {
    state.pos += m[0].length;
    return true;
  }
  if (!silent) {
    const info = (props.images || {})[id] || {};
    // 历史消息里 map 缺失时按 id 直拼 URL,onerror 降级为失败态
    const status = info.status || "ready";
    const url = info.url || `/api/v1/files/genimg/${id}.png`;
    const t = state.push("html_inline", "", 0);
    if (status === "failed") {
      t.content = `<span class="gen-image-failed" role="img" aria-label="示意图生成失败">⚠️ 示意图生成失败${info.error ? `：${escapeHtml(String(info.error).slice(0, 80))}` : ""}</span>`;
    } else if (status === "pending") {
      t.content = `<span class="gen-image-pending" data-image-id="${escapeHtml(id)}"><span class="gen-image-spinner"></span>示意图生成中…</span>`;
    } else {
      t.content =
        `<span class="gen-image-wrap"><img class="gen-image" src="${escapeHtml(url)}"` +
        ` data-image-id="${escapeHtml(id)}" alt="${escapeHtml(info.prompt || "示意图")}" loading="lazy"` +
        ` onerror="this.closest('.gen-image-wrap').classList.add('img-broken')" /></span>`;
    }
  }
  state.pos += m[0].length;
  return true;
});

// ===== inline math $...$ =====
md.inline.ruler.after("wikilink", "math_inline", (state, silent) => {
  if (state.src.charCodeAt(state.pos) !== 0x24) return false; // '$'
  // 不消耗 $$ 块
  if (state.src.charCodeAt(state.pos + 1) === 0x24) return false;
  // 必须左侧不是 \
  if (state.pos > 0 && state.src.charCodeAt(state.pos - 1) === 0x5c) return false;
  const start = state.pos + 1;
  let end = -1;
  for (let i = start; i < state.src.length; i++) {
    const c = state.src.charCodeAt(i);
    if (c === 0x5c) { i += 1; continue; } // \\ skip
    if (c === 0x24) { end = i; break; }
    if (c === 0x0a) return false;
  }
  if (end < 0 || end === start) return false;
  if (!silent) {
    const latex = state.src.slice(start, end);
    const t = state.push("html_inline", "", 0);
    t.content = renderMath(latex, false);
  }
  state.pos = end + 1;
  return true;
});

// ===== inline 级 display math $$...$$ =====
// 当 $$ 公式出现在「列表项内」时,markdown-it 会把列表项的续行折叠进同一个 inline
// 段落(breaks:true 下换行变 <br>),块级 math_block 规则拿不到独立 block,导致 $$ 与
// \frac 原样显示成乱码。这条 inline 规则兜底:在 inline 流里直接吃掉 $$...$$(允许跨行),
// 渲染成 display 公式。注册在 math_inline(单 $)之前,优先匹配双 $。
md.inline.ruler.after("wikilink", "math_display_inline", (state, silent) => {
  if (
    state.src.charCodeAt(state.pos) !== 0x24 ||
    state.src.charCodeAt(state.pos + 1) !== 0x24
  )
    return false;
  // 左侧不是 \(避免吃掉转义的 \$$)
  if (state.pos > 0 && state.src.charCodeAt(state.pos - 1) === 0x5c) return false;
  const start = state.pos + 2;
  let end = -1;
  for (let i = start; i < state.src.length - 1; i++) {
    const c = state.src.charCodeAt(i);
    if (c === 0x5c) { i += 1; continue; } // 跳过 \x 转义
    if (c === 0x24 && state.src.charCodeAt(i + 1) === 0x24) { end = i; break; }
  }
  if (end < 0 || end === start) return false;
  if (!silent) {
    const latex = state.src.slice(start, end).trim();
    const t = state.push("html_inline", "", 0);
    t.content = `<span class="math-block">${renderMath(latex, true)}</span>`;
  }
  state.pos = end + 2;
  return true;
});

// ===== block math $$...$$ =====
// 单行 $$...$$  允许后面跟附加内容(如 [^1] / 普通文字),trailing 部分作为段落继续渲染
md.block.ruler.before(
  "paragraph",
  "math_block",
  (state, startLine, endLine, silent) => {
    const startPos = state.bMarks[startLine] + state.tShift[startLine];
    const max = state.eMarks[startLine];
    if (startPos + 2 > max) return false;
    if (state.src.slice(startPos, startPos + 2) !== "$$") return false;

    const firstLineRest = state.src.slice(startPos + 2, max);
    // 单行 $$...$$ (闭合 $$ 出现在同一行)
    const closeOnSameLine = firstLineRest.indexOf("$$");
    if (closeOnSameLine >= 0) {
      const latex = firstLineRest.slice(0, closeOnSameLine);
      const trailing = firstLineRest.slice(closeOnSameLine + 2).trim();
      if (!silent) {
        const token = state.push("html_block", "", 0);
        let html = `<div class="math-block">${renderMath(latex, true)}</div>`;
        if (trailing) {
          // trailing 作为 inline 走一遍,保留 [^1] / wikilink / 等
          html += md.renderInline(trailing);
        }
        token.content = html;
        token.map = [startLine, startLine + 1];
      }
      state.line = startLine + 1;
      return true;
    }

    // 多行块:逐行找闭合 $$
    let line = startLine + 1;
    let buf = firstLineRest;
    let endFound = false;
    let lastLine = startLine;
    let trailing = "";
    while (line < endLine) {
      const lstart = state.bMarks[line] + state.tShift[line];
      const lmax = state.eMarks[line];
      const lineText = state.src.slice(lstart, lmax);
      const closeIdx = lineText.indexOf("$$");
      if (closeIdx >= 0) {
        buf += "\n" + lineText.slice(0, closeIdx);
        trailing = lineText.slice(closeIdx + 2).trim();
        endFound = true;
        lastLine = line;
        break;
      }
      buf += "\n" + lineText;
      line += 1;
    }
    if (!endFound) return false;
    if (!silent) {
      const token = state.push("html_block", "", 0);
      let html = `<div class="math-block">${renderMath(buf.trim(), true)}</div>`;
      if (trailing) html += md.renderInline(trailing);
      token.content = html;
      token.map = [startLine, lastLine + 1];
    }
    state.line = lastLine + 1;
    return true;
  },
  { alt: [] }
);

// 剥离 [[X]] 后面的关系元数据注释 <!-- type: X --> <!-- confidence: X -->。
// 这些注释是后端抽取 wiki_links 用的(link_resolver.py),不该展示给用户。
// markdown-it 在 html:false 模式下会把 <!-- ... --> 当纯文本显示出来,所以这里先清掉。
// 兼容 LLM 写坏的未闭合形式(如 `<!-- type: 相关 <!-- confidence: EXTRACTED -->`)。
function stripMetaComments(src) {
  if (!src) return "";
  return (
    src
      // 先处理损坏的:第一个注释缺 -->,直接连到下一个 <!--
      .replace(/<!--\s*type\s*:[^>]*?(?=<!--)/g, "")
      // 标准闭合的 type / confidence 注释
      .replace(/<!--\s*(?:type|confidence)\s*:[\s\S]*?-->/g, "")
      // 残留的孤立 --> 收尾
      .replace(/[ \t]*-->/g, "")
      // 行尾因删注释留下的多余空格
      .replace(/[ \t]+$/gm, "")
  );
}

const html = computed(() => {
  const citationList = normalizeCitations(props.citations);
  activeCitationNumbers = new Map();
  // key(工效学#E1 / web#W1)精确命中优先,slug 兜底(旧页面级引用)
  activeCitationMeta = new Map();
  for (const item of citationList) {
    activeCitationMeta.set(item.key, item);
    if (!activeCitationMeta.has(item.slug)) activeCitationMeta.set(item.slug, item);
  }
  const visibleSource = props.citationMode
    ? stripCitationFooter(unwrapCodeCitations(props.source || ""))
    : props.source || "";
  const src = stripMetaComments(normalizeMath(visibleSource));
  if (!src) return "";
  return props.inline ? md.renderInline(src) : md.render(src);
});

function onClick(ev) {
  const img = ev.target.closest("img.gen-image");
  if (img) {
    ev.preventDefault();
    emit("imageClick", {
      imageId: img.getAttribute("data-image-id") || "",
      url: img.getAttribute("src") || "",
    });
    return;
  }
  // 正文里的普通外链(裸 URL / markdown 链接):新标签打开,不打断当前对话
  const extLink = ev.target.closest("a");
  if (extLink && /^https?:/i.test(extLink.getAttribute("href") || "")) {
    ev.preventDefault();
    window.open(extLink.getAttribute("href"), "_blank", "noopener");
    return;
  }
  const el = ev.target.closest("a.wikilink, a.citation-link, .citation-ref a");
  if (!el) return;
  ev.preventDefault();
  // 网络来源:外链新标签打开
  const url = el.getAttribute("data-url");
  if (url) {
    window.open(url, "_blank", "noopener");
    return;
  }
  const slug = el.getAttribute("data-slug");
  const kind = el.getAttribute("data-kind");
  if (slug) {
    const query = {};
    if (kind) query.kind = kind;
    // chunk 引用:带上原文片段 + 小节名,wiki 页做定位高亮
    const hl = el.getAttribute("data-hl");
    const heading = el.getAttribute("data-heading");
    if (hl) query.hl = hl;
    if (heading) query.heading = heading;
    router.push({
      path: `/kb/wiki/${encodeURIComponent(slug)}`,
      query,
    });
  }
}

function onDblClick(ev) {
  // 双击正文里的生成图片 → 新标签页打开原图(与浏览器直接打开图片一致)
  const img = ev.target.closest("img.gen-image");
  if (!img) return;
  ev.preventDefault();
  const url = img.getAttribute("src");
  if (url) window.open(url, "_blank", "noopener");
}

onMounted(() => {
  container.value?.addEventListener("click", onClick);
  container.value?.addEventListener("dblclick", onDblClick);
});
onBeforeUnmount(() => {
  container.value?.removeEventListener("click", onClick);
  container.value?.removeEventListener("dblclick", onDblClick);
});
</script>

<template>
  <div ref="container" class="markdown-viewer" v-html="html" />
</template>

<style scoped>
.markdown-viewer :deep(.math-block) {
  margin: 12px 0;
  overflow-x: auto;
  text-align: center;
}
.markdown-viewer :deep(.math-error) {
  color: #ef4444;
  background: #fef2f2;
}

/* 脚注 */
.markdown-viewer :deep(.footnotes) {
  margin-top: 16px;
  padding-top: 8px;
  border-top: 1px solid #e5e7eb;
  font-size: 12.5px;
  color: #6b7280;
}
.markdown-viewer :deep(.footnotes hr) { display: none; }
.markdown-viewer :deep(.footnotes ol) {
  padding-left: 20px;
  margin: 0;
}
.markdown-viewer :deep(.footnote-ref) {
  font-size: 0.8em;
  vertical-align: super;
  line-height: 0;
  margin: 0 1px;
}
.markdown-viewer :deep(.footnote-ref.orphan) {
  color: #6b7280;
  font-weight: 500;
}
.markdown-viewer :deep(.footnote-backref) {
  text-decoration: none;
  margin-left: 4px;
}

.markdown-viewer :deep(.citation-ref) {
  margin: 0 1px;
  font-size: 0.72em;
  line-height: 0;
  vertical-align: super;
}
.markdown-viewer :deep(.citation-ref a) {
  color: #64748b;
  text-decoration: none;
  font-weight: 600;
  border: 0;
  background: transparent;
}
.markdown-viewer :deep(.citation-ref a:hover) {
  color: #2563eb;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.markdown-viewer :deep(a.citation-link) {
  color: #475569;
  text-decoration: none;
  border-bottom: 1px dashed #94a3b8;
  cursor: pointer;
}
.markdown-viewer :deep(a.citation-link:hover) {
  color: #2563eb;
  border-bottom-color: #2563eb;
}
.markdown-viewer :deep(.citation-link-no) {
  margin-left: 3px;
  font-size: 0.82em;
  color: #94a3b8;
}
.markdown-viewer :deep(a.citation-link:hover .citation-link-no) {
  color: #2563eb;
}

.markdown-viewer :deep(.citation-ref.web a) {
  color: #0e9f6e;
}
.markdown-viewer :deep(.citation-ref.web a:hover) {
  color: #057a55;
}

/* ===== 生成图片卡 ===== */
.markdown-viewer :deep(.gen-image-wrap) {
  display: block;
  margin: 12px auto;
  text-align: center;
}
.markdown-viewer :deep(img.gen-image) {
  max-width: min(480px, 100%);
  border-radius: 10px;
  border: 1px solid #e5e7eb;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.08);
  cursor: zoom-in;
  display: inline-block;
  margin: 0 auto;
}
.markdown-viewer :deep(.gen-image-wrap.img-broken img) {
  display: none;
}
.markdown-viewer :deep(.gen-image-wrap.img-broken)::after {
  content: "⚠️ 示意图暂不可用";
  display: inline-block;
  padding: 10px 14px;
  border: 1px dashed #d1d5db;
  border-radius: 10px;
  color: #9ca3af;
  font-size: 13px;
}
.markdown-viewer :deep(.gen-image-pending) {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0;
  padding: 12px 16px;
  border: 1px dashed #c7d2fe;
  border-radius: 10px;
  background: #f5f7ff;
  color: #6172a8;
  font-size: 13px;
}
.markdown-viewer :deep(.gen-image-spinner) {
  width: 14px;
  height: 14px;
  border: 2px solid #c7d2fe;
  border-top-color: #4f6ef7;
  border-radius: 50%;
  animation: gen-img-spin 0.9s linear infinite;
}
@keyframes gen-img-spin {
  to { transform: rotate(360deg); }
}
.markdown-viewer :deep(.gen-image-failed) {
  display: inline-block;
  margin: 10px 0;
  padding: 10px 14px;
  border: 1px dashed #fecaca;
  border-radius: 10px;
  background: #fef2f2;
  color: #b91c1c;
  font-size: 13px;
}

/* 行内公式不要被强制断 */
.markdown-viewer :deep(.katex) {
  white-space: nowrap;
}
.markdown-viewer :deep(.math-block .katex-display) {
  margin: 0;
}
</style>
