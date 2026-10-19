<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch, defineAsyncComponent } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import {
  Search,
  Refresh,
  View,
  Hide,
  Sunny,
  Moon,
  ArrowLeft,
  ArrowRight,
  Aim,
  Right,
  Edit,
} from "@element-plus/icons-vue";
import { useGraphStore } from "@/stores/graph";
import { kindColor, kindLabel, KIND_RING, INSIGHT_LABELS } from "@/constants/graphTheme";

const Graph2D = defineAsyncComponent(() => import("@/components/Graph2D.vue"));
const Graph3D = defineAsyncComponent(() => import("@/components/Graph3D.vue"));

const route = useRoute();
const router = useRouter();
const store = useGraphStore();

const canvasRef = ref(null);
const leftCollapsed = ref(false);
const detailDrawer = ref(false);
const fps = ref(0);
const fading = ref(false);

// 路由 ?path=a,b,c → 高亮节点
watch(
  () => route.query.path,
  (raw) => {
    if (!raw) return store.setPath([]);
    store.setPath(String(raw).split(",").filter(Boolean));
  },
  { immediate: true }
);

// 路由 ?mode=2d|3d
watch(
  () => route.query.mode,
  (m) => {
    if (m === "2d" || m === "3d") store.setMode(m);
  },
  { immediate: true }
);

watch(
  () => store.mode,
  (m) => {
    if (route.query.mode !== m) {
      router.replace({ query: { ...route.query, mode: m } });
    }
  }
);

const selectedNode = computed(() =>
  store.selectedNodeId ? store.nodeIndex.get(store.selectedNodeId) : null
);

const neighborList = computed(() => {
  if (!store.selectedNodeId) return [];
  return store
    .neighborsOf(store.selectedNodeId)
    .map((id) => store.nodeIndex.get(id))
    .filter(Boolean)
    .sort((a, b) => (b.degree || 0) - (a.degree || 0))
    .slice(0, 30);
});

/* 把后端 insight 的 title / detail 美化为纯中文，去掉 wikilink/参数痕迹 */
function prettifyInsight(it) {
  const k = it.kind;
  const slugs = it.related_slugs || [];
  const titleOf = (slug) => store.nodeIndex.get(slug)?.title || slug;
  const detail = it.detail || "";
  let title = it.title || "";
  let cleanDetail = detail;

  // 去掉 [[xxx]] 标记，换成 «标题»
  title = title.replace(/\[\[([^\]]+)\]\]/g, (_, s) => `«${titleOf(s.trim())}»`);

  if (k === "surprising_link") {
    // 原："weight=0.62 跨越社区 0 与 1"
    const m = detail.match(/weight=([\d.]+).*?社区\s*(\d+)\s*与\s*(\d+)/);
    if (m) cleanDetail = `权重 ${m[1]} · 跨越社区 #${m[2]} 与 #${m[3]}`;
    if (slugs.length === 2) {
      title = `«${titleOf(slugs[0])}»  ↔  «${titleOf(slugs[1])}»`;
    }
  } else if (k === "bridge_node") {
    // 原："桥节点 [[X]] 连接 N 个社区" / detail: "communities=[1, 2, 3]"
    if (slugs.length) title = `«${titleOf(slugs[0])}»`;
    const m = detail.match(/communities=\[([^\]]+)\]/);
    if (m) {
      const ids = m[1].split(",").map((x) => x.trim()).filter(Boolean);
      cleanDetail = `连接 ${ids.length} 个社区：${ids.map((i) => "#" + i).join(" / ")}`;
    }
  } else if (k === "knowledge_gap") {
    if (slugs.length) title = `«${titleOf(slugs[0])}»`;
    const m = detail.match(/degree=(\d+)/);
    if (m) cleanDetail = `度数仅 ${m[1]}，建议补充关联素材`;
  } else if (k === "sparse_community") {
    const m = it.title?.match(/#(\d+)\s*\(\s*(\d+)/);
    if (m) title = `社区 #${m[1]} · ${m[2]} 个节点`;
    const mm = detail.match(/members=\[([^\]]+)\]/);
    if (mm) {
      const items = mm[1]
        .split(",")
        .map((x) => x.replace(/['"\s]/g, ""))
        .filter(Boolean)
        .map(titleOf);
      cleanDetail = `成员：${items.join("、")}`;
    }
  }
  return { ...it, title, detail: cleanDetail };
}

const insightGroups = computed(() => {
  const groups = {
    surprising_link: [],
    bridge_node: [],
    knowledge_gap: [],
    sparse_community: [],
  };
  for (const raw of store.insights || []) {
    const it = prettifyInsight(raw);
    if (groups[it.kind]) groups[it.kind].push(it);
  }
  return groups;
});

const stats = computed(() => ({
  total: store.nodes.length,
  edges: store.edges.length,
  communities: Object.keys(store.communities || {}).length,
  visible: store.visibleNodeIds.size,
}));

async function init() {
  // 强制刷新:每次进图谱页都拉最新 graph-data.json
  // (wiki ingest / 编辑 / 删除 / crystallize 后端都会 fire-and-forget 重建,
  //  这里 force=true 保证用户感知到最新节点/边,无需手动点重建)
  await store.load(true);
  if (store.loadError) ElMessage.error("图谱加载失败：" + store.loadError);
}

async function onRebuild() {
  try {
    await store.rebuild();
    ElMessage.success("图谱已重建");
  } catch (e) {
    ElMessage.error("重建失败：" + (e?.response?.data?.detail || e.message));
  }
}

function switchMode(m) {
  if (m === store.mode) return;
  fading.value = true;
  setTimeout(() => {
    store.setMode(m);
    fading.value = false;
  }, 200);
}

function onNodeClick(node) {
  store.select(node.id);
  detailDrawer.value = true;
}

function onFocusNode(id) {
  canvasRef.value?.focus?.(id);
  store.select(id);
}

function gotoWiki(node) {
  if (!node) return;
  detailDrawer.value = false; // 先关抽屉,避免离开时 overlay 残留挡住交互
  // node.id 形如 `entity:工作研究`,wiki 路由用 slug 字段
  const slug = node.slug || node.id;
  router.push({
    path: `/kb/wiki/${encodeURIComponent(slug)}`,
    query: node.kind ? { kind: node.kind } : {},
  });
}

function gotoWikiEdit(node) {
  if (!node) return;
  detailDrawer.value = false;
  const slug = node.slug || node.id;
  router.push({
    path: `/kb/wiki/${encodeURIComponent(slug)}`,
    query: { edit: 1, ...(node.kind ? { kind: node.kind } : {}) },
  });
}

function fpsCallback(v) {
  fps.value = v;
}

onMounted(init);
onBeforeUnmount(() => {
  // 离开页面强制关抽屉,清掉可能残留的 overlay,避免回来后挡住画布交互
  detailDrawer.value = false;
});
</script>

<template>
  <div class="graph-page" :class="`theme-${store.theme}`">
    <!-- ============ 顶栏 ============ -->
    <header class="gx-topbar">
      <div class="gx-title-block">
        <h2 class="gx-title">知识图谱</h2>
        <span class="gx-sub">{{ stats.visible }} / {{ stats.total }} 节点 · {{ stats.edges }} 边 · {{ stats.communities }} 社区</span>
      </div>

      <div class="gx-tools">
        <el-input
          :model-value="store.search"
          @update:model-value="store.setSearch"
          :prefix-icon="Search"
          placeholder="搜索节点 / 标题"
          size="small"
          clearable
          class="gx-search"
        />

        <!-- 2D / 3D segmented -->
        <div class="gx-segment" role="tablist">
          <button
            class="gx-seg-btn"
            :class="{ active: store.mode === '2d' }"
            @click="switchMode('2d')"
          >2D</button>
          <button
            class="gx-seg-btn"
            :class="{ active: store.mode === '3d' }"
            @click="switchMode('3d')"
          >3D</button>
        </div>

        <el-tooltip :content="store.theme === 'dark' ? '切换浅色' : '切换深色'" placement="bottom">
          <button
            class="gx-icon-btn"
            @click="store.setTheme(store.theme === 'dark' ? 'light' : 'dark')"
          >
            <el-icon><Sunny v-if="store.theme === 'dark'" /><Moon v-else /></el-icon>
          </button>
        </el-tooltip>

        <el-tooltip :content="store.showLabels ? '隐藏标签' : '显示标签'" placement="bottom">
          <button class="gx-icon-btn" @click="store.setShowLabels(!store.showLabels)">
            <el-icon><View v-if="store.showLabels" /><Hide v-else /></el-icon>
          </button>
        </el-tooltip>

        <el-button
          size="small"
          :icon="Refresh"
          :loading="store.rebuilding"
          @click="onRebuild"
        >重建</el-button>
      </div>
    </header>

    <!-- ============ 主体三栏 ============ -->
    <div class="gx-body">
      <!-- 左面板 -->
      <aside class="gx-left" :class="{ collapsed: leftCollapsed }">
        <button
          class="gx-collapse-btn"
          @click="leftCollapsed = !leftCollapsed"
          :title="leftCollapsed ? '展开' : '收起'"
        >
          <el-icon><ArrowRight v-if="leftCollapsed" /><ArrowLeft v-else /></el-icon>
        </button>

        <div v-show="!leftCollapsed" class="gx-left-inner">
          <!-- 图例:常驻侧边,三类节点 + 圈层说明 -->
          <section class="gx-section gx-legend-block">
            <h4 class="gx-h">节点类型 · 由内向外</h4>
            <div class="gx-legend">
              <div
                v-for="k in store.kindList"
                :key="k"
                class="gx-legend-row"
              >
                <span class="gx-ring-idx">{{ (KIND_RING[k] ?? 0) + 1 }}</span>
                <span
                  class="gx-legend-dot"
                  :style="{ background: kindColor(k, store.theme) }"
                ></span>
                <span class="gx-legend-name">{{ kindLabel(k) }}</span>
                <span class="gx-legend-pos">{{ ["中心", "中圈", "外圈"][KIND_RING[k] ?? 0] }}</span>
              </div>
            </div>
            <ul class="gx-tips">
              <li>素材居中,主题、实体逐层向外辐射</li>
              <li>节点大小 与 度数 成正比</li>
              <li>双击节点：相机聚焦</li>
              <li>悬停节点：邻居高亮</li>
            </ul>
          </section>

          <!-- Insights -->
          <section class="gx-section gx-insights-block">
            <h4 class="gx-h">图谱洞察</h4>
            <div v-if="!store.insights?.length" class="gx-empty">
              暂无洞察，先点击右上角「重建」生成
            </div>

            <template v-else>
              <details v-if="insightGroups.surprising_link.length" open>
                <summary>
                  <span class="gx-ins-label">{{ INSIGHT_LABELS.surprising_link.title }}</span>
                  <span class="gx-ins-count">{{ insightGroups.surprising_link.length }}</span>
                </summary>
                <p class="gx-ins-desc">{{ INSIGHT_LABELS.surprising_link.desc }}</p>
                <ul class="gx-ins-list">
                  <li v-for="(it, i) in insightGroups.surprising_link" :key="'sl' + i">
                    <div class="gx-ins-title">{{ it.title }}</div>
                    <div class="gx-ins-detail">{{ it.detail }}</div>
                    <div class="gx-ins-actions">
                      <button
                        v-for="s in it.related_slugs"
                        :key="s"
                        class="gx-mini-chip"
                        @click="onFocusNode(s)"
                      >{{ store.nodeIndex.get(s)?.title || s }}</button>
                    </div>
                  </li>
                </ul>
              </details>

              <details v-if="insightGroups.bridge_node.length">
                <summary>
                  <span class="gx-ins-label">{{ INSIGHT_LABELS.bridge_node.title }}</span>
                  <span class="gx-ins-count">{{ insightGroups.bridge_node.length }}</span>
                </summary>
                <p class="gx-ins-desc">{{ INSIGHT_LABELS.bridge_node.desc }}</p>
                <ul class="gx-ins-list">
                  <li v-for="(it, i) in insightGroups.bridge_node" :key="'bn' + i">
                    <div class="gx-ins-title">{{ it.title }}</div>
                    <div class="gx-ins-detail">{{ it.detail }}</div>
                    <div class="gx-ins-actions">
                      <button
                        v-for="s in it.related_slugs"
                        :key="s"
                        class="gx-mini-chip"
                        @click="onFocusNode(s)"
                      >{{ store.nodeIndex.get(s)?.title || s }}</button>
                    </div>
                  </li>
                </ul>
              </details>

              <details v-if="insightGroups.knowledge_gap.length">
                <summary>
                  <span class="gx-ins-label">{{ INSIGHT_LABELS.knowledge_gap.title }}</span>
                  <span class="gx-ins-count">{{ insightGroups.knowledge_gap.length }}</span>
                </summary>
                <p class="gx-ins-desc">{{ INSIGHT_LABELS.knowledge_gap.desc }}</p>
                <ul class="gx-ins-list">
                  <li v-for="(it, i) in insightGroups.knowledge_gap" :key="'kg' + i">
                    <div class="gx-ins-title">{{ it.title }}</div>
                    <div class="gx-ins-detail">{{ it.detail }}</div>
                    <div class="gx-ins-actions">
                      <button
                        v-for="s in it.related_slugs"
                        :key="s"
                        class="gx-mini-chip"
                        @click="onFocusNode(s)"
                      >{{ store.nodeIndex.get(s)?.title || s }}</button>
                    </div>
                  </li>
                </ul>
              </details>

              <details v-if="insightGroups.sparse_community.length">
                <summary>
                  <span class="gx-ins-label">{{ INSIGHT_LABELS.sparse_community.title }}</span>
                  <span class="gx-ins-count">{{ insightGroups.sparse_community.length }}</span>
                </summary>
                <p class="gx-ins-desc">{{ INSIGHT_LABELS.sparse_community.desc }}</p>
                <ul class="gx-ins-list">
                  <li v-for="(it, i) in insightGroups.sparse_community" :key="'sc' + i">
                    <div class="gx-ins-title">{{ it.title }}</div>
                    <div class="gx-ins-detail">{{ it.detail }}</div>
                  </li>
                </ul>
              </details>
            </template>
          </section>
        </div>
      </aside>

      <!-- 画布 -->
      <main class="gx-canvas" :class="{ fading }">
        <div v-if="store.loading" class="gx-loading">
          <div class="gx-skeleton"></div>
          <span>正在加载图谱…</span>
        </div>
        <div v-else-if="store.loadError" class="gx-error">
          {{ store.loadError }}
          <el-button size="small" @click="store.load(true)">重试</el-button>
        </div>
        <div v-else-if="!store.nodes.length" class="gx-empty-canvas">
          <p>图谱为空。先到「素材管理」消化一些 wiki，再回来重建图谱。</p>
          <el-button size="small" type="primary" @click="onRebuild">立即重建</el-button>
        </div>
        <template v-else>
          <Graph2D
            v-if="store.mode === '2d'"
            ref="canvasRef"
            @node-click="onNodeClick"
            @fps="fpsCallback"
          />
          <Graph3D
            v-else
            ref="canvasRef"
            @node-click="onNodeClick"
            @fps="fpsCallback"
          />

          <!-- 画布常驻图例:橙=素材(中心) 蓝=主题(中圈) 绿=实体(外圈) -->
          <div class="gx-legend-float">
            <div
              v-for="k in store.kindList"
              :key="k"
              class="gx-legend-float-row"
            >
              <span
                class="gx-legend-dot"
                :style="{ background: kindColor(k, store.theme) }"
              ></span>
              <span class="gx-legend-float-name">{{ kindLabel(k) }}</span>
              <span class="gx-legend-float-pos">{{ ["中心", "中圈", "外圈"][KIND_RING[k] ?? 0] }}</span>
            </div>
          </div>
        </template>
      </main>
    </div>

    <!-- 底栏 -->
    <footer class="gx-footer">
      <span class="gx-mono">{{ stats.visible }} / {{ stats.total }} 节点</span>
      <span class="gx-sep">·</span>
      <span class="gx-mono">{{ stats.edges }} 边</span>
      <span class="gx-sep">·</span>
      <span class="gx-mono">{{ stats.communities }} 社区</span>
      <span class="gx-sep">·</span>
      <span class="gx-mono">{{ store.mode === "2d" ? "2D 平面" : "3D 星球" }}</span>
      <span class="gx-sep">·</span>
      <span class="gx-mono">FPS {{ fps || "—" }}</span>
      <span v-if="store.generatedAt" class="gx-gen">
        · 生成于 {{ new Date(store.generatedAt).toLocaleString() }}
      </span>
    </footer>

    <!-- 节点详情抽屉:overlay 设为穿透(pointer-events:none),
         否则即便 modal=false,全屏 overlay 仍会吃掉画布上的拖拽事件,3D 转不动。 -->
    <el-drawer
      v-model="detailDrawer"
      :title="selectedNode?.title || selectedNode?.id || '节点'"
      direction="rtl"
      size="380px"
      :with-header="true"
      :modal="false"
      :close-on-click-modal="false"
      :lock-scroll="false"
      modal-class="gx-drawer-overlay"
    >
      <div v-if="selectedNode" class="gx-detail">
        <div class="gx-detail-meta">
          <span
            class="gx-pill"
            :style="{
              background: kindColor(selectedNode.kind || 'entity', store.theme),
            }"
          ></span>
          <div class="gx-pill-text">
            <div class="gx-pill-id">{{ selectedNode.slug || selectedNode.id }}</div>
            <div class="gx-pill-kind">
              {{ kindLabel(selectedNode.kind) }}
              <span v-if="selectedNode.community_id != null">· 社区 #{{ selectedNode.community_id }}</span>
              <span v-if="selectedNode.degree != null">· 度数 {{ selectedNode.degree }}</span>
            </div>
          </div>
        </div>

        <div v-if="selectedNode.tags?.length" class="gx-detail-tags">
          <span v-for="t in selectedNode.tags" :key="t" class="gx-tag">#{{ t }}</span>
        </div>

        <div class="gx-detail-actions">
          <el-button size="small" :icon="Aim" @click="onFocusNode(selectedNode.id)">在图中聚焦</el-button>
          <el-button size="small" type="primary" :icon="Right" @click="gotoWiki(selectedNode)">前往 Wiki</el-button>
          <el-tooltip content="跳到该节点的 wiki 页并直接进入编辑模式" placement="top">
            <el-button size="small" :icon="Edit" @click="gotoWikiEdit(selectedNode)">调整</el-button>
          </el-tooltip>
        </div>

        <div class="gx-neighbors">
          <h4 class="gx-h">邻居 · {{ neighborList.length }}</h4>
          <div v-if="!neighborList.length" class="gx-empty">该节点没有邻居</div>
          <ul v-else class="gx-neighbor-list">
            <li v-for="n in neighborList" :key="n.id">
              <button class="gx-neighbor-item" @click="onFocusNode(n.id)">
                <span
                  class="gx-comm-dot small"
                  :style="{
                    background: kindColor(n.kind || 'entity', store.theme),
                  }"
                ></span>
                <span class="gx-neighbor-title">{{ n.title || n.id }}</span>
                <span class="gx-neighbor-meta">{{ kindLabel(n.kind) }} · {{ n.degree || 0 }}</span>
              </button>
            </li>
          </ul>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<!-- 非 scoped:抽屉 overlay 挂在 body 上,scoped 选择器够不着。
     让全屏 overlay 穿透鼠标事件,但抽屉面板本身保持可交互。 -->
<style lang="scss">
.gx-drawer-overlay {
  pointer-events: none !important;
}
.gx-drawer-overlay .el-drawer {
  pointer-events: auto;
}
</style>

<style lang="scss" scoped>
/* ===== 主题 token（页面级，避免污染全局） ===== */
.graph-page {
  --gx-bg: #fafaf7;
  --gx-surface: #ffffff;
  --gx-surface-2: #f4f5f1;
  --gx-text: #1f2937;
  --gx-text-2: #64748b;
  --gx-border: #e5e7eb;
  --gx-border-strong: #cbd5e1;
  --gx-primary: #1e3a5f;
  --gx-accent: #a87b3d;
  --gx-danger: #dc2626;
  --gx-mono-font: "JetBrains Mono", ui-monospace, "SFMono-Regular", Consolas, monospace;
  --gx-serif-font: "Noto Serif SC", "Songti SC", "STSong", serif;

  display: flex;
  flex-direction: column;
  height: calc(100vh - 80px);
  background: var(--gx-bg);
  color: var(--gx-text);
  border-radius: 8px;
  border: 1px solid var(--gx-border);
  overflow: hidden;
}
.graph-page.theme-dark {
  --gx-bg: #0a0e14;
  --gx-surface: #0f1419;
  --gx-surface-2: #161b23;
  --gx-text: #e5e7eb;
  --gx-text-2: #94a3b8;
  --gx-border: #2d3540;
  --gx-border-strong: #3a4250;
  --gx-primary: #7aa2c4;
  --gx-accent: #d4a574;
  --gx-danger: #f87171;
}

/* ===== 顶栏 ===== */
.gx-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  border-bottom: 1px solid var(--gx-border);
  background: var(--gx-surface);
  gap: 16px;
  flex-wrap: wrap;
}
.gx-title-block { min-width: 220px; }
.gx-title {
  margin: 0;
  font-family: var(--gx-serif-font);
  font-size: 18px;
  font-weight: 600;
  color: var(--gx-text);
  letter-spacing: -0.01em;
}
.gx-sub {
  font-size: 12px;
  color: var(--gx-text-2);
  font-family: var(--gx-mono-font);
}
.gx-tools {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.gx-search { width: 220px; }

/* segmented control */
.gx-segment {
  display: inline-flex;
  border: 1px solid var(--gx-border);
  border-radius: 6px;
  overflow: hidden;
  background: var(--gx-surface);
}
.gx-seg-btn {
  border: 0;
  background: transparent;
  color: var(--gx-text-2);
  padding: 6px 14px;
  font-size: 12px;
  font-family: var(--gx-mono-font);
  cursor: pointer;
  transition: all 0.15s;
  &.active {
    background: var(--gx-primary);
    color: #fff;
  }
  &:not(.active):hover { background: var(--gx-surface-2); }
}
.gx-segment.small .gx-seg-btn { padding: 5px 10px; font-size: 11px; font-family: inherit; }

.gx-icon-btn {
  width: 32px; height: 32px;
  border: 1px solid var(--gx-border);
  border-radius: 6px;
  background: var(--gx-surface);
  color: var(--gx-text-2);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
  &:hover {
    border-color: var(--gx-border-strong);
    color: var(--gx-text);
  }
}

/* ===== body 三栏 ===== */
.gx-body {
  flex: 1;
  display: flex;
  min-height: 0;
}
.gx-left {
  width: 260px;
  border-right: 1px solid var(--gx-border);
  background: var(--gx-surface);
  position: relative;
  flex-shrink: 0;
  transition: width 0.2s;
  &.collapsed { width: 32px; }
}
.gx-collapse-btn {
  position: absolute;
  top: 8px; right: -1px;
  width: 24px; height: 24px;
  background: var(--gx-surface);
  border: 1px solid var(--gx-border);
  border-radius: 0 4px 4px 0;
  cursor: pointer;
  z-index: 2;
  color: var(--gx-text-2);
  display: flex;
  align-items: center;
  justify-content: center;
  &:hover { color: var(--gx-text); }
}
.gx-left-inner {
  height: 100%;
  overflow-y: auto;
  padding: 12px 12px 16px;
}

.gx-tabs {
  display: flex;
  border: 1px solid var(--gx-border);
  border-radius: 6px;
  overflow: hidden;
  margin-bottom: 14px;
}
.gx-tab {
  flex: 1;
  border: 0;
  background: transparent;
  color: var(--gx-text-2);
  padding: 6px 0;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
  border-right: 1px solid var(--gx-border);
  &:last-child { border-right: 0; }
  &.active {
    background: var(--gx-surface-2);
    color: var(--gx-text);
    font-weight: 500;
  }
}

.gx-section { display: flex; flex-direction: column; gap: 14px; }
.gx-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  &.row {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
  }
  > label {
    font-size: 11px;
    color: var(--gx-text-2);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 500;
  }
}
.gx-num { color: var(--gx-text); font-family: var(--gx-mono-font); }

.gx-chip-row { display: flex; flex-wrap: wrap; gap: 4px; }
.gx-chip {
  --chip-color: var(--gx-text-2);
  border: 1px solid var(--gx-border);
  background: var(--gx-surface);
  color: var(--gx-text-2);
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 11px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: all 0.15s;
  .gx-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--chip-color);
  }
  &.active {
    color: var(--gx-text);
    border-color: var(--chip-color);
    background: color-mix(in srgb, var(--chip-color) 8%, transparent);
  }
  &:not(.active) .gx-dot { opacity: 0.4; }
}

.gx-comm-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 280px;
  overflow-y: auto;
  border: 1px solid var(--gx-border);
  border-radius: 4px;
  padding: 4px;
}
.gx-comm-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border: 0;
  background: transparent;
  border-radius: 3px;
  cursor: pointer;
  text-align: left;
  font-size: 12px;
  color: var(--gx-text-2);
  &:hover { background: var(--gx-surface-2); color: var(--gx-text); }
  &.active { color: var(--gx-text); }
}
.gx-comm-dot {
  width: 10px; height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  &.small { width: 8px; height: 8px; }
}
.gx-comm-name {
  flex: 1;
  font-family: var(--gx-mono-font);
  font-size: 11px;
}
.gx-comm-size {
  font-family: var(--gx-mono-font);
  font-size: 10px;
  color: var(--gx-text-2);
}

.gx-link-btn {
  border: 0;
  background: transparent;
  color: var(--gx-accent);
  text-align: left;
  font-size: 12px;
  cursor: pointer;
  padding: 0;
  &:hover { text-decoration: underline; }
}

.gx-h {
  margin: 12px 0 6px;
  font-size: 11px;
  color: var(--gx-text-2);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 500;
}
.gx-legend { display: flex; flex-direction: column; gap: 4px; }
.gx-legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--gx-text);
}
.gx-legend-block { margin-bottom: 4px; }
.gx-insights-block { border-top: 1px solid var(--gx-border); padding-top: 8px; }
.gx-legend-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--gx-text);
  padding: 5px 6px;
  border-radius: 6px;
  background: var(--gx-surface-2);
}
.gx-ring-idx {
  width: 16px; height: 16px;
  flex-shrink: 0;
  border-radius: 50%;
  border: 1px solid var(--gx-border-strong);
  color: var(--gx-text-2);
  font-family: var(--gx-mono-font);
  font-size: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.gx-legend-name { flex: 1; font-weight: 500; }
.gx-legend-pos {
  font-size: 11px;
  color: var(--gx-text-2);
  font-family: var(--gx-mono-font);
}
.gx-legend-dot {
  width: 10px; height: 10px;
  border-radius: 50%;
}
.gx-tips {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--gx-text-2);
  line-height: 1.7;
}

/* insights */
details {
  border-top: 1px solid var(--gx-border);
  padding: 8px 0;
  &:first-of-type { border-top: 0; }
}
summary {
  font-size: 12px;
  color: var(--gx-text);
  font-weight: 500;
  cursor: pointer;
  padding: 4px 0;
  list-style: none;
  &::-webkit-details-marker { display: none; }
  &::before {
    content: "▸";
    margin-right: 6px;
    font-size: 9px;
    color: var(--gx-text-2);
    transition: transform 0.15s;
    display: inline-block;
  }
}
details[open] summary::before { transform: rotate(90deg); }

.gx-ins-list {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.gx-ins-list li {
  border-left: 2px solid var(--gx-border);
  padding: 4px 0 4px 8px;
}
.gx-ins-title {
  font-size: 12px;
  color: var(--gx-text);
  margin-bottom: 2px;
}
.gx-ins-detail {
  font-size: 11px;
  color: var(--gx-text-2);
  font-family: var(--gx-mono-font);
  margin-bottom: 4px;
}
.gx-ins-label {
  font-weight: 500;
}
.gx-ins-count {
  font-family: var(--gx-mono-font);
  font-size: 11px;
  color: var(--gx-text-2);
  margin-left: auto;
  background: var(--gx-surface-2);
  padding: 0 6px;
  border-radius: 8px;
}
summary {
  display: flex;
  align-items: center;
  gap: 6px;
}
.gx-ins-desc {
  margin: 4px 0 8px;
  font-size: 11px;
  color: var(--gx-text-2);
  line-height: 1.5;
  font-style: italic;
}
.gx-ins-actions { display: flex; flex-wrap: wrap; gap: 4px; }
.gx-mini-chip {
  border: 1px solid var(--gx-border);
  background: var(--gx-surface);
  color: var(--gx-accent);
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 3px;
  cursor: pointer;
  &:hover { background: var(--gx-surface-2); }
}

.gx-empty {
  padding: 16px;
  text-align: center;
  color: var(--gx-text-2);
  font-size: 12px;
}

/* ===== 画布 ===== */
.gx-canvas {
  flex: 1;
  position: relative;
  background: var(--gx-surface);
  overflow: hidden;
  transition: opacity 0.2s;
  &.fading { opacity: 0; }
}

/* 画布常驻浮动图例 */
.gx-legend-float {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  background: color-mix(in srgb, var(--gx-surface) 88%, transparent);
  backdrop-filter: blur(6px);
  border: 1px solid var(--gx-border);
  border-radius: 8px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
  pointer-events: none;
}
.gx-legend-float-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--gx-text);
}
.gx-legend-float-name { font-weight: 500; min-width: 28px; }
.gx-legend-float-pos {
  font-size: 11px;
  color: var(--gx-text-2);
  font-family: var(--gx-mono-font);
}
.gx-loading, .gx-error, .gx-empty-canvas {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--gx-text-2);
  font-size: 13px;
}
.gx-skeleton {
  width: 80px; height: 80px;
  border-radius: 50%;
  border: 2px solid var(--gx-border);
  border-top-color: var(--gx-primary);
  animation: spin 0.9s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ===== 底栏 ===== */
.gx-footer {
  height: 28px;
  border-top: 1px solid var(--gx-border);
  background: var(--gx-surface);
  color: var(--gx-text-2);
  display: flex;
  align-items: center;
  padding: 0 14px;
  gap: 8px;
  font-size: 11px;
}
.gx-mono { font-family: var(--gx-mono-font); }
.gx-sep { opacity: 0.5; }
.gx-gen { margin-left: auto; }

/* ===== 详情抽屉 ===== */
.gx-detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
  font-size: 13px;
  color: var(--gx-text);
}
.gx-detail-meta {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  background: var(--gx-surface-2);
  border-radius: 6px;
  border: 1px solid var(--gx-border);
}
.gx-pill {
  width: 36px; height: 36px;
  border-radius: 50%;
  flex-shrink: 0;
}
.gx-pill-id {
  font-family: var(--gx-mono-font);
  font-size: 13px;
  color: var(--gx-text);
  word-break: break-all;
}
.gx-pill-kind {
  font-size: 11px;
  color: var(--gx-text-2);
  margin-top: 2px;
}
.gx-detail-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.gx-tag {
  font-size: 11px;
  color: var(--gx-text-2);
  background: var(--gx-surface-2);
  padding: 2px 6px;
  border-radius: 3px;
}
.gx-detail-actions { display: flex; gap: 6px; }
.gx-neighbor-list {
  list-style: none;
  margin: 0; padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 50vh;
  overflow-y: auto;
}
.gx-neighbor-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: 0;
  background: transparent;
  padding: 6px 8px;
  border-radius: 4px;
  cursor: pointer;
  text-align: left;
  &:hover { background: var(--gx-surface-2); }
}
.gx-neighbor-title {
  flex: 1;
  font-size: 12.5px;
  color: var(--gx-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gx-neighbor-meta {
  font-size: 11px;
  color: var(--gx-text-2);
  font-family: var(--gx-mono-font);
}

@media (prefers-reduced-motion: reduce) {
  .gx-canvas, .gx-skeleton { transition: none; animation: none; }
}
</style>
