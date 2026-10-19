<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import ForceGraph from "force-graph";
import { kindColor } from "@/constants/graphTheme";

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  edges: { type: Array, default: () => [] },
  height: { type: Number, default: 240 },
});
const emit = defineEmits(["node-click"]);

const container = ref(null);
let graph = null;
let ro = null;

// 语义边统一深灰实线;关系含义只靠边中点的文字标签表达(支持任意扩展类型)。
// 包含/生成边用浅灰虚线。
const SEMANTIC_EDGE_COLOR = "rgba(71,85,105,0.7)";
function edgeColor(l) {
  if (l.edge_kind === "containment") return "rgba(148,163,184,0.45)";
  return SEMANTIC_EDGE_COLOR;
}

function build() {
  if (!container.value || graph) return;
  const el = container.value;
  graph = ForceGraph()(el)
    .backgroundColor("#ffffff")
    .width(el.clientWidth || 320)
    .height(props.height)
    .nodeId("id")
    .nodeRelSize(1)
    .linkColor((l) => edgeColor(l))
    .linkWidth((l) => (l.edge_kind === "containment" ? 0.8 : 1 + (l.weight || 0) * 1.5))
    .linkLineDash((l) => (l.edge_kind === "containment" ? [3, 3] : null))
    .linkDirectionalArrowLength(5)
    .linkDirectionalArrowRelPos(1)
    .linkDirectionalArrowColor((l) => edgeColor(l))
    .nodeCanvasObject((node, ctx, scale) => {
      const r = node.is_center ? 6 : 4;
      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = kindColor(node.kind || "entity", "light");
      ctx.fill();
      if (node.is_center) {
        ctx.lineWidth = 2 / scale;
        ctx.strokeStyle = "#f97316";
        ctx.stroke();
      }
      // 标签:中心常显,其它在放大后显示
      if (node.is_center || scale > 1.2) {
        const label = node.title || node.slug || "";
        const fontSize = Math.max(10 / scale, 3);
        ctx.font = `${fontSize}px "Noto Serif SC", serif`;
        ctx.fillStyle = node.is_center ? "#1f2937" : "#64748b";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(label, node.x, node.y + r + 1);
      }
    })
    .linkCanvasObjectMode(() => "after")
    .linkCanvasObject((link, ctx, scale) => {
      // 语义边在起点端补画反向箭头(「相关」是双向);关系类型文字标签暂时隐藏。
      if (link.edge_kind !== "semantic") return;
      const s = link.source, t = link.target;
      if (!s || !t || typeof s !== "object" || typeof t !== "object") return;
      const rawType = link.rel_type;
      const isRelated = !rawType || rawType === "相关" || rawType === "related";
      // 「相关」是双向关系:在起点端补画一个反向箭头(target 端的正向箭头由内置绘制)
      if (isRelated) {
        const dx = t.x - s.x, dy = t.y - s.y;
        const len = Math.hypot(dx, dy) || 1;
        const ux = dx / len, uy = dy / len;
        const r = s.is_center ? 6 : 4;        // 避开节点半径
        const tipx = s.x + ux * (r + 1);
        const tipy = s.y + uy * (r + 1);
        const a = 5;                           // 箭头长度,与内置 arrowLength 对齐
        const ang = Math.atan2(uy, ux);
        const spread = 0.5;
        ctx.beginPath();
        ctx.moveTo(tipx, tipy);
        ctx.lineTo(tipx + a * Math.cos(ang + Math.PI - spread), tipy + a * Math.sin(ang + Math.PI - spread));
        ctx.lineTo(tipx + a * Math.cos(ang + Math.PI + spread), tipy + a * Math.sin(ang + Math.PI + spread));
        ctx.closePath();
        ctx.fillStyle = edgeColor(link);
        ctx.fill();
      }
    })
    .onNodeClick((node) => emit("node-click", node));
  setData();
}

function setData() {
  if (!graph) return;
  graph.graphData({
    nodes: props.nodes.map((n) => ({ ...n })),
    links: props.edges.map((e) => ({ ...e, source: e.source, target: e.target })),
  });
}

watch(() => [props.nodes, props.edges], () => setData(), { deep: true });

onMounted(() => {
  build();
  ro = new ResizeObserver(() => {
    if (graph && container.value) graph.width(container.value.clientWidth || 320);
  });
  if (container.value) ro.observe(container.value);
});

onBeforeUnmount(() => {
  if (ro) ro.disconnect();
  if (graph) {
    graph._destructor && graph._destructor();
    graph = null;
  }
});
</script>

<template>
  <div ref="container" class="mini-graph" :style="{ height: height + 'px' }" />
</template>

<style scoped>
.mini-graph {
  width: 100%;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}
</style>
