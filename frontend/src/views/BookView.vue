<script setup>
import { computed, defineAsyncComponent, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  ArrowLeft,
  Back,
  Notebook,
  Share,
  Refresh,
  Edit,
  ArrowUp,
  ArrowDown,
  Rank,
  Setting,
  Connection,
  Guide,
} from "@element-plus/icons-vue";
import { wikiApi } from "@/api";
import { useGraphStore } from "@/stores/graph";
import { useAuthStore } from "@/stores/auth";

const Graph2D = defineAsyncComponent(() => import("@/components/Graph2D.vue"));
const Graph3D = defineAsyncComponent(() => import("@/components/Graph3D.vue"));
const BookMindmap = defineAsyncComponent(() => import("@/components/BookMindmap.vue"));
const BookRoadmap = defineAsyncComponent(() => import("@/components/BookRoadmap.vue"));

const route = useRoute();
const router = useRouter();
const graphStore = useGraphStore();
const auth = useAuthStore();

const book = computed(() => decodeURIComponent(String(route.params.book || "")));

const loading = ref(false);
const tree = ref({ chapters: [] });
const activeTab = ref("tree"); // tree | graph
const graphLoaded = ref(false);
const manageMode = ref(false);   // 管理员:章节排序 / 重命名
const saving = ref(false);
const allBooks = ref([]);        // 移动章节用的可选书目

// 章节 slug 的当前顺序(管理模式下本地维护,保存时一次性写回)
const chapterSlugs = computed(() => (tree.value.chapters || []).map((c) => c.slug));

// el-tree 数据:把后端返回的 chapters 嵌套结构映射成 el-tree 需要的 data 形式。
// node-key 用带父路径的复合 nodeKey(全树唯一);slug/kind 保留原值供跳转消歧。
// 同名 topic/entity(如「工作研究」既是主题又是实体)、同一实体挂多个主题下时,
// 用 slug 当 node-key 会冲突,导致 el-tree 展开/高亮/选中错乱 —— 必须用唯一 key。
const treeData = computed(() => {
  return (tree.value.chapters || []).map((c) => {
    const chapterKey = `source:${c.slug}`;
    const children = [];
    for (const t of c.topics || []) {
      const topicKey = `${chapterKey}/topic:${t.slug}`;
      children.push({
        nodeKey: topicKey,
        slug: t.slug,
        title: t.title,
        kind: "topic",
        children: (t.entities || []).map((e) => ({
          nodeKey: `${topicKey}/entity:${e.slug}`,
          slug: e.slug,
          title: e.title,
          kind: "entity",
          exists: e.exists !== false,
          isLeaf: true,
        })),
      });
    }
    if ((c.orphan_entities || []).length) {
      const orphanKey = `${chapterKey}/__orphan`;
      children.push({
        nodeKey: orphanKey,
        slug: `__orphan_${c.slug}`,
        title: "(无主题分组)",
        kind: "topic",
        orphan: true,
        children: c.orphan_entities.map((e) => ({
          nodeKey: `${orphanKey}/entity:${e.slug}`,
          slug: e.slug,
          title: e.title,
          kind: "entity",
          isLeaf: true,
        })),
      });
    }
    return {
      nodeKey: chapterKey,
      slug: c.slug,
      title: c.title,
      kind: "source",
      children,
    };
  });
});

const treeStats = computed(() => {
  const chapters = tree.value.chapters || [];
  let topics = 0;
  let entities = 0;
  for (const c of chapters) {
    topics += (c.topics || []).length;
    entities += (c.orphan_entities || []).length;
    for (const t of c.topics || []) entities += (t.entities || []).length;
  }
  return { chapters: chapters.length, topics, entities };
});

async function loadTree() {
  if (!book.value) return;
  loading.value = true;
  try {
    const data = await wikiApi.bookTree(book.value);
    tree.value = data?.data || data || { chapters: [] };
  } catch (e) {
    ElMessage.error("书内目录加载失败:" + (e.response?.data?.detail || e.message));
    tree.value = { chapters: [] };
  } finally {
    loading.value = false;
  }
}

async function loadBookGraph(force = false) {
  if (!book.value) return;
  await graphStore.load(force, { book: book.value });
  if (graphStore.loadError) {
    ElMessage.error("图谱加载失败:" + graphStore.loadError);
  } else {
    graphLoaded.value = true;
  }
}

// ===== ② 导航:返回书架 / 上一页 =====
function goShelf() {
  router.push("/kb/wiki");
}
function goBack() {
  // 浏览器历史可回退则回退,否则退回书架
  if (window.history.length > 1) router.back();
  else router.push("/kb/wiki");
}

// ===== ① 管理:章节排序 / 重命名 / 移动 =====
function toggleManage() {
  manageMode.value = !manageMode.value;
}

function moveChapter(index, dir) {
  const chapters = tree.value.chapters || [];
  const j = index + dir;
  if (j < 0 || j >= chapters.length) return;
  const arr = chapters.slice();
  [arr[index], arr[j]] = [arr[j], arr[index]];
  tree.value = { ...tree.value, chapters: arr };
}

async function saveOrder() {
  saving.value = true;
  try {
    await wikiApi.reorderChapters(book.value, chapterSlugs.value);
    ElMessage.success("章节顺序已保存");
  } catch (e) {
    ElMessage.error("保存失败:" + (e.response?.data?.detail || e.message));
  } finally {
    saving.value = false;
  }
}

async function renameNode(node) {
  // node: { slug, title, kind }  — 章节或主题都可改名
  try {
    const { value } = await ElMessageBox.prompt(
      `重命名「${node.title}」`,
      node.kind === "source" ? "重命名章节" : "重命名主题",
      {
        confirmButtonText: "保存",
        cancelButtonText: "取消",
        inputValue: node.title,
        inputPattern: /\S+/,
        inputErrorMessage: "名称不能为空",
      }
    );
    const title = (value || "").trim();
    if (!title || title === node.title) return;
    await wikiApi.renamePage(node.slug, title, node.kind);
    node.title = title;
    ElMessage.success("已重命名");
  } catch (e) {
    if (e === "cancel" || e === "close") return;
    ElMessage.error("重命名失败:" + (e.response?.data?.detail || e.message));
  }
}

async function loadAllBooks() {
  try {
    const res = await wikiApi.books();
    const body = res?.data || res || {};
    allBooks.value = (body.books || [])
      .map((b) => b.book)
      .filter((b) => b && b !== "未分类");
  } catch (_) {
    allBooks.value = [];
  }
}

async function moveChapterToBook(chapter) {
  const others = allBooks.value.filter((b) => b !== book.value);
  if (!others.length) {
    ElMessage.info("没有其它知识库可移动到");
    return;
  }
  try {
    const { value } = await ElMessageBox.prompt(
      `把章节「${chapter.title}」移动到哪本书?\n可选:${others.join(" / ")}`,
      "移动章节",
      {
        confirmButtonText: "移动",
        cancelButtonText: "取消",
        inputValue: others[0],
        inputPattern: /\S+/,
        inputErrorMessage: "请输入目标书名",
      }
    );
    const target = (value || "").trim();
    if (!target || target === book.value) return;
    await wikiApi.moveChapter(chapter.slug, target);
    ElMessage.success(`已移动到「${target}」`);
    await loadTree();
  } catch (e) {
    if (e === "cancel" || e === "close") return;
    ElMessage.error("移动失败:" + (e.response?.data?.detail || e.message));
  }
}

function onNodeClick(data) {
  // el-tree 节点点击:source/topic/entity 都跳到对应 wiki 页(orphan 桶不跳)
  if (data.orphan) return;
  if (!data.slug || data.slug.startsWith("__orphan_")) return;
  // 带上节点自身 kind,消歧同名实体/主题(如「程序分析」既是实体又是主题)
  router.push({
    path: `/kb/wiki/${encodeURIComponent(data.slug)}`,
    query: data.kind ? { kind: data.kind } : {},
  });
}

function onGraphNodeClick(node) {
  if (!node) return;
  graphStore.select(node.id);
  const slug = node.slug || node.id;
  router.push({
    path: `/kb/wiki/${encodeURIComponent(slug)}`,
    query: node.kind ? { kind: node.kind } : {},
  });
}

watch(activeTab, async (v) => {
  if (v === "graph" && !graphLoaded.value) {
    await loadBookGraph(false);
  }
});

watch(
  () => book.value,
  async () => {
    graphLoaded.value = false;
    await loadTree();
    if (activeTab.value === "graph") {
      await loadBookGraph(true);
    }
  }
);

onMounted(() => {
  loadTree();
  if (auth.isAdmin) loadAllBooks();
});
</script>

<template>
  <div class="book-view">
    <header class="bv-header">
      <el-button :icon="Back" link @click="goBack">上一页</el-button>
      <el-button :icon="ArrowLeft" link @click="goShelf">返回书架</el-button>
      <h1 class="bv-title">{{ book }}</h1>
      <div class="bv-stats">
        <span><b>{{ treeStats.chapters }}</b> 章</span>
        <span class="bv-sep">·</span>
        <span><b>{{ treeStats.topics }}</b> 主题</span>
        <span class="bv-sep">·</span>
        <span><b>{{ treeStats.entities }}</b> 实体</span>
      </div>
      <div v-if="auth.isAdmin" class="bv-admin">
        <el-button
          size="small"
          :type="manageMode ? 'primary' : 'default'"
          :icon="Setting"
          @click="toggleManage"
        >{{ manageMode ? "完成管理" : "管理" }}</el-button>
        <el-button
          v-if="manageMode"
          size="small"
          type="success"
          :loading="saving"
          @click="saveOrder"
        >保存顺序</el-button>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="bv-tabs">
      <el-tab-pane name="tree">
        <template #label>
          <span class="bv-tab-label">
            <el-icon><Notebook /></el-icon>目录树
          </span>
        </template>
        <div v-loading="loading" class="bv-tree-wrap">
          <el-empty v-if="!loading && !treeData.length" description="暂无章节" />

          <!-- 管理模式:章节排序 / 重命名 / 移动 -->
          <div v-else-if="manageMode" class="bv-manage">
            <p class="bv-manage-tip">拖动用上下按钮调整章节顺序,改完点「保存顺序」;主题在下方目录树里改名。</p>
            <ul class="bv-chapter-list">
              <li v-for="(c, i) in tree.chapters" :key="c.slug" class="bv-chapter-row">
                <span class="bv-chapter-idx">{{ i + 1 }}</span>
                <span class="bv-chapter-name">{{ c.title }}</span>
                <span class="bv-chapter-meta">{{ (c.topics || []).length }} 主题</span>
                <span class="bv-chapter-ops">
                  <el-button size="small" :icon="ArrowUp" :disabled="i === 0" @click="moveChapter(i, -1)" />
                  <el-button size="small" :icon="ArrowDown" :disabled="i === tree.chapters.length - 1" @click="moveChapter(i, 1)" />
                  <el-button size="small" :icon="Edit" @click="renameNode({ slug: c.slug, title: c.title, kind: 'source' })">改名</el-button>
                  <el-button size="small" :icon="Rank" @click="moveChapterToBook(c)">移动</el-button>
                </span>
              </li>
            </ul>
            <p class="bv-manage-tip">主题重命名:</p>
            <el-tree
              :data="treeData"
              node-key="nodeKey"
              :default-expand-all="false"
              :expand-on-click-node="false"
            >
              <template #default="{ data }">
                <span class="bv-node" :class="`bv-node-${data.kind}`" :data-orphan="data.orphan ? '1' : null">
                  <span class="bv-node-kind">
                    <template v-if="data.kind === 'source'">章</template>
                    <template v-else-if="data.kind === 'topic'">主题</template>
                    <template v-else>实体</template>
                  </span>
                  <span class="bv-node-title">{{ data.title }}</span>
                  <el-button
                    v-if="(data.kind === 'topic' && !data.orphan) || data.kind === 'source'"
                    size="small"
                    text
                    :icon="Edit"
                    class="bv-node-rename"
                    @click.stop="renameNode(data)"
                  >改名</el-button>
                </span>
              </template>
            </el-tree>
          </div>

          <el-tree
            v-else
            :data="treeData"
            node-key="nodeKey"
            :default-expand-all="false"
            :expand-on-click-node="false"
            @node-click="onNodeClick"
          >
            <template #default="{ data }">
              <span class="bv-node" :class="`bv-node-${data.kind}`" :data-orphan="data.orphan ? '1' : null">
                <span class="bv-node-kind">
                  <template v-if="data.kind === 'source'">章</template>
                  <template v-else-if="data.kind === 'topic'">主题</template>
                  <template v-else>实体</template>
                </span>
                <span class="bv-node-title">{{ data.title }}</span>
                <span v-if="data.children?.length" class="bv-node-count">{{ data.children.length }}</span>
              </span>
            </template>
          </el-tree>
        </div>
      </el-tab-pane>

      <el-tab-pane name="mindmap" lazy>
        <template #label>
          <span class="bv-tab-label">
            <el-icon><Connection /></el-icon>思维导图
          </span>
        </template>
        <div class="bv-mindmap-wrap" v-loading="loading">
          <el-empty v-if="!loading && !treeData.length" description="暂无章节" />
          <BookMindmap v-else :book="book" :chapters="tree.chapters || []" />
        </div>
      </el-tab-pane>

      <el-tab-pane name="roadmap" lazy>
        <template #label>
          <span class="bv-tab-label">
            <el-icon><Guide /></el-icon>学习路线
          </span>
        </template>
        <div class="bv-roadmap-wrap" v-loading="loading">
          <el-empty v-if="!loading && !treeData.length" description="暂无章节" />
          <BookRoadmap v-else :book="book" :chapters="tree.chapters || []" />
        </div>
      </el-tab-pane>

      <el-tab-pane name="graph">
        <template #label>
          <span class="bv-tab-label">
            <el-icon><Share /></el-icon>知识图谱
          </span>
        </template>
        <div class="bv-graph-wrap">
          <div class="bv-graph-toolbar">
            <span class="bv-graph-stat">
              {{ graphStore.nodes.length }} 节点 · {{ graphStore.edges.length }} 边
            </span>
            <div class="bv-graph-mode">
              <button
                class="bv-mode-btn"
                :class="{ active: graphStore.mode === '2d' }"
                @click="graphStore.setMode('2d')"
              >2D</button>
              <button
                class="bv-mode-btn"
                :class="{ active: graphStore.mode === '3d' }"
                @click="graphStore.setMode('3d')"
              >3D</button>
            </div>
            <el-button
              size="small"
              :icon="Refresh"
              :loading="graphStore.loading"
              @click="loadBookGraph(true)"
            >刷新</el-button>
          </div>
          <div class="bv-graph-canvas">
            <div v-if="graphStore.loading" class="bv-graph-loading">正在加载该书的知识图谱…</div>
            <div v-else-if="graphStore.loadError" class="bv-graph-loading">{{ graphStore.loadError }}</div>
            <div v-else-if="!graphStore.nodes.length" class="bv-graph-loading">该书暂无图谱节点</div>
            <template v-else>
              <Graph2D
                v-if="graphStore.mode === '2d'"
                @node-click="onGraphNodeClick"
              />
              <Graph3D
                v-else
                @node-click="onGraphNodeClick"
              />
            </template>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.book-view {
  max-width: 1200px;
  margin: 0 auto;
  padding: 4px 0 24px;
}

.bv-header {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 8px 4px 16px;
  border-bottom: 1px solid #e2e8f0;
  margin-bottom: 8px;
}
.bv-title {
  margin: 0;
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: 20px;
  font-weight: 600;
  color: #0f172a;
}
.bv-stats {
  margin-left: auto;
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 12.5px;
  color: #64748b;
}
.bv-stats b { color: #0f172a; }
.bv-sep { color: #cbd5e1; margin: 0 6px; }

.bv-admin {
  display: inline-flex;
  gap: 8px;
  margin-left: 14px;
}

/* manage mode */
.bv-manage {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.bv-manage-tip {
  margin: 4px 0;
  font-size: 12px;
  color: #94a3b8;
}
.bv-chapter-list {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
}
.bv-chapter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid #f1f5f9;
}
.bv-chapter-row:last-child { border-bottom: 0; }
.bv-chapter-idx {
  width: 24px;
  text-align: center;
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 12px;
  color: #94a3b8;
  flex-shrink: 0;
}
.bv-chapter-name {
  flex: 1;
  min-width: 0;
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-weight: 600;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bv-chapter-meta {
  font-size: 11px;
  color: #94a3b8;
  font-family: "JetBrains Mono", ui-monospace, monospace;
  flex-shrink: 0;
}
.bv-chapter-ops {
  display: inline-flex;
  gap: 4px;
  flex-shrink: 0;
}
.bv-node-rename {
  margin-left: 8px;
}

.bv-tabs {
  --el-tabs-header-height: 42px;
}
.bv-tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

/* tree */
.bv-tree-wrap {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px 14px;
  min-height: 260px;
}
.bv-node {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13.5px;
  line-height: 1.6;
}
.bv-node-kind {
  font-size: 10.5px;
  letter-spacing: 0.04em;
  padding: 1px 5px;
  border-radius: 3px;
  background: #f1f5f9;
  color: #64748b;
  flex-shrink: 0;
}
.bv-node-source .bv-node-kind {
  background: #fff7ed;
  color: #ea580c;
}
.bv-node-topic .bv-node-kind {
  background: #eff6ff;
  color: #2563eb;
}
.bv-node-source .bv-node-title {
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-weight: 600;
  color: #0f172a;
}
.bv-node-topic .bv-node-title { color: #1e293b; }
.bv-node-entity .bv-node-title { color: #475569; }
.bv-node[data-orphan="1"] .bv-node-title {
  color: #94a3b8;
  font-style: italic;
}
.bv-node-count {
  font-size: 11px;
  color: #94a3b8;
  font-family: "JetBrains Mono", ui-monospace, monospace;
}

/* mindmap / roadmap */
.bv-mindmap-wrap {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  min-height: 480px;
  height: calc(100vh - 220px);
  overflow: hidden;
}
.bv-roadmap-wrap {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  min-height: 480px;
  padding: 18px 8px;
}

/* graph */
.bv-graph-wrap {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  min-height: 480px;
  height: calc(100vh - 220px);
}
.bv-graph-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid #e2e8f0;
}
.bv-graph-stat {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 12px;
  color: #64748b;
}
.bv-graph-mode {
  margin-left: auto;
  display: inline-flex;
  border: 1px solid #e2e8f0;
  border-radius: 5px;
  overflow: hidden;
}
.bv-mode-btn {
  border: 0;
  background: #fff;
  color: #64748b;
  padding: 4px 12px;
  font-size: 12px;
  font-family: "JetBrains Mono", ui-monospace, monospace;
  cursor: pointer;
}
.bv-mode-btn.active {
  background: #f97316;
  color: #fff;
}
.bv-graph-canvas {
  flex: 1;
  position: relative;
  overflow: hidden;
}
.bv-graph-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #94a3b8;
  font-size: 13px;
}
</style>
