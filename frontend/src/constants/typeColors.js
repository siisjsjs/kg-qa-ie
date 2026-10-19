/**
 * 实体类型颜色映射（沿用旧项目 IE 配色，2D / 3D 共用）
 */
export const typeColors = {
  // 核心理论层
  学科: "#4f46e5",
  概念: "#2563eb",
  术语: "#0284c7",
  理论: "#7c3aed",
  原则: "#9333ea",
  模型: "#c026d3",
  体系: "#db2777",

  // 方法与工具层
  方法: "#059669",
  技术: "#0d9488",
  工具: "#0891b2",
  流程: "#0284c7",
  步骤: "#3b82f6",
  要素: "#06b6d4",
  动作: "#10b981",
  动素: "#84cc16",

  // 物理实体层
  设备: "#65a30d",
  材料: "#d97706",
  产品: "#ea580c",
  产线: "#dc2626",

  // 数据与标准层
  指标: "#eab308",
  参数: "#ca8a04",
  公式: "#b45309",
  标准: "#d97706",
  规范: "#ea580c",
  目标: "#c2410c",

  // 组织与人物
  组织: "#7e22ce",
  人物: "#be185d",
  职能: "#a21caf",

  // 上下文
  地点: "#475569",
  时间: "#64748b",
  事件: "#94a3b8",
  问题: "#ef4444",
  浪费: "#b91c1c",
  现象: "#f87171",

  // wiki 节点 kind（新方法论）
  entity: "#2563eb",
  topic: "#059669",
  source: "#d97706",
  query: "#7c3aed",
  synthesis: "#db2777",
  comparison: "#0d9488",
};

export const HIGHLIGHT_COLOR = "#e11d48";
export const DEFAULT_COLOR = "#64748b";

const DYNAMIC_PALETTE = [
  "#6366f1", "#8b5cf6", "#a855f7", "#d946ef", "#ec4899",
  "#f43f5e", "#ef4444", "#f97316", "#f59e0b", "#eab308",
  "#84cc16", "#22c55e", "#10b981", "#14b8a6", "#06b6d4",
  "#0ea5e9", "#3b82f6", "#a78bfa", "#c084fc", "#e879f9",
];

export function generateColorForType(name) {
  if (!name) return DEFAULT_COLOR;
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = ((hash << 5) - hash + name.charCodeAt(i)) | 0;
  }
  return DYNAMIC_PALETTE[Math.abs(hash) % DYNAMIC_PALETTE.length];
}

export function colorOf(type) {
  return typeColors[type] || generateColorForType(type);
}
