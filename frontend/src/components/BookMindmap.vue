<script setup>
// 书籍思维导图:书 → 章节 → 主题 → 实体 四层可折叠横向树(ECharts tree)。
// 单击非叶节点 = 展开/折叠;单击实体叶子 = 打开实体页;双击任意节点 = 打开对应 wiki 页。
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import * as echarts from "echarts/core";
import { TreeChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([TreeChart, TooltipComponent, CanvasRenderer]);

const props = defineProps({
  book: { type: String, required: true },
  // 后端 /wiki/books/{book}/tree 的 chapters 数组
  chapters: { type: Array, default: () => [] },
});

const router = useRouter();
const chartEl = ref(null);
const wrapEl = ref(null);
// 展开档位:1=只看章(默认,点章节按需展开) / 2=展开到主题 / -1=全部展开
const depth = ref(1);
let chart = null;
let resizeObserver = null;

// 统计可见叶子数,用于动态撑高画布:节点一多(全部展开 589+ 实体)固定高度必然重叠,
// 画布高度 = 叶子数 × 行高,外层滚动查看,保证任何档位下标签都不挤。
function visibleLeafCount() {
  const chapters = props.chapters || [];
  if (depth.value === 1) return chapters.length;
  let topics = 0;
  let entities = 0;
  for (const c of chapters) {
    const ts = c.topics || [];
    topics += ts.length + ((c.orphan_entities || []).length ? 1 : 0);
    for (const t of ts) entities += (t.entities || []).length || 1;
    entities += (c.orphan_entities || []).length;
  }
  return depth.value === 2 ? topics : entities;
}

function applyChartHeight() {
  if (!chartEl.value) return;
  const wrapH = wrapEl.value ? wrapEl.value.clientHeight : 480;
  // 行高:章 34px / 主题 26px / 实体 20px,不小于容器可视高
  const rowH = depth.value === 1 ? 34 : depth.value === 2 ? 26 : 20;
  const h = Math.max(wrapH, visibleLeafCount() * rowH + 40);
  chartEl.value.style.height = `${h}px`;
}

const KIND_STYLE = {
  book: { color: "#f97316", symbolSize: 18 },
  source: { color: "#ea580c", symbolSize: 13 },
  topic: { color: "#2563eb", symbolSize: 9 },
  entity: { color: "#94a3b8", symbolSize: 5 },
};

const KIND_NAME = { book: "书", source: "章节", topic: "主题", entity: "实体" };

// source slug 带日期前缀(2026-06-07-第五章-xxx),显示时剥掉
function cleanTitle(s) {
  return String(s || "").replace(/^\d{4}-\d{2}-\d{2}-/, "");
}

function buildTreeData() {
  const chapterNodes = (props.chapters || []).map((c, idx) => {
    const topicNodes = (c.topics || []).map((t) => ({
      name: t.title,
      slug: t.slug,
      kind: "topic",
      itemStyle: { color: KIND_STYLE.topic.color },
      symbolSize: KIND_STYLE.topic.symbolSize,
      label: { color: "#1d4ed8", fontSize: 12 },
      children: (t.entities || []).map((e) => ({
        name: e.title,
        slug: e.slug,
        kind: "entity",
        exists: e.exists !== false,
        itemStyle: { color: KIND_STYLE.entity.color },
        symbolSize: KIND_STYLE.entity.symbolSize,
        label: { color: "#64748b", fontSize: 11 },
      })),
    }));
    if ((c.orphan_entities || []).length) {
      topicNodes.push({
        name: "(未归类实体)",
        kind: "orphan",
        itemStyle: { color: "#cbd5e1" },
        symbolSize: 8,
        label: { color: "#94a3b8", fontSize: 12, fontStyle: "italic" },
        children: c.orphan_entities.map((e) => ({
          name: e.title,
          slug: e.slug,
          kind: "entity",
          exists: true,
          itemStyle: { color: KIND_STYLE.entity.color },
          symbolSize: KIND_STYLE.entity.symbolSize,
          label: { color: "#64748b", fontSize: 11 },
        })),
      });
    }
    return {
      name: `${idx + 1}. ${cleanTitle(c.title)}`,
      slug: c.slug,
      kind: "source",
      itemStyle: { color: KIND_STYLE.source.color },
      symbolSize: KIND_STYLE.source.symbolSize,
      label: {
        color: "#0f172a",
        fontSize: 13.5,
        fontWeight: 600,
        fontFamily: '"Noto Serif SC", "Songti SC", serif',
      },
      children: topicNodes,
    };
  });

  return {
    name: props.book,
    kind: "book",
    itemStyle: { color: KIND_STYLE.book.color },
    symbolSize: KIND_STYLE.book.symbolSize,
    label: {
      // 根节点标签放节点下方、向右延伸,避免被容器左缘裁切
      position: "bottom",
      align: "left",
      distance: 10,
      color: "#0f172a",
      fontSize: 16,
      fontWeight: 700,
      fontFamily: '"Noto Serif SC", "Songti SC", serif',
    },
    children: chapterNodes,
  };
}

function option() {
  return {
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      confine: true,
      formatter: (p) => {
        const d = p.data || {};
        const kind = KIND_NAME[d.kind] || "";
        const hint =
          d.kind === "entity"
            ? "单击打开页面"
            : d.kind === "orphan"
              ? "单击展开/折叠"
              : "单击展开/折叠 · 双击打开页面";
        return `<b>${p.name}</b>${kind ? ` <span style="color:#94a3b8">${kind}</span>` : ""}<br/><span style="color:#94a3b8;font-size:11px">${hint}</span>`;
      },
    },
    series: [
      {
        type: "tree",
        data: [buildTreeData()],
        orient: "LR",
        left: "2%",
        right: "22%",
        top: 16,
        bottom: 16,
        edgeShape: "curve",
        // 只允许拖拽平移;不占用滚轮,让滚轮自然滚动长画布
        roam: "move",
        initialTreeDepth: depth.value,
        expandAndCollapse: true,
        animationDuration: 300,
        animationDurationUpdate: 400,
        lineStyle: { color: "#e2e8f0", width: 1.2, curveness: 0.5 },
        label: {
          position: "left",
          verticalAlign: "middle",
          align: "right",
          distance: 6,
          // 超长标题截断,悬停 tooltip 看全称
          formatter: (p) =>
            p.name.length > 24 ? p.name.slice(0, 23) + "…" : p.name,
        },
        leaves: {
          label: {
            position: "right",
            verticalAlign: "middle",
            align: "left",
            distance: 6,
          },
        },
        emphasis: { focus: "descendant" },
      },
    ],
  };
}

function gotoNode(data) {
  if (!data?.slug) return;
  if (data.kind === "entity" && data.exists === false) return;
  router.push({
    path: `/kb/wiki/${encodeURIComponent(data.slug)}`,
    query: data.kind && data.kind !== "book" ? { kind: data.kind } : {},
  });
}

function render() {
  if (!chart) return;
  applyChartHeight();
  chart.resize();
  chart.setOption(option(), { notMerge: true });
}

function setDepth(d) {
  depth.value = d;
  render();
  // 切档后回到顶部,避免停留在长画布中段不知所措
  if (wrapEl.value) wrapEl.value.scrollTop = 0;
}

onMounted(() => {
  chart = echarts.init(chartEl.value);
  render();
  chart.on("click", (p) => {
    // 实体叶子没有子节点,单击直接跳页;其余节点单击交给内置展开/折叠
    if (p.data?.kind === "entity") gotoNode(p.data);
  });
  chart.on("dblclick", (p) => {
    if (p.data?.kind && p.data.kind !== "entity" && p.data.kind !== "orphan") {
      gotoNode(p.data);
    }
  });
  resizeObserver = new ResizeObserver(() => chart && chart.resize());
  resizeObserver.observe(chartEl.value);
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  chart?.dispose();
  chart = null;
});

watch(() => props.chapters, render, { deep: false });
</script>

<template>
  <div class="mindmap-wrap">
    <div class="mm-toolbar">
      <span class="mm-hint">滚轮上下浏览 · 单击展开/折叠 · 双击打开页面</span>
      <div class="mm-depth">
        <button class="mm-btn" :class="{ active: depth === 1 }" @click="setDepth(1)">章节</button>
        <button class="mm-btn" :class="{ active: depth === 2 }" @click="setDepth(2)">主题</button>
        <button class="mm-btn" :class="{ active: depth === -1 }" @click="setDepth(-1)">全部</button>
      </div>
    </div>
    <div ref="wrapEl" class="mm-scroll">
      <div ref="chartEl" class="mm-chart" />
    </div>
  </div>
</template>

<style scoped>
.mindmap-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.mm-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid #e2e8f0;
}
.mm-hint {
  font-size: 12px;
  color: #94a3b8;
}
.mm-depth {
  margin-left: auto;
  display: inline-flex;
  border: 1px solid #e2e8f0;
  border-radius: 5px;
  overflow: hidden;
}
.mm-btn {
  border: 0;
  background: #fff;
  color: #64748b;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
}
.mm-btn.active {
  background: #f97316;
  color: #fff;
}
.mm-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}
.mm-chart {
  width: 100%;
  height: 100%;
}
</style>
