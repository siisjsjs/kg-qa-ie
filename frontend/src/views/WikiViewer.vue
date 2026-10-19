<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { Edit, View as ViewIcon, Delete, Back } from "@element-plus/icons-vue";
import MarkdownViewer from "@/components/MarkdownViewer.vue";
import SidePanel from "@/components/SidePanel.vue";
import WikiSearchBar from "@/components/WikiSearchBar.vue";
import MiniEgoGraph from "@/components/MiniEgoGraph.vue";
import { visibleCitationDetails } from "@/utils/citations";
import { genImageUrl } from "@/stores/conversations";
import { recordVisit } from "@/utils/visited";
import { wikiApi } from "@/api";
import { useAuthStore } from "@/stores/auth";
import { KIND_LABELS, LINK_TYPE_LABELS } from "@/constants/graphTheme";
import { colorOf } from "@/constants/typeColors";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const slug = computed(() => route.params.slug);
// kind 用于消歧同名页(实体与主题可同名,如「程序分析」)。来自路由 query。
const kindParam = computed(() => route.query.kind || undefined);
const page = ref(null);
const indexData = ref(null);
const loading = ref(false);
const saving = ref(false);
const deleting = ref(false);
const editing = ref(false);

// 编辑缓冲:body + 结构化 frontmatter
const editBody = ref("");
const editFm = ref({
  title: "",
  aliases: [],
  tags: [],
  domains: [],
});
// 只读 frontmatter(如 version / created / updated / sources / slug / kind / confidence)
const readonlyFm = ref({});

// 百科关系聚合
const relations = ref(null);
const relationsLoading = ref(false);

const KIND_LABEL = {
  ...KIND_LABELS,
  schema: "Schema",
};

function relTypeLabel(t) {
  return LINK_TYPE_LABELS[t] || t || "相关";
}

// 问答记录页:与智能问答一致的 [N] 编号形式渲染引用
const pageCitations = computed(() => {
  const p = page.value;
  if (!p || !["query", "synthesis", "comparison"].includes(p.kind)) return [];
  const fm = p.frontmatter || {};
  const details = Array.isArray(fm.citation_details) && fm.citation_details.length
    ? fm.citation_details
    : [
        ...(fm.citations || []),
        ...(fm.web_citations || []),
      ];
  return visibleCitationDetails(p.body || p.content || "", details);
});

function imagesFromFrontmatter(raw) {
  const map = {};
  for (const img of raw || []) {
    if (!img?.image_id) continue;
    map[img.image_id] = {
      image_id: img.image_id,
      status: img.status === "failed" ? "failed" : "ready",
      prompt: img.prompt || "",
      caption: img.caption || "",
      chart: img.chart || null,
      url: img.status === "failed" ? "" : (img.url || genImageUrl(img.image_id)),
    };
  }
  return map;
}

const pageImages = computed(() => imagesFromFrontmatter(page.value?.frontmatter?.images));

const imagePanel = ref({ open: false, image: null });
function openWikiImage(payload) {
  const info = pageImages.value[payload?.imageId] || {};
  imagePanel.value = {
    open: true,
    image: {
      url: payload?.url || info.url || "",
      prompt: info.prompt || "",
      caption: info.caption || "",
      chart: info.chart || null,
    },
  };
}
function closeWikiImage() {
  imagePanel.value = { open: false, image: null };
}

const persistSessionId = computed(() => {
  const raw = page.value?.frontmatter?.session_id;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : null;
});
function openPersistSession() {
  if (!persistSessionId.value) return;
  router.push({ path: "/kb/query", query: { session: String(persistSessionId.value) } });
}

async function loadRelations() {
  if (!slug.value) return;
  relationsLoading.value = true;
  relations.value = null;
  try {
    const data = await wikiApi.relations(slug.value, kindParam.value);
    relations.value = data?.data || data;
  } catch (e) {
    relations.value = null;
  } finally {
    relationsLoading.value = false;
  }
}

async function load() {
  if (!slug.value) {
    loading.value = true;
    try {
      const data = await wikiApi.index();
      indexData.value = data?.data || data;
    } catch (e) {
      // 显示骨架
    } finally {
      loading.value = false;
    }
    return;
  }
  loading.value = true;
  page.value = null;
  try {
    const data = await wikiApi.page(slug.value, kindParam.value);
    page.value = data?.data || data;
    // 学习足迹:页面成功打开即记录(学习路线图的进度点亮用)
    recordVisit(page.value?.slug || slug.value);
    primeEditBuffer();
    // 处理 ?edit=1
    if (route.query.edit && auth.isAdmin) {
      editing.value = true;
    }
    // 加载百科关系(非阻塞,失败静默)
    loadRelations();
    // chunk 引用跳转:按 ?hl= / ?heading= 滚动定位并高亮(非阻塞)
    applyCitationHighlight();
  } catch (e) {
    if (e.response?.status === 404) {
      page.value = { not_found: true };
    } else if (e.response?.status === 410) {
      page.value = { gone: true, detail: e.response?.data?.detail };
    } else {
      ElMessage.error("加载失败：" + (e.response?.data?.detail || e.message));
    }
  } finally {
    loading.value = false;
  }
}

function primeEditBuffer() {
  if (!page.value) return;
  editBody.value = page.value.body || page.value.content || page.value.markdown || "";
  const fm = page.value.frontmatter || {};
  editFm.value = {
    title: fm.title || page.value.title || slug.value || "",
    aliases: Array.isArray(fm.aliases) ? [...fm.aliases] : [],
    tags: Array.isArray(fm.tags) ? [...fm.tags] : [],
    domains: Array.isArray(fm.domains) ? [...fm.domains] : [],
  };
  // 只读字段
  const ro = { ...fm };
  ["title", "aliases", "tags", "domains"].forEach((k) => delete ro[k]);
  readonlyFm.value = ro;
}

function startEdit() {
  if (!auth.isAdmin) {
    ElMessage.warning("仅管理员可编辑");
    return;
  }
  primeEditBuffer();
  editing.value = true;
}

function cancelEdit() {
  editing.value = false;
  // 清掉 ?edit=1
  if (route.query.edit) {
    router.replace({ query: { ...route.query, edit: undefined } });
  }
}

async function save({ exitAfter = false } = {}) {
  saving.value = true;
  try {
    // 把结构化 frontmatter 字段合并回完整 frontmatter(保留只读字段不动)
    const mergedFm = { ...readonlyFm.value, ...editFm.value };
    await wikiApi.updatePage(slug.value, {
      body: editBody.value,
      frontmatter: mergedFm,
      bump_version: true,
    });
    ElMessage.success("已保存");
    if (exitAfter) {
      editing.value = false;
      if (route.query.edit) {
        router.replace({ query: { ...route.query, edit: undefined } });
      }
    }
    await load();
  } catch (e) {
    ElMessage.error("保存失败：" + (e.response?.data?.detail || e.message));
  } finally {
    saving.value = false;
  }
}

async function doDelete() {
  if (!auth.isAdmin) {
    ElMessage.warning("仅管理员可删除");
    return;
  }
  const kind = page.value?.kind || "";
  const isSource = kind === "source";
  const tip = isSource
    ? "这是素材页:删除会级联清理引用此素材的实体/主题段,且变孤立的实体/主题页将整页删除。该操作不可恢复。"
    : "删除该页面后，访问会返回 410 Gone。该操作不可恢复。";
  try {
    await ElMessageBox.confirm(tip, `确认删除 ${page.value?.title || slug.value} ?`, {
      confirmButtonText: "确认删除",
      cancelButtonText: "取消",
      type: "warning",
      confirmButtonClass: "el-button--danger",
    });
  } catch (_) {
    return; // cancel
  }
  deleting.value = true;
  try {
    const res = await wikiApi.deletePage(slug.value);
    if (res?.cascaded && res?.removed_pages?.length > 1) {
      ElMessage.success(
        `已删除 + 级联清理:整页删除 ${res.removed_pages.length} 个，更新 ${res.updated_pages?.length || 0} 个`
      );
    } else {
      ElMessage.success("已删除");
    }
    router.replace("/kb/wiki");
  } catch (e) {
    ElMessage.error("删除失败：" + (e.response?.data?.detail || e.message));
  } finally {
    deleting.value = false;
  }
}

// ===== chunk 引用高亮定位(改造计划 §8.3:?hl=原文片段 & ?heading=小节名) =====
const readerEl = ref(null);
let hlTimer = null;

function normText(s) {
  return String(s || "").replace(/\s+/g, "").toLowerCase();
}

async function applyCitationHighlight() {
  const hl = String(route.query.hl || "");
  const heading = String(route.query.heading || "");
  if (!hl && !heading) return;
  await nextTick();
  const root = readerEl.value;
  if (!root) return;
  // 之前的高亮先清掉(同页二次跳转)
  root.querySelectorAll(".cite-hl").forEach((el) => el.classList.remove("cite-hl"));
  let target = null;
  if (hl) {
    // 空白差异(渲染合并/换行)用归一化文本兜底匹配
    const needle = normText(hl).slice(0, 60);
    if (needle) {
      const blocks = root.querySelectorAll("p, li, td, blockquote, pre, h1, h2, h3, h4, h5, h6");
      for (const el of blocks) {
        if (normText(el.textContent).includes(needle)) {
          target = el;
          break;
        }
      }
    }
  }
  if (!target && heading) {
    const hn = normText(heading);
    for (const el of root.querySelectorAll("h1, h2, h3, h4, h5, h6")) {
      if (hn && normText(el.textContent).includes(hn)) {
        target = el;
        break;
      }
    }
  }
  if (!target) return;
  target.classList.add("cite-hl");
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  if (hlTimer) clearTimeout(hlTimer);
  hlTimer = setTimeout(() => target.classList.remove("cite-hl"), 6000);
}

watch([slug, kindParam], load);
// 同页跳不同引用(slug 不变、hl 变)时只重新定位,不重载页面
watch(
  () => [route.query.hl, route.query.heading],
  () => { if (page.value && !page.value.not_found) applyCitationHighlight(); }
);
onMounted(load);

function gotoPage(s, kind) {
  router.push({
    path: `/kb/wiki/${encodeURIComponent(s)}`,
    query: kind ? { kind } : {},
  });
}

// 问答记录页引用列表:web 来源外链新标签,KB 来源站内跳转
function gotoCitation(c) {
  if (c.type === "web") {
    if (c.url) window.open(c.url, "_blank", "noopener");
    return;
  }
  gotoPage(c.slug, c.kind);
}

function goBack() {
  // 优先回退到来源页(书籍目录/图谱/索引);无历史则退回 Wiki 首页
  if (window.history.length > 1) router.back();
  else router.push("/kb/wiki");
}

const groupedIndex = computed(() => {
  const idx = indexData.value;
  if (!idx) return null;
  const by = idx.by_kind || {};
  // 顺序：实体 / 主题 / 素材 / 综合 / 对比 / 查询（与 KIND_LABELS 对齐）
  const ordered = ["entity", "topic", "source", "synthesis", "comparison", "query"];
  const out = {};
  for (const k of ordered) {
    out[KIND_LABEL[k] || k] = by[k] || [];
  }
  return out;
});

const totalIndex = computed(() => {
  const g = groupedIndex.value;
  if (!g) return 0;
  return Object.values(g).reduce((s, l) => s + (l?.length || 0), 0);
});

// 信息框是否有任何关系内容(用于决定右栏是否展示百科信息框)
const hasRelations = computed(() => {
  const r = relations.value;
  if (!r) return false;
  return (
    (r.related_entities?.length || 0) +
      (r.mentioned_by_topics?.length || 0) +
      (r.sources?.length || 0) >
    0
  );
});

const GRAPH_DEFAULT_LIMIT = 24;
const graphLimit = ref(GRAPH_DEFAULT_LIMIT);

watch(slug, () => {
  graphLimit.value = GRAPH_DEFAULT_LIMIT;
});

const allMiniNodes = computed(() => relations.value?.mini_graph?.nodes || []);
const allMiniEdges = computed(() => relations.value?.mini_graph?.edges || []);
const miniNeighborCount = computed(() => allMiniNodes.value.filter((n) => !n.is_center).length);
const showGraphLimit = computed(() => miniNeighborCount.value > GRAPH_DEFAULT_LIMIT);
const effectiveGraphLimit = computed(() => {
  const max = miniNeighborCount.value;
  if (max <= GRAPH_DEFAULT_LIMIT) return max;
  return Math.min(Math.max(1, Number(graphLimit.value) || GRAPH_DEFAULT_LIMIT), max);
});
const miniGraphNodes = computed(() => {
  const nodes = allMiniNodes.value;
  if (!showGraphLimit.value) return nodes;
  const others = nodes.filter((n) => !n.is_center).slice(0, effectiveGraphLimit.value);
  const keep = new Set(others.map((n) => n.id));
  const center = nodes.find((n) => n.is_center);
  if (center) keep.add(center.id);
  return nodes.filter((n) => keep.has(n.id));
});
const miniGraphEdges = computed(() => {
  const ids = new Set(miniGraphNodes.value.map((n) => n.id));
  return allMiniEdges.value.filter((e) => ids.has(e.source) && ids.has(e.target));
});
const showMiniGraph = computed(() => allMiniNodes.value.length > 1);

// 右栏"下层节点"列表标题随 kind 切换:实体→相关实体 / 主题→包含实体 / 素材→衍生主题
const relatedLabel = computed(() => {
  const k = relations.value?.kind || page.value?.kind;
  if (k === "topic") return "包含实体";
  if (k === "source") return "衍生主题";
  return "相关实体";
});
// 关系图谱副标题(说明这张图在表达什么)
const graphHint = computed(() => {
  const k = relations.value?.kind || page.value?.kind;
  if (k === "topic") return "主题下的实体";
  if (k === "source") return "由该素材衍生的主题";
  return "与该实体语义相关的节点";
});

function onMiniNodeClick(node) {
  if (node?.slug && node.slug !== slug.value) gotoPage(node.slug, node.kind);
}
</script>

<template>
  <div v-loading="loading" class="wiki-shell" :class="{ 'has-image-panel': imagePanel.open }">
    <div class="wiki-main">
    <!-- 索引视图 -->
    <template v-if="!slug">
      <div class="wiki-topbar">
        <WikiSearchBar />
      </div>
      <div class="page-header">
        <div>
          <h2 class="page-title">Wiki 索引</h2>
          <p class="page-subtitle">实体 / 主题 / 素材 / 综合 / 查询</p>
        </div>
      </div>
      <el-empty v-if="!groupedIndex || totalIndex === 0" description="尚未初始化或无数据" />
      <template v-else>
        <el-card
          v-for="(list, kind) in groupedIndex"
          :key="kind"
          shadow="never"
          style="margin-bottom: 12px"
          v-show="list.length"
        >
          <template #header>
            <strong>{{ kind }}</strong>
            <el-tag size="small" style="margin-left: 8px">{{ list.length }}</el-tag>
          </template>
          <div class="link-grid">
            <a
              v-for="p in list"
              :key="p.slug"
              class="wiki-chip"
              @click.prevent="gotoPage(p.slug)"
            >{{ p.title || p.slug }}</a>
          </div>
        </el-card>
      </template>
    </template>

    <!-- 单页视图 -->
    <template v-else>
      <div class="page-header">
        <div>
          <el-button :icon="Back" link class="back-btn" @click="goBack">返回</el-button>
          <h2 class="page-title">{{ page?.title || slug }}</h2>
          <p class="page-subtitle" v-if="page?.kind">
            <el-tag size="small">{{ KIND_LABEL[page.kind] || page.kind }}</el-tag>
            <span v-if="page.frontmatter?.version" class="muted-meta">
              · v{{ page.frontmatter.version }}
            </span>
            <span v-if="page.frontmatter?.tags?.length" style="margin-left:6px">
              <el-tag
                v-for="t in page.frontmatter.tags"
                :key="t"
                size="small"
                effect="plain"
                style="margin-right:4px"
              >{{ t }}</el-tag>
            </span>
            <el-button
              v-if="persistSessionId"
              link
              type="primary"
              size="small"
              style="margin-left:8px"
              @click="openPersistSession"
            >查看完整会话 #{{ persistSessionId }}</el-button>
          </p>
        </div>
        <div class="actions">
          <el-button
            v-if="auth.isAdmin && !editing && !page?.not_found && !page?.gone"
            size="small"
            :icon="Edit"
            type="primary"
            @click="startEdit"
          >编辑</el-button>
          <el-button
            v-if="auth.isAdmin && !editing && !page?.not_found && !page?.gone"
            size="small"
            :icon="Delete"
            type="danger"
            :loading="deleting"
            @click="doDelete"
          >删除</el-button>
          <template v-if="editing">
            <el-button size="small" @click="cancelEdit">取消</el-button>
            <el-button size="small" :loading="saving" @click="save({ exitAfter: false })">保存</el-button>
            <el-button size="small" type="primary" :loading="saving" @click="save({ exitAfter: true })">
              保存并退出
            </el-button>
          </template>
        </div>
      </div>

      <el-empty v-if="page?.not_found" :description="`页面 ${slug} 不存在`" />
      <el-alert
        v-else-if="page?.gone"
        type="warning"
        :title="`页面 ${slug} 已被删除（来源被清理）`"
        :description="page.detail?.message || '410 Gone'"
        :closable="false"
        show-icon
      />

      <el-row v-else-if="page" :gutter="14">
        <el-col :xs="24" :md="page.kind === 'query' ? 24 : 17">
          <!-- 阅读模式 -->
          <el-card v-if="!editing" shadow="never">
            <el-collapse v-if="page.frontmatter">
              <el-collapse-item title="frontmatter（只读）" name="fm">
                <pre class="frontmatter-pre">{{ JSON.stringify(page.frontmatter, null, 2) }}</pre>
              </el-collapse-item>
            </el-collapse>
            <div ref="readerEl">
              <MarkdownViewer
                :source="page.body || page.content || ''"
                :citation-mode="['query', 'synthesis', 'comparison'].includes(page.kind)"
                :citations="pageCitations"
                :images="pageImages"
                @image-click="openWikiImage"
              />
            </div>
            <div
              v-if="['query', 'synthesis', 'comparison'].includes(page.kind) && pageCitations.length"
              class="citation-sources"
            >
              <div class="citation-sources-label">引用来源</div>
              <ol>
                <li v-for="(c, ci) in pageCitations" :key="c.key || c.slug">
                  <a @click.prevent="gotoCitation(c)">
                    <span class="citation-number">{{ ci + 1 }}</span>
                    <span class="citation-title">{{ c.title }}</span>
                  </a>
                </li>
              </ol>
            </div>
          </el-card>

          <!-- 编辑模式 -->
          <el-card v-else shadow="never" class="editor-card">
            <!-- frontmatter 表单 -->
            <el-form label-position="top" size="small" class="fm-form">
              <el-form-item label="标题">
                <el-input v-model="editFm.title" placeholder="页面标题" />
              </el-form-item>
              <el-form-item label="别名 (回车添加)">
                <el-select
                  v-model="editFm.aliases"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  placeholder="如 ergonomics / 人因工程"
                  style="width: 100%"
                />
              </el-form-item>
              <el-form-item label="标签 tags">
                <el-select
                  v-model="editFm.tags"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  placeholder="如 method / classic"
                  style="width: 100%"
                />
              </el-form-item>
              <el-form-item label="领域 domains">
                <el-select
                  v-model="editFm.domains"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  placeholder="如 人因工程 / 精益生产"
                  style="width: 100%"
                />
              </el-form-item>
              <el-collapse v-if="Object.keys(readonlyFm).length">
                <el-collapse-item title="其它 frontmatter（只读）" name="ro">
                  <pre class="frontmatter-pre">{{ JSON.stringify(readonlyFm, null, 2) }}</pre>
                </el-collapse-item>
              </el-collapse>
            </el-form>

            <!-- body 左编辑右预览 -->
            <div class="editor-split">
              <div class="editor-pane">
                <div class="pane-label">
                  <el-icon><Edit /></el-icon> 编辑（markdown · 支持 [[双链]] / KaTeX）
                </div>
                <el-input
                  v-model="editBody"
                  type="textarea"
                  :rows="22"
                  resize="none"
                  placeholder="编辑 markdown 正文..."
                  class="md-textarea"
                />
              </div>
              <div class="preview-pane">
                <div class="pane-label">
                  <el-icon><ViewIcon /></el-icon> 实时预览
                </div>
                <div class="preview-body">
                  <MarkdownViewer :source="editBody" />
                </div>
              </div>
            </div>
          </el-card>
        </el-col>
        <el-col v-if="page.kind !== 'query'" :xs="24" :md="7">
          <!-- 百科信息框 -->
          <el-card shadow="never" class="infobox" v-loading="relationsLoading">
            <template #header>
              <div class="infobox-head">
                <span class="ib-dot" :style="{ background: colorOf(page.kind) }" />
                <strong>{{ page?.title || slug }}</strong>
                <el-tag size="small" effect="plain">{{ KIND_LABEL[page.kind] || page.kind }}</el-tag>
              </div>
            </template>

            <!-- 内嵌 ego 关系图 -->
            <div v-if="showMiniGraph" class="ib-section">
              <div class="ib-label">关系图谱</div>
              <p class="ib-hint">{{ graphHint }}</p>
              <MiniEgoGraph
                :nodes="miniGraphNodes"
                :edges="miniGraphEdges"
                :height="220"
                @node-click="onMiniNodeClick"
              />
              <div v-if="showGraphLimit" class="graph-limit">
                <div class="graph-limit-row">
                  <span>显示节点</span>
                  <span class="graph-limit-count">{{ effectiveGraphLimit }} / {{ miniNeighborCount }}</span>
                </div>
                <el-slider
                  v-model="graphLimit"
                  :min="8"
                  :max="miniNeighborCount"
                  :step="1"
                  size="small"
                  :show-tooltip="false"
                />
              </div>
              <div class="graph-legend">
                <span class="lg-item"><i class="lg-line solid" /> 语义关系</span>
                <span class="lg-item"><i class="lg-line dashed" /> 归属/生成</span>
              </div>
            </div>

            <!-- 下层节点:相关实体 / 包含实体 / 衍生主题 -->
            <div v-if="relations?.related_entities?.length" class="ib-section">
              <div class="ib-label">
                {{ relatedLabel }}
                <span class="ib-count">{{ relations.related_entities.length }}</span>
              </div>
              <div class="rel-chips">
                <a
                  v-for="r in relations.related_entities"
                  :key="r.slug"
                  class="rel-chip"
                  :class="{ missing: !r.exists }"
                  @click.prevent="r.exists && gotoPage(r.slug, r.kind)"
                >
                  <span class="rel-name">{{ r.title || r.slug }}</span>
                </a>
              </div>
            </div>

            <!-- 被哪些主题提及(可展开看主题来自哪些素材)-->
            <div v-if="relations?.mentioned_by_topics?.length" class="ib-section">
              <div class="ib-label">
                被主题提及
                <span class="ib-count">{{ relations.mentioned_by_topics.length }}</span>
              </div>
              <el-collapse class="topic-collapse">
                <el-collapse-item
                  v-for="t in relations.mentioned_by_topics"
                  :key="t.slug"
                  :name="t.slug"
                >
                  <template #title>
                    <a class="topic-title" @click.stop.prevent="gotoPage(t.slug, 'topic')">{{ t.title || t.slug }}</a>
                  </template>
                  <div v-if="t.sources?.length" class="src-list">
                    <div class="src-sub">来自素材</div>
                    <a
                      v-for="s in t.sources"
                      :key="s.slug"
                      class="src-item"
                      :class="{ missing: !s.exists }"
                      @click.prevent="s.exists && gotoPage(s.slug, 'source')"
                    >
                      {{ s.title || s.slug }}
                      <span v-if="s.book" class="src-book">{{ s.book }}</span>
                    </a>
                  </div>
                  <el-empty v-else :image-size="28" description="无关联素材" />
                </el-collapse-item>
              </el-collapse>
            </div>

            <!-- 本页直接来源素材 -->
            <div v-if="relations?.sources?.length" class="ib-section">
              <div class="ib-label">
                来源素材
                <span class="ib-count">{{ relations.sources.length }}</span>
              </div>
              <a
                v-for="s in relations.sources"
                :key="s.slug"
                class="src-item"
                :class="{ missing: !s.exists }"
                @click.prevent="s.exists && gotoPage(s.slug, 'source')"
              >
                {{ s.title || s.slug }}
                <span v-if="s.book" class="src-book">{{ s.book }}</span>
              </a>
            </div>

            <el-empty
              v-if="!relationsLoading && !hasRelations && !showMiniGraph"
              :image-size="40"
              description="暂无关系数据"
            />
          </el-card>

          <!-- 反向链接(保留,补充信息)-->
          <el-card v-if="page.backlinks?.length" shadow="never" style="margin-top:12px">
            <template #header><strong>反向链接</strong></template>
            <ul class="backlink-list">
              <li
                v-for="b in page.backlinks"
                :key="b.slug"
                @click="gotoPage(b.slug)"
              >{{ b.title || b.slug }}</li>
            </ul>
          </el-card>
        </el-col>
      </el-row>
    </template>
    </div>
    <SidePanel
      v-if="imagePanel.open"
      mode="image"
      :image="imagePanel.image"
      @close="closeWikiImage"
    />
  </div>
</template>

<style scoped>
.wiki-shell.has-image-panel {
  display: flex;
  align-items: stretch;
  gap: 0;
}
.wiki-main { min-width: 0; flex: 1; }
.wiki-topbar { margin-bottom: 14px; max-width: 640px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 12px; }
.back-btn { padding: 0; margin-bottom: 4px; color: var(--text-light); }
.back-btn:hover { color: var(--primary-color); }
.muted-meta { color: var(--text-light); font-size: 12px; margin-left: 4px; }
.link-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.wiki-chip {
  padding: 4px 10px;
  border-radius: 4px;
  background: #fff7ed;
  color: var(--primary-color);
  font-size: 13px;
  cursor: pointer;
  border: 1px solid transparent;
}
.wiki-chip:hover { border-color: var(--primary-color); }
.frontmatter-pre {
  background: #f8fafc;
  padding: 8px;
  border-radius: 4px;
  font-size: 12px;
  margin: 0;
  overflow-x: auto;
}
.backlink-list { list-style: none; padding: 0; margin: 0; }
.backlink-list li {
  padding: 6px 0;
  cursor: pointer;
  color: var(--text-main);
  border-bottom: 1px dashed var(--border-color);
  font-size: 13px;
}
.backlink-list li:hover { color: var(--primary-color); }

/* 编辑器 */
.editor-card { padding-top: 4px; }
.fm-form { margin-bottom: 12px; }
.editor-split {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
@media (max-width: 900px) {
  .editor-split { grid-template-columns: 1fr; }
}
.editor-pane, .preview-pane {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 6px;
  overflow: hidden;
}
.pane-label {
  font-size: 12px;
  color: var(--text-light, #64748b);
  padding: 6px 10px;
  background: #f8fafc;
  border-bottom: 1px solid var(--border-color, #e5e7eb);
  display: flex;
  align-items: center;
  gap: 6px;
}
.md-textarea :deep(.el-textarea__inner) {
  border: 0;
  border-radius: 0;
  font-family: "JetBrains Mono", ui-monospace, "SFMono-Regular", Consolas, monospace;
  font-size: 13px;
  line-height: 1.65;
  padding: 12px 14px;
  min-height: 480px;
}
.citation-sources {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #e5e7eb;
}
.citation-sources-label {
  margin-bottom: 6px;
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}
.citation-sources ol {
  display: grid;
  gap: 3px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.citation-sources li { min-width: 0; }
.citation-sources a {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  align-items: start;
  gap: 6px;
  padding: 3px 0;
  color: #334155;
  line-height: 1.45;
  text-decoration: none;
}
.citation-sources a:hover .citation-title {
  color: #2563eb;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.citation-number {
  width: 20px;
  color: #64748b;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.citation-title {
  min-width: 0;
  overflow-wrap: anywhere;
}
.preview-body {
  padding: 12px 14px;
  min-height: 480px;
  max-height: 70vh;
  overflow-y: auto;
  background: #ffffff;
}

/* 百科信息框 */
.infobox :deep(.el-card__header) { padding: 12px 14px; background: #fffaf4; }
.infobox-head { display: flex; align-items: center; gap: 8px; }
.ib-dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.ib-section { margin-bottom: 16px; }
.ib-section:last-child { margin-bottom: 0; }
.ib-label {
  display: flex;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-light, #64748b);
  letter-spacing: 0.02em;
  margin-bottom: 8px;
}
.ib-count {
  margin-left: 6px;
  font-weight: 400;
  color: var(--text-light, #94a3b8);
}
.ib-hint {
  margin: 0 0 8px;
  font-size: 11.5px;
  color: var(--text-light, #94a3b8);
  line-height: 1.4;
}
.graph-limit { margin-top: 8px; }
.graph-limit-row {
  display: flex;
  justify-content: space-between;
  font-size: 11.5px;
  color: var(--text-light, #94a3b8);
  margin-bottom: 2px;
}
.graph-limit-count { font-variant-numeric: tabular-nums; }
.graph-limit :deep(.el-slider) { padding: 0 6px; }
.graph-legend {
  display: flex;
  gap: 14px;
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-light, #94a3b8);
}
.lg-item { display: inline-flex; align-items: center; gap: 5px; }
.lg-line { display: inline-block; width: 16px; height: 0; border-top-width: 2px; }
.lg-line.solid { border-top: 2px solid #2563eb; }
.lg-line.dashed { border-top: 2px dashed #94a3b8; }
.rel-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.rel-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 9px;
  border-radius: 999px;
  background: #fff7ed;
  border: 1px solid #fde7cf;
  font-size: 13px;
  cursor: pointer;
  color: var(--text-main, #1f2937);
  transition: border-color 0.15s ease;
}
.rel-chip:hover { border-color: var(--primary-color, #f97316); }
.rel-chip.missing { opacity: 0.5; cursor: default; background: #f8fafc; border-color: #e5e7eb; }
.rel-type {
  font-size: 11px;
  color: var(--primary-color, #f97316);
  background: #fff;
  border-radius: 4px;
  padding: 0 5px;
}
.topic-collapse :deep(.el-collapse-item__header) { font-size: 13px; height: 36px; line-height: 36px; }
.topic-title { color: var(--text-main, #1f2937); cursor: pointer; }
.topic-title:hover { color: var(--primary-color, #f97316); }
.src-list { padding: 2px 0; }
.src-sub { font-size: 11px; color: var(--text-light, #94a3b8); margin-bottom: 4px; }
.src-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 8px;
  border-radius: 5px;
  font-size: 13px;
  cursor: pointer;
  color: var(--text-main, #1f2937);
}
.src-item:hover { background: #fff7ed; color: var(--primary-color, #f97316); }
.src-item.missing { opacity: 0.5; cursor: default; }
.src-item.missing:hover { background: transparent; color: var(--text-main, #1f2937); }
.src-book {
  font-size: 11px;
  color: var(--text-light, #94a3b8);
  background: #f1f5f9;
  border-radius: 4px;
  padding: 1px 6px;
  white-space: nowrap;
}
@media (prefers-reduced-motion: reduce) {
  .rel-chip { transition: none; }
}

/* chunk 引用定位高亮(6s 后移除 class 淡出) */
:deep(.cite-hl) {
  background: linear-gradient(transparent 20%, #fef08a 20%, #fef08a 90%, transparent 90%);
  border-radius: 3px;
  transition: background 1.2s ease;
  scroll-margin-top: 80px;
}
</style>
