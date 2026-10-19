<script setup>
import { onBeforeUnmount, onMounted, ref, watch, computed } from "vue";
import ForceGraph from "force-graph";
import { useGraphStore } from "@/stores/graph";
import { kindColor, kindLabel, SURFACE, HIGHLIGHT } from "@/constants/graphTheme";

const emit = defineEmits(["node-click", "fps"]);

const container = ref(null);
const store = useGraphStore();

let graph = null;
let ro = null;
let frameTimer = null;
let frameCount = 0;
let lastFpsTs = 0;
let pulseTimer = 0;
let pulseNodeId = null;

const surface = computed(() => SURFACE[store.theme]);
const highlight = computed(() => HIGHLIGHT[store.theme]);

const adjacencyKey = computed(() => store.adjacency); // for watch

function buildGraphData() {
  const { nodes, edges } = store.filteredGraph;
  // 2D 用自然力导向自由排布,不做圈层钉位
  return {
    nodes: nodes.map((n) => ({ ...n })),
    links: edges.map((e) => ({ ...e })),
  };
}

function nodeRadius(node) {
  const d = node.degree || 0;
  return 3 + Math.sqrt(d) * 1.6;
}

function isPath(id) {
  return store.pathNodes.includes(id);
}
function isHovered(id) {
  return store.hoveredNodeId === id;
}
function isSelected(id) {
  return store.selectedNodeId === id;
}
function neighborSet() {
  if (!store.hoveredNodeId) return null;
  const set = store.adjacency.get(store.hoveredNodeId);
  return set || null;
}

function build() {
  if (!container.value || graph) return;
  const el = container.value;
  graph = ForceGraph()(el)
    .backgroundColor(surface.value.canvas)
    .graphData(buildGraphData())
    .nodeId("id")
    .nodeRelSize(1)
    .linkSource("source")
    .linkTarget("target")
    .cooldownTicks(200)
    .d3AlphaDecay(0.018)
    .d3VelocityDecay(0.32)
    .linkColor(() => surface.value.link)
    .linkWidth(0.6)
    .linkDirectionalArrowLength(0)
    .nodeCanvasObjectMode(() => "replace")
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint((node, color, ctx) => {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(node.x, node.y, nodeRadius(node) + 4, 0, 2 * Math.PI, false);
      ctx.fill();
    })
    .linkCanvasObjectMode(() => "replace")
    .linkCanvasObject(drawLink)
    .onNodeHover((node) => {
      store.hover(node ? node.id : null);
      el.style.cursor = node ? "pointer" : "grab";
    })
    .onNodeClick((node) => {
      emit("node-click", node);
    })
    .onNodeDragEnd((node) => {
      // 释放固定 → 让物理引擎重排：双击节点解锁
      node.fx = node.x;
      node.fy = node.y;
    });

  // 自然力导向:拉开节点距离的疏松布局
  const charge = graph.d3Force("charge");
  if (charge) charge.strength(-420).distanceMax(2400);
  const link = graph.d3Force("link");
  if (link) link.distance((l) => 90 + (1 - (l.weight || 0.5)) * 110).strength(0.12);
  const center = graph.d3Force("center");
  if (center) center.strength(0.05);
  // 碰撞防重叠
  import("d3-force-3d").then(({ forceCollide }) => {
    if (!graph) return;
    graph.d3Force("collide", forceCollide((n) => nodeRadius(n) + 12).strength(0.9));
    graph.d3ReheatSimulation();
  });
  graph.cooldownTicks(200);

  // 初始 zoom & 自适应
  setTimeout(() => graph?.zoomToFit(400, 100), 1200);

  resize();
  window.addEventListener("resize", resize);
  // 容器挂载初期宽高可能为 0(异步组件 + flex 布局),用 ResizeObserver
  // 在真实尺寸到位时补一次 resize,避免必须手动刷新才显示
  ro = new ResizeObserver(() => resize());
  ro.observe(el);
  startFpsLoop();
}

function drawNode(node, ctx, globalScale) {
  const r = nodeRadius(node);
  const c = kindColor(node.kind || "entity", store.theme);
  const hov = store.hoveredNodeId;
  const ns = neighborSet();
  const isNeighbor = hov && ns && ns.has(node.id);
  const isHov = isHovered(node.id);
  const isSel = isSelected(node.id);
  const inPath = isPath(node.id);

  let alpha = 1;
  if (hov && !isHov && !isNeighbor) alpha = 0.18;

  // 选中外圈
  if (isSel || inPath) {
    ctx.beginPath();
    ctx.arc(node.x, node.y, r + 3, 0, 2 * Math.PI, false);
    ctx.strokeStyle = inPath ? highlight.value.node : surface.value.label;
    ctx.lineWidth = 1.5 / globalScale;
    ctx.globalAlpha = alpha;
    ctx.stroke();
  }

  // hover halo
  if (isHov) {
    ctx.beginPath();
    ctx.arc(node.x, node.y, r + 2, 0, 2 * Math.PI, false);
    ctx.fillStyle = c;
    ctx.globalAlpha = 0.18;
    ctx.fill();
  }

  // 主体
  ctx.beginPath();
  ctx.arc(node.x, node.y, r, 0, 2 * Math.PI, false);
  ctx.fillStyle = c;
  ctx.globalAlpha = alpha;
  ctx.fill();

  // 描边
  ctx.lineWidth = 0.8 / globalScale;
  ctx.strokeStyle = surface.value.canvas;
  ctx.stroke();

  // 搜索闪烁
  if (pulseNodeId === node.id) {
    const t = (performance.now() - pulseTimer) / 800;
    if (t < 1) {
      const p = (1 - t);
      ctx.beginPath();
      ctx.arc(node.x, node.y, r + 12 * t + 4, 0, 2 * Math.PI, false);
      ctx.strokeStyle = highlight.value.node;
      ctx.globalAlpha = p * 0.8;
      ctx.lineWidth = 2 / globalScale;
      ctx.stroke();
    } else {
      pulseNodeId = null;
    }
  }

  // 标签 LOD：zoom 大于 1.2 或 hover/selected/path 时显示
  const showLabel =
    store.showLabels &&
    (globalScale > 1.2 || isHov || isSel || inPath || isNeighbor);
  if (showLabel) {
    // 同名 source/topic/entity 节点(如「工作研究」三种 kind 各一个)标签会重名,
    // 加「实体/主题/素材」前缀消歧,避免图上分不清谁是谁。
    const base = node.title || node.id;
    const label = `[${kindLabel(node.kind || "entity")}] ${base}`;
    const fontSize = Math.max(10 / globalScale, 1.5);
    ctx.font = `${fontSize}px Inter, "Noto Sans SC", system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillStyle = isHov || isSel || inPath ? surface.value.label : surface.value.labelMuted;
    ctx.globalAlpha = alpha;
    ctx.fillText(label, node.x, node.y + r + 2);
  }
  ctx.globalAlpha = 1;
}

function drawLink(link, ctx, globalScale) {
  const sId = typeof link.source === "object" ? link.source.id : link.source;
  const tId = typeof link.target === "object" ? link.target.id : link.target;
  const hov = store.hoveredNodeId;
  const ns = neighborSet();
  const isHovEdge = hov && (sId === hov || tId === hov);

  // 路径高亮
  const path = store.pathNodes;
  const inPath =
    path.length >= 2 &&
    path.includes(sId) &&
    path.includes(tId) &&
    Math.abs(path.indexOf(sId) - path.indexOf(tId)) === 1;

  let color, w;
  if (inPath) {
    color = highlight.value.link;
    w = 1.6 / globalScale;
  } else if (isHovEdge) {
    color = highlight.value.neighbor;
    w = 1.2 / globalScale;
  } else if (hov) {
    color = surface.value.linkDim;
    w = 0.4 / globalScale;
  } else {
    color = surface.value.link;
    w = 0.4 / globalScale;
  }

  const sx = link.source.x, sy = link.source.y;
  const tx = link.target.x, ty = link.target.y;
  if (sx == null || tx == null) return;
  ctx.beginPath();
  ctx.moveTo(sx, sy);
  ctx.lineTo(tx, ty);
  ctx.strokeStyle = color;
  ctx.lineWidth = w;
  ctx.stroke();
}

function resize() {
  if (!graph || !container.value) return;
  const w = container.value.clientWidth;
  const h = container.value.clientHeight;
  if (!w || !h) return;
  const wasZero = graph.width() === 0 || graph.height() === 0;
  graph.width(w);
  graph.height(h);
  // 从 0 尺寸首次拿到真实宽高 → 补一次自适应缩放
  if (wasZero) setTimeout(() => graph?.zoomToFit(400, 80), 300);
}

function startFpsLoop() {
  let prev = performance.now();
  const tick = (now) => {
    frameCount++;
    if (now - lastFpsTs > 1000) {
      emit("fps", frameCount);
      frameCount = 0;
      lastFpsTs = now;
    }
    prev = now;
    frameTimer = requestAnimationFrame(tick);
  };
  lastFpsTs = performance.now();
  frameTimer = requestAnimationFrame(tick);
}

function stopFpsLoop() {
  if (frameTimer) cancelAnimationFrame(frameTimer);
  frameTimer = null;
}

/* 数据变化 → 重新喂；主题/showLabels 不动数据 */
watch(
  () => [store.filteredGraph],
  () => {
    if (!graph) return;
    graph.graphData(buildGraphData());
  },
  { deep: true }
);

watch(
  () => store.theme,
  () => {
    if (!graph) return;
    graph.backgroundColor(surface.value.canvas);
    // 强制重绘
    graph.nodeCanvasObject(drawNode);
    graph.linkCanvasObject(drawLink);
  }
);

/* hover 变化触发重绘 */
watch(
  () => [store.hoveredNodeId, store.selectedNodeId, store.showLabels, store.pathNodes],
  () => {
    if (!graph) return;
    graph.nodeCanvasObject(drawNode);
    graph.linkCanvasObject(drawLink);
  }
);

/* 搜索内容变化 → 闪烁第一个匹配 */
watch(
  () => store.search,
  (q) => {
    if (!q) return;
    const ids = store.visibleNodeIds;
    const target = [...ids][0];
    if (target) {
      pulseNodeId = target;
      pulseTimer = performance.now();
      focus(target);
    }
  }
);

defineExpose({
  focus(id) {
    if (!graph) return;
    const node = graph.graphData().nodes.find((n) => n.id === id);
    if (!node) return;
    pulseNodeId = id;
    pulseTimer = performance.now();
    graph.centerAt(node.x, node.y, 600);
    graph.zoom(2, 600);
  },
});

onMounted(build);
onBeforeUnmount(() => {
  stopFpsLoop();
  window.removeEventListener("resize", resize);
  if (ro) { ro.disconnect(); ro = null; }
  if (graph) graph._destructor?.();
  graph = null;
});
</script>

<template>
  <div ref="container" class="graph-2d-container" />
</template>

<style scoped>
.graph-2d-container {
  width: 100%;
  height: 100%;
  cursor: grab;
}
.graph-2d-container:active { cursor: grabbing; }
</style>
