import assert from "node:assert/strict";
import test from "node:test";

import {
  fallbackCitationTitle,
  extractVisibleCitationSlugs,
  normalizeCitations,
  stripCitationFooter,
  visibleCitationDetails,
} from "../src/utils/citations.js";

test("normalizes citation metadata without exposing a dated slug", () => {
  const slug = "2026-06-11-第十五章-面向中国制造-2025-的工业工程";
  assert.deepEqual(
    normalizeCitations([{ slug, title: slug, kind: "source" }]),
    [{
      slug,
      title: "第十五章 面向中国制造 2025 的工业工程",
      kind: "source",
    }],
  );
});

test("keeps the backend title while preserving the navigation slug", () => {
  const slug = "2026-06-11-第十五章-面向中国制造-2025-的工业工程";
  assert.deepEqual(
    normalizeCitations([{
      slug,
      title: "第十五章 面向中国制造2025的工业工程",
      kind: "source",
    }]),
    [{
      slug,
      title: "第十五章 面向中国制造2025的工业工程",
      kind: "source",
    }],
  );
});

test("removes a generated citation footer but keeps inline citations", () => {
  const source = "正文结论 [[智能制造]]。\n\n## 引用\n- [[智能制造]]\n- [[制造执行系统]]";
  assert.equal(stripCitationFooter(source), "正文结论 [[智能制造]]。");
});

test("does not remove a real section that contains prose", () => {
  const source = "## 引用\n这部分解释引用规范，不是来源列表。";
  assert.equal(stripCitationFooter(source), source);
});

test("removes a mid-document footer and keeps the appended sections", () => {
  const source = [
    "## 结论",
    "动素是动作分析的最小单位 [[动素]]。",
    "",
    "## 引用",
    "- [[2026-06-11-第七章-动作分析]]",
    "- [[动素]]",
    "",
    "## 补充",
    "补充说明:动素分析需要配合影片分析同步进行。",
    "",
    "## 适用条件",
    "适用于高重复性的手工装配工位。",
  ].join("\n");
  assert.equal(
    stripCitationFooter(source),
    [
      "## 结论",
      "动素是动作分析的最小单位 [[动素]]。",
      "",
      "## 补充",
      "补充说明:动素分析需要配合影片分析同步进行。",
      "",
      "## 适用条件",
      "适用于高重复性的手工装配工位。",
    ].join("\n"),
  );
});

test("removes every footer when the answer contains more than one", () => {
  const source = [
    "第一段 [[A]]。",
    "",
    "## 引用",
    "- [[A]]",
    "",
    "## 补充",
    "第二段 [[B]]。",
    "",
    "## 引用",
    "- [[B]]",
  ].join("\n");
  assert.equal(
    stripCitationFooter(source),
    ["第一段 [[A]]。", "", "## 补充", "第二段 [[B]]。"].join("\n"),
  );
});

test("keeps body lines that mix prose with a wikilink", () => {
  const source = [
    "## 引用",
    "- [[动素]]",
    "",
    "## 补充",
    "参见 [[动作经济原则]] 一节的三条准则。",
    "- 前置条件见 [[工序分析]] 与现场记录。",
  ].join("\n");
  assert.equal(
    stripCitationFooter(source),
    [
      "## 补充",
      "参见 [[动作经济原则]] 一节的三条准则。",
      "- 前置条件见 [[工序分析]] 与现场记录。",
    ].join("\n"),
  );
});

test("removes the explicit no-citation placeholder footer", () => {
  const source = "纯推理结论，没有素材支撑。\n\n## 引用\n- (本回答未引用素材)";
  assert.equal(stripCitationFooter(source), "纯推理结论，没有素材支撑。");
});

test("returns the text untouched when there is no footer", () => {
  const source = "## 结论\n动素清单共 18 项 [[动素]]。\n\n## 补充\n注意疲劳因素。";
  assert.equal(stripCitationFooter(source), source);
});

test("ignores footer-only slugs when the footer sits mid-document", () => {
  const source = [
    "正文 [[A]]。",
    "",
    "## 引用",
    "- [[A]]",
    "- [[C]]",
    "",
    "## 补充",
    "补充正文 [[B]]。",
  ].join("\n");
  assert.deepEqual(extractVisibleCitationSlugs(source), ["A", "B"]);
});

test("fallback title never returns the raw dated slug", () => {
  assert.equal(fallbackCitationTitle("2026-01-02-第一章-绪论"), "第一章 绪论");
});

test("source list follows only citations actually used in visible prose", () => {
  const source = "正文 [[A]] 和 [[B|别名]]。\n\n## 引用\n- [[A]]\n- [[B]]\n- [[C]]";
  assert.deepEqual(extractVisibleCitationSlugs(source), ["A", "B"]);
  assert.deepEqual(
    visibleCitationDetails(source, [
      { slug: "A", title: "来源 A", kind: "entity" },
      { slug: "B", title: "来源 B", kind: "topic" },
      { slug: "C", title: "来源 C", kind: "source" },
    ]),
    [
      { slug: "A", title: "来源 A", kind: "entity" },
      { slug: "B", title: "来源 B", kind: "topic" },
    ],
  );
});
