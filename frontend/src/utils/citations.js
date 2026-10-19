const DATE_PREFIX = /^\d{4}-\d{2}-\d{2}-/;
const REF_SUFFIX = /^([EW]\d+)$/i;
// 模型偶尔把引用包进行内代码 `[[slug#E3]]`,渲染前先剥掉反引号还原成可解析 token
const CODE_WRAPPED_CITATION = /`+\s*(\[\[[^\[\]`]+\]\])\s*`+/g;

export function unwrapCodeCitations(source) {
  return String(source || "").replace(CODE_WRAPPED_CITATION, "$1");
}

export function fallbackCitationTitle(slug) {
  const text = String(slug || "")
    .replace(DATE_PREFIX, "")
    .replace(/-/g, " ")
    .trim();
  return text || "未命名来源";
}

// [[...]] 内部 token 解析(chunk 引用协议):
//   "工效学#E1" → {key:"工效学#E1", slug:"工效学", ref:"E1", type:"chunk"}
//   "web#W2"    → {key:"web#W2",    slug:"web#W2", ref:"W2", type:"web"}
//   "工效学"     → {key:"工效学",     slug:"工效学", ref:"",   type:"page"}
//   "工效学#简介"(锚点形态)→ 当页面引用,丢锚点
export function parseCitationToken(raw) {
  const token = String(raw || "").trim();
  if (token.includes("#")) {
    const idx = token.indexOf("#");
    const left = token.slice(0, idx).trim();
    const right = token.slice(idx + 1).trim();
    const m = REF_SUFFIX.exec(right);
    if (m) {
      const ref = m[1].toUpperCase();
      if (left.toLowerCase() === "web" || ref.startsWith("W")) {
        return { key: `web#${ref}`, slug: `web#${ref}`, ref, type: "web" };
      }
      return { key: `${left}#${ref}`, slug: left, ref, type: "chunk" };
    }
    return { key: left, slug: left, ref: "", type: "page" };
  }
  return { key: token, slug: token, ref: "", type: "page" };
}

export function normalizeCitations(citations) {
  const seen = new Set();
  const result = [];
  for (const raw of Array.isArray(citations) ? citations : []) {
    const item = typeof raw === "string" ? { slug: raw } : raw || {};
    const slug = String(item.slug || "").trim();
    if (!slug) continue;
    const type = item.type || (item.ref ? (String(item.ref).startsWith("W") ? "web" : "chunk") : "page");
    // 引用详情的匹配 key:chunk = slug#E1,web = web#W1(slug 本身),page = slug
    const key = type === "chunk" && item.ref ? `${slug}#${item.ref}` : slug;
    if (seen.has(key)) continue;
    seen.add(key);
    const suppliedTitle = String(item.title || "").trim();
    result.push({
      key,
      slug,
      type,
      ref: item.ref || "",
      title: suppliedTitle && suppliedTitle !== slug
        ? suppliedTitle
        : fallbackCitationTitle(slug),
      kind: String(item.kind || "").trim(),
      heading: item.heading || "",
      quote: item.quote || "",
      url: item.url || "",
      line_start: item.line_start ?? null,
      line_end: item.line_end ?? null,
    });
  }
  return result;
}

const CITATION_HEADING = /^\s{0,3}(?:#{1,6}\s*(?:引用来源|引用)|\*\*(?:引用来源|引用)\*\*\s*[:：]?|(?:引用来源|引用)\s*[:：])\s*$/;
const ANY_HEADING = /^\s{0,3}#{1,6}(?:\s|$)/;
// 列表前缀:空白、引用块 >、-*+ 项目符号、以及 1. / 1) / 1、这类编号
const LIST_MARKER = /^[\s>*+\-]*(?:\d+\s*[.)、])?\s*/;
const WIKILINK_RUN = /^(?:\[\[[^\[\]]+\]\][\s,，;；/、]*)+$/;
const NO_CITATION_MARK = /^[（(]\s*本回答未引用素材\s*[)）]$/;

function isBlank(line) {
  return line.trim() === "";
}

function isCitationLine(line) {
  if (ANY_HEADING.test(line)) return false;
  const body = line.replace(LIST_MARKER, "").trim();
  if (!body) return false;
  return WIKILINK_RUN.test(body) || NO_CITATION_MARK.test(body);
}

// 定位「标题 + 紧随的连续引用行」区块并只删除它,后面的章节照常保留。
// [[slug#E1]] / [[web#W1]] 形态同样被 WIKILINK_RUN 覆盖,无需特判。
export function stripCitationFooter(source) {
  const text = String(source || "");
  if (!text) return text;
  const lines = text.split("\n");
  const out = [];
  let removed = false;
  let i = 0;
  while (i < lines.length) {
    if (!CITATION_HEADING.test(lines[i])) {
      out.push(lines[i]);
      i += 1;
      continue;
    }
    // 区块只延伸到最后一条引用行:中间空行可以跨过,但空行自身不能把区块撑长
    let cursor = i + 1;
    let lastCitation = -1;
    while (cursor < lines.length) {
      const line = lines[cursor];
      if (ANY_HEADING.test(line)) break;
      if (isBlank(line)) {
        cursor += 1;
        continue;
      }
      if (!isCitationLine(line)) break;
      lastCitation = cursor;
      cursor += 1;
    }
    const trailingOnly = lines.slice(i + 1).every(isBlank);
    // 标题下一条引用都没有时保守放过,免得吃掉「解释引用规范」这类真正文小节
    const blockEnd = lastCitation >= 0
      ? lastCitation
      : (trailingOnly ? lines.length - 1 : -1);
    if (blockEnd < 0) {
      out.push(lines[i]);
      i += 1;
      continue;
    }
    removed = true;
    i = blockEnd + 1;
    while (i < lines.length && isBlank(lines[i])) i += 1;
    while (out.length && isBlank(out[out.length - 1])) out.pop();
    if (i < lines.length && out.length) out.push("");
  }
  return removed ? out.join("\n").trim() : text;
}

// 正文里可见引用 token(去重保序;key = 归一化 token,见 parseCitationToken)
export function extractVisibleCitationKeys(source) {
  const body = stripCitationFooter(unwrapCodeCitations(source));
  const seen = new Set();
  const keys = [];
  const pattern = /\[\[([^\[\]|]+?)(?:\|[^\[\]]+?)?\]\]/g;
  let match;
  while ((match = pattern.exec(body)) !== null) {
    const parsed = parseCitationToken(match[1]);
    if (parsed.key && !seen.has(parsed.key)) {
      seen.add(parsed.key);
      keys.push(parsed.key);
    }
  }
  return keys;
}

// 兼容旧调用:只要 KB 页面 slug(剥 #En 编号,web 不算)
export function extractVisibleCitationSlugs(source) {
  const seen = new Set();
  const slugs = [];
  for (const key of extractVisibleCitationKeys(source)) {
    const parsed = parseCitationToken(key);
    if (parsed.type === "web") continue;
    if (!seen.has(parsed.slug)) {
      seen.add(parsed.slug);
      slugs.push(parsed.slug);
    }
  }
  return slugs;
}

export function visibleCitationDetails(source, citations) {
  const normalized = normalizeCitations(citations);
  const byKey = new Map(normalized.map((item) => [item.key, item]));
  const bySlug = new Map();
  for (const item of normalized) {
    if (!bySlug.has(item.slug)) bySlug.set(item.slug, item);
  }
  return extractVisibleCitationKeys(source).map((key) => {
    if (byKey.has(key)) return byKey.get(key);
    const parsed = parseCitationToken(key);
    // 编号没对上时退回页面级详情
    return (
      bySlug.get(parsed.slug) || {
        key,
        slug: parsed.slug,
        type: parsed.type,
        ref: parsed.ref,
        title: fallbackCitationTitle(parsed.slug),
        kind: "",
        heading: "",
        quote: "",
        url: "",
      }
    );
  });
}
