<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { Reading, Notebook, Share, Compass, Plus } from "@element-plus/icons-vue";
import { wikiApi } from "@/api";
import { useAuthStore } from "@/stores/auth";
import WikiSearchBar from "@/components/WikiSearchBar.vue";

const router = useRouter();
const auth = useAuthStore();

const loading = ref(false);
const indexData = ref({ books: [], totals: {} });

// 已知的 5 张固定卡片(按目标顺序),后端返回的顺序按章节数 desc(已与计划一致)。
// 若后端将来新增 book,卡片会自动出现。
const FALLBACK_BOOKS = [
  "基础工业工程",
  "生产计划与管理",
  "精益生产",
  "质量管理",
  "基础资料",
];

const visibleBooks = computed(() => {
  const list = indexData.value?.books || [];
  // 只显示有章节的 + "基础资料"(即使章节=1 也显示),"未分类" 不展示卡片
  return list.filter((b) => b.book !== "未分类" && (b.chapter_count > 0 || b.topic_count > 0 || b.entity_count > 0));
});

const totals = computed(() => indexData.value?.totals || { books: 0, chapters: 0, topics: 0, entities: 0 });

// 根据 book 名取一段简介(纯展示用,失败也无所谓)
const BOOK_BLURBS = {
  "基础工业工程": "工作研究 · 时间研究 · 标准作业 · 现场改善",
  "生产计划与管理": "MPS · MRP · 综合计划 · 库存与作业控制",
  "精益生产": "JIT · 看板 · 价值流图 · TPM · 自働化",
  "质量管理": "TQM · 六西格玛 · SPC · QFD · ISO 9000",
  "基础资料": "通用基础材料 · 行业概览",
};

function blurbFor(book) {
  return BOOK_BLURBS[book] || "知识图谱与结构化笔记";
}

function gotoBook(book) {
  router.push(`/kb/wiki/book/${encodeURIComponent(book)}`);
}

async function createBook() {
  try {
    const { value } = await ElMessageBox.prompt("请输入新知识库的名称", "新建知识库", {
      confirmButtonText: "创建",
      cancelButtonText: "取消",
      inputPattern: /\S+/,
      inputErrorMessage: "名称不能为空",
    });
    const name = (value || "").trim();
    if (!name) return;
    const res = await wikiApi.createBook(name);
    const body = res?.data || res || {};
    if (body.created === false) {
      ElMessage.warning(`「${name}」已存在`);
    } else {
      ElMessage.success(`已创建「${name}」`);
    }
    await load();
    router.push(`/kb/wiki/book/${encodeURIComponent(name)}`);
  } catch (e) {
    if (e === "cancel" || e === "close") return;
    ElMessage.error("创建失败:" + (e.response?.data?.detail || e.message));
  }
}

async function load() {
  loading.value = true;
  try {
    const data = await wikiApi.books();
    indexData.value = data?.data || data || { books: [], totals: {} };
  } catch (e) {
    ElMessage.error("Wiki 概览加载失败:" + (e.response?.data?.detail || e.message));
    indexData.value = { books: [], totals: {} };
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="wiki-home">
    <!-- 全局搜索:实体 / 主题 / 素材 -->
    <div class="wh-search">
      <WikiSearchBar placeholder="搜索实体 / 主题 / 素材…" />
    </div>

    <!-- 顶部统计条 -->
    <header class="wh-stats" v-loading="loading">
      <div class="wh-title">
        <h1>知识库浏览</h1>
        <p class="wh-subtitle">按书架结构浏览章节、主题与实体节点,或在每本书内查看专属知识图谱</p>
      </div>
      <div class="wh-header-right">
        <el-button
          v-if="auth.isAdmin"
          type="primary"
          :icon="Plus"
          class="wh-new-btn"
          @click="createBook"
        >新建知识库</el-button>
        <div class="wh-counts">
        <div class="wh-count-item">
          <div class="wh-count-num">{{ totals.books || 0 }}</div>
          <div class="wh-count-label">本书</div>
        </div>
        <div class="wh-count-item">
          <div class="wh-count-num">{{ totals.chapters || 0 }}</div>
          <div class="wh-count-label">章节</div>
        </div>
        <div class="wh-count-item">
          <div class="wh-count-num">{{ totals.topics || 0 }}</div>
          <div class="wh-count-label">主题</div>
        </div>
        <div class="wh-count-item">
          <div class="wh-count-num">{{ totals.entities || 0 }}</div>
          <div class="wh-count-label">实体</div>
        </div>
        </div>
      </div>
    </header>

    <!-- 5 张大卡片 -->
    <div class="wh-grid">
      <article
        v-for="b in visibleBooks"
        :key="b.book"
        class="wh-card"
        :class="{ 'wh-card-basic': b.book === '基础资料' }"
        @click="gotoBook(b.book)"
        tabindex="0"
        @keyup.enter="gotoBook(b.book)"
      >
        <div class="wh-card-icon">
          <el-icon><Reading v-if="b.book !== '基础资料'" /><Compass v-else /></el-icon>
        </div>
        <div class="wh-card-body">
          <h3 class="wh-card-title">{{ b.book }}</h3>
          <p class="wh-card-blurb">{{ blurbFor(b.book) }}</p>
          <div class="wh-card-stats">
            <span><b>{{ b.chapter_count }}</b> 章</span>
            <span class="wh-card-sep">·</span>
            <span><b>{{ b.topic_count }}</b> 主题</span>
            <span class="wh-card-sep">·</span>
            <span><b>{{ b.entity_count }}</b> 实体</span>
          </div>
        </div>
        <div class="wh-card-arrow">→</div>
      </article>

      <div v-if="!loading && !visibleBooks.length" class="wh-empty">
        暂无消化过的素材。前往「素材管理」上传第一份资料。
      </div>
    </div>
  </div>
</template>

<style scoped>
.wiki-home {
  max-width: 1080px;
  margin: 0 auto;
  padding: 8px 4px 32px;
}

.wh-search {
  max-width: 640px;
  margin: 0 auto 22px;
}

.wh-stats {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 22px 26px;
  background: linear-gradient(135deg, #fff7ed 0%, #fff 60%);
  border: 1px solid #fed7aa;
  border-radius: 12px;
  margin-bottom: 22px;
}
.wh-title h1 {
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: #0f172a;
  margin: 0 0 6px;
}
.wh-subtitle {
  margin: 0;
  font-size: 13px;
  color: #64748b;
  line-height: 1.5;
}
.wh-counts {
  display: flex;
  gap: 28px;
}
.wh-header-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 14px;
}
.wh-new-btn {
  align-self: flex-end;
}
.wh-count-item {
  text-align: right;
}
.wh-count-num {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 22px;
  font-weight: 600;
  color: #f97316;
  line-height: 1.1;
}
.wh-count-label {
  font-size: 11px;
  color: #64748b;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-top: 2px;
}

.wh-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.wh-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 20px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.18s ease;
  outline: none;
}
.wh-card:hover, .wh-card:focus-visible {
  border-color: #f97316;
  box-shadow: 0 6px 20px rgba(249, 115, 22, 0.12);
  transform: translateY(-2px);
}
.wh-card.wh-card-basic {
  background: #fafafa;
}
.wh-card-icon {
  width: 44px; height: 44px;
  flex-shrink: 0;
  border-radius: 8px;
  background: #fff7ed;
  color: #f97316;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
}
.wh-card-basic .wh-card-icon {
  background: #f1f5f9;
  color: #64748b;
}
.wh-card-body {
  flex: 1;
  min-width: 0;
}
.wh-card-title {
  margin: 0 0 4px;
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: 16px;
  font-weight: 600;
  color: #0f172a;
}
.wh-card-blurb {
  margin: 0 0 8px;
  font-size: 12.5px;
  color: #64748b;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wh-card-stats {
  font-size: 12px;
  color: #475569;
  font-family: "JetBrains Mono", ui-monospace, monospace;
}
.wh-card-stats b {
  color: #0f172a;
  font-weight: 600;
}
.wh-card-sep {
  margin: 0 6px;
  color: #cbd5e1;
}
.wh-card-arrow {
  font-size: 18px;
  color: #cbd5e1;
  transition: transform 0.18s, color 0.18s;
  flex-shrink: 0;
}
.wh-card:hover .wh-card-arrow {
  color: #f97316;
  transform: translateX(4px);
}

.wh-empty {
  grid-column: 1 / -1;
  padding: 48px 20px;
  text-align: center;
  color: #94a3b8;
  font-size: 13px;
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
  background: #fafafa;
}

@media (max-width: 640px) {
  .wh-stats {
    flex-direction: column;
    align-items: stretch;
    gap: 14px;
  }
  .wh-counts {
    justify-content: space-between;
    gap: 8px;
  }
  .wh-count-item { text-align: left; }
  .wh-grid {
    grid-template-columns: 1fr;
  }
}
</style>
