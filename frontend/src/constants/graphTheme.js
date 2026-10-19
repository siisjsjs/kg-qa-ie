/**
 * 知识图谱配色 —— 简约学术风
 *
 * 1) 社区染色：低饱和大地色 8 色循环（默认推荐）
 * 2) kind 染色：保留语义但全部降低饱和与亮度
 * 3) 高亮 / 路径 / 静态色 暗模式各自一套
 * 4) i18n: kind / insight / link_type 中文标签
 */

/** wiki kind 中文标签（前后端、图谱、Wiki 浏览 单一来源） */
export const KIND_LABELS = {
  entity: "实体",
  topic: "主题",
  source: "素材",
  query: "查询",
  synthesis: "综合",
  comparison: "对比",
};

/** insight kind 中文标签 + 描述 */
export const INSIGHT_LABELS = {
  surprising_link: { title: "跨社区高权重边", desc: "横跨两个社区且权重较高的连接，揭示惊喜联系" },
  bridge_node: { title: "桥节点", desc: "连接多个社区的枢纽节点，常是学科交叉点" },
  knowledge_gap: { title: "知识缺口", desc: "度数偏低的实体，可能需要补充素材" },
  sparse_community: { title: "稀疏社区", desc: "节点数少的小社区，可能是孤立子领域" },
};

/** wikilink type → 中文（用于详情/边描述） */
export const LINK_TYPE_LABELS = {
  实现: "实现",
  依赖: "依赖",
  衍生: "衍生",
  对比: "对比",
  矛盾: "矛盾",
  related: "相关",
};

export function kindLabel(k) {
  return KIND_LABELS[k] || k || "未知";
}
export function insightLabel(k) {
  return INSIGHT_LABELS[k]?.title || k;
}

export const COMMUNITY_PALETTE_LIGHT = [
  "#1e3a5f", // 藏青
  "#a87b3d", // 学术金
  "#6b7f4f", // 苔绿
  "#8b3a3a", // 砖红
  "#7d6b3d", // 橄榄
  "#5b4a6b", // 紫灰
  "#3d6b6b", // 青灰
  "#8a5a3a", // 陶土
];

export const COMMUNITY_PALETTE_DARK = [
  "#7aa2c4",
  "#d4a574",
  "#a8b88a",
  "#c87a7a",
  "#bdaa78",
  "#9b8caf",
  "#7daaaa",
  "#c89a7a",
];

/* kind 调色：知识库只有 source / topic / entity 三类,三色拉开层次。
 * source(素材) = 中心锚点,最暖最亮的学术金;
 * topic(主题)  = 中圈骨架,沉稳藏青蓝;
 * entity(实体) = 外圈,数量最多,降饱和苔绿,避免喧宾夺主。
 * 其余 kind 保留兜底色(理论上已被三类过滤,不会渲染)。 */
export const KIND_PALETTE = {
  light: {
    source: "#b8842f",   // 学术金 — 中心
    topic: "#2f5a8c",    // 藏青蓝 — 中圈
    entity: "#6b8f5a",   // 苔绿 — 外圈
    query: "#6b4a8a",
    synthesis: "#a04a6b",
    comparison: "#3d7d7a",
  },
  dark: {
    source: "#e0b066",   // 暖金
    topic: "#7aa6d6",    // 亮藏青
    entity: "#9cc080",   // 亮苔绿
    query: "#b89adb",
    synthesis: "#d48aa3",
    comparison: "#7dc4c0",
  },
};

/* 三类节点的圈层层级(中心放射布局用):
 * 0 = 最内(素材),1 = 中圈(主题),2 = 外圈(实体)。 */
export const KIND_RING = {
  source: 0,
  topic: 1,
  entity: 2,
};

/* 知识库只展示的三类(顺序即图例/圈层由内到外) */
export const CORE_KINDS = ["source", "topic", "entity"];

const DEFAULT = { light: "#64748b", dark: "#94a3b8" };

export const HIGHLIGHT = {
  light: { node: "#dc2626", link: "#dc2626", neighbor: "#1e3a5f" },
  dark: { node: "#f87171", link: "#f87171", neighbor: "#a8c4e0" },
};

export const SURFACE = {
  light: {
    bg: "#fafaf7",
    canvas: "#ffffff",
    link: "rgba(100, 116, 139, 0.35)",
    linkDim: "rgba(100, 116, 139, 0.08)",
    label: "#1f2937",
    labelMuted: "#94a3b8",
    grid: "rgba(15, 23, 42, 0.04)",
  },
  dark: {
    bg: "#0a0e14",
    canvas: "#0f1419",
    link: "rgba(160, 180, 200, 0.25)",
    linkDim: "rgba(160, 180, 200, 0.06)",
    label: "#e5e7eb",
    labelMuted: "#64748b",
    grid: "rgba(255, 255, 255, 0.04)",
  },
};

export function communityColor(cid, theme = "light") {
  const pal = theme === "dark" ? COMMUNITY_PALETTE_DARK : COMMUNITY_PALETTE_LIGHT;
  if (cid == null) return DEFAULT[theme];
  return pal[cid % pal.length];
}

export function kindColor(kind, theme = "light") {
  const pal = KIND_PALETTE[theme] || KIND_PALETTE.light;
  return pal[kind] || DEFAULT[theme];
}

export function nodeColor(node, { colorBy = "community", theme = "light" } = {}) {
  if (colorBy === "kind") return kindColor(node.kind || "entity", theme);
  return communityColor(node.community_id, theme);
}
