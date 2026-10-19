<script setup>
// 学习路线图:地铁线风格的书籍脉络。
// 一条纵向主线自上而下贯穿全书,章节是主线上的大站(按阅读顺序编号),
// 每站右侧挂该章的主题"支线小站"。点主题开抽屉看实体;浏览过的页面亮起(本机足迹)。
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { Check, Right } from "@element-plus/icons-vue";
import { getVisitedSet } from "@/utils/visited";

const props = defineProps({
  book: { type: String, required: true },
  chapters: { type: Array, default: () => [] },
});

const router = useRouter();
const visited = ref(getVisitedSet());

// 抽屉:当前选中的主题
const drawerOpen = ref(false);
const activeTopic = ref(null);
const activeChapter = ref(null);

function cleanTitle(s) {
  return String(s || "").replace(/^\d{4}-\d{2}-\d{2}-/, "");
}

const stations = computed(() =>
  (props.chapters || []).map((c, idx) => {
    const topics = (c.topics || []).map((t) => ({
      ...t,
      visited: visited.value.has(t.slug),
      entityCount: (t.entities || []).length,
    }));
    if ((c.orphan_entities || []).length) {
      topics.push({
        slug: `__orphan_${c.slug}`,
        title: "未归类实体",
        orphan: true,
        visited: false,
        entities: c.orphan_entities,
        entityCount: c.orphan_entities.length,
      });
    }
    return {
      ...c,
      no: idx + 1,
      cleanTitle: cleanTitle(c.title),
      visited: visited.value.has(c.slug),
      topics,
    };
  })
);

const progress = computed(() => {
  const total = stations.value.length;
  const done = stations.value.filter((s) => s.visited).length;
  return { total, done, pct: total ? Math.round((done / total) * 100) : 0 };
});

function refreshVisited() {
  visited.value = getVisitedSet();
}

function gotoPage(slug, kind) {
  if (!slug) return;
  router.push({
    path: `/kb/wiki/${encodeURIComponent(slug)}`,
    query: kind ? { kind } : {},
  });
}

function openTopic(chapter, topic) {
  activeChapter.value = chapter;
  activeTopic.value = topic;
  drawerOpen.value = true;
}

function isEntityVisited(slug) {
  return visited.value.has(slug);
}
</script>

<template>
  <div class="roadmap" @focusin="refreshVisited">
    <!-- 顶部学习进度 -->
    <div class="rm-progress">
      <div class="rm-progress-text">
        学习进度:已浏览 <b>{{ progress.done }}</b> / {{ progress.total }} 章
        <span class="rm-progress-hint">(以本机浏览记录统计,打开章节页即点亮)</span>
      </div>
      <div class="rm-progress-bar">
        <div class="rm-progress-fill" :style="{ width: progress.pct + '%' }" />
      </div>
    </div>

    <el-empty v-if="!stations.length" description="暂无章节" />

    <!-- 主线 + 站点 -->
    <div v-else class="rm-line">
      <div v-for="s in stations" :key="s.slug" class="rm-station">
        <!-- 左:主线节点 -->
        <div class="rm-node-col">
          <button
            class="rm-node"
            :class="{ visited: s.visited }"
            :title="s.visited ? '已浏览,点击进入章节页' : '点击进入章节页'"
            @click="gotoPage(s.slug, 'source')"
          >
            <el-icon v-if="s.visited"><Check /></el-icon>
            <template v-else>{{ s.no }}</template>
          </button>
        </div>

        <!-- 右:章节卡 + 主题支线 -->
        <div class="rm-body">
          <div class="rm-chapter" @click="gotoPage(s.slug, 'source')">
            <span class="rm-chapter-title">{{ s.cleanTitle }}</span>
            <span class="rm-chapter-meta">
              {{ s.topics.length }} 主题
            </span>
            <el-icon class="rm-chapter-go"><Right /></el-icon>
          </div>

          <div v-if="s.topics.length" class="rm-topics">
            <button
              v-for="t in s.topics"
              :key="t.slug"
              class="rm-topic"
              :class="{ visited: t.visited, orphan: t.orphan }"
              @click="openTopic(s, t)"
            >
              <span class="rm-topic-dot" />
              <span class="rm-topic-name">{{ t.title }}</span>
              <span class="rm-topic-count">{{ t.entityCount }}</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 终点站 -->
      <div class="rm-terminal">
        <span class="rm-terminal-dot" :class="{ done: progress.done === progress.total && progress.total > 0 }" />
        <span class="rm-terminal-text">
          {{ progress.done === progress.total && progress.total > 0 ? "全书浏览完成 🎓" : "全书终点" }}
        </span>
      </div>
    </div>

    <!-- 主题抽屉:实体列表 -->
    <el-drawer
      v-model="drawerOpen"
      :title="activeTopic?.title || ''"
      size="380px"
      :append-to-body="true"
    >
      <template #header>
        <div class="rm-drawer-head">
          <div class="rm-drawer-title">{{ activeTopic?.title }}</div>
          <div class="rm-drawer-sub" v-if="activeChapter">
            {{ activeChapter.cleanTitle }}
          </div>
        </div>
      </template>
      <div v-if="activeTopic" class="rm-drawer-body">
        <el-button
          v-if="!activeTopic.orphan"
          type="primary"
          plain
          size="small"
          style="margin-bottom: 14px"
          @click="gotoPage(activeTopic.slug, 'topic'); drawerOpen = false"
        >打开主题页 →</el-button>

        <div class="rm-drawer-label">
          {{ activeTopic.orphan ? "本章未归类实体" : "包含实体" }}
          <span class="rm-drawer-count">{{ (activeTopic.entities || []).length }}</span>
        </div>
        <div class="rm-entity-chips">
          <a
            v-for="e in activeTopic.entities || []"
            :key="e.slug"
            class="rm-entity-chip"
            :class="{ missing: e.exists === false, visited: isEntityVisited(e.slug) }"
            @click.prevent="e.exists !== false && (gotoPage(e.slug, 'entity'), drawerOpen = false)"
          >
            <el-icon v-if="isEntityVisited(e.slug)" class="rm-chip-check"><Check /></el-icon>
            {{ e.title || e.slug }}
          </a>
        </div>
        <el-empty
          v-if="!(activeTopic.entities || []).length"
          :image-size="48"
          description="该主题暂无实体"
        />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.roadmap {
  max-width: 860px;
  margin: 0 auto;
  padding: 4px 8px 40px;
}

/* 进度条 */
.rm-progress {
  padding: 14px 16px;
  background: linear-gradient(135deg, #fff7ed 0%, #fff 70%);
  border: 1px solid #fed7aa;
  border-radius: 10px;
  margin-bottom: 26px;
}
.rm-progress-text {
  font-size: 13px;
  color: #475569;
  margin-bottom: 8px;
}
.rm-progress-text b { color: #f97316; font-size: 15px; }
.rm-progress-hint { color: #94a3b8; font-size: 11.5px; margin-left: 6px; }
.rm-progress-bar {
  height: 6px;
  border-radius: 999px;
  background: #f1f5f9;
  overflow: hidden;
}
.rm-progress-fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #fb923c, #f97316);
  transition: width 0.4s ease;
}

/* 主线 */
.rm-line {
  position: relative;
  padding-left: 8px;
}
/* 纵向地铁线 */
.rm-line::before {
  content: "";
  position: absolute;
  left: 27px;
  top: 8px;
  bottom: 30px;
  width: 4px;
  border-radius: 2px;
  background: linear-gradient(180deg, #fdba74, #f97316);
}

.rm-station {
  position: relative;
  display: flex;
  gap: 18px;
  margin-bottom: 26px;
}
.rm-node-col {
  flex-shrink: 0;
  width: 40px;
  display: flex;
  justify-content: center;
}
.rm-node {
  position: relative;
  z-index: 1;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 3px solid #f97316;
  background: #fff;
  color: #f97316;
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 15px;
  font-weight: 700;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.rm-node:hover {
  transform: scale(1.08);
  box-shadow: 0 4px 14px rgba(249, 115, 22, 0.3);
}
.rm-node.visited {
  background: #f97316;
  color: #fff;
}

.rm-body { flex: 1; min-width: 0; padding-top: 2px; }

.rm-chapter {
  display: flex;
  align-items: baseline;
  gap: 10px;
  cursor: pointer;
  padding: 6px 10px;
  margin-left: -10px;
  border-radius: 8px;
  transition: background 0.15s ease;
}
.rm-chapter:hover { background: #fff7ed; }
.rm-chapter-title {
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: 16.5px;
  font-weight: 600;
  color: #0f172a;
}
.rm-chapter:hover .rm-chapter-title { color: #ea580c; }
.rm-chapter-meta {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 11.5px;
  color: #94a3b8;
}
.rm-chapter-go {
  color: #cbd5e1;
  transition: transform 0.15s ease, color 0.15s ease;
  align-self: center;
}
.rm-chapter:hover .rm-chapter-go { color: #f97316; transform: translateX(3px); }

/* 主题支线 */
.rm-topics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.rm-topic {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 1px solid #dbeafe;
  background: #eff6ff;
  color: #1e40af;
  border-radius: 999px;
  padding: 5px 12px 5px 9px;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.rm-topic:hover {
  border-color: #2563eb;
  box-shadow: 0 2px 8px rgba(37, 99, 235, 0.15);
}
.rm-topic-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 2px solid #2563eb;
  background: #fff;
  flex-shrink: 0;
}
.rm-topic.visited .rm-topic-dot { background: #2563eb; }
.rm-topic.visited { background: #dbeafe; }
.rm-topic-name {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rm-topic-count {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 10.5px;
  color: #60a5fa;
}
.rm-topic.orphan {
  border-color: #e2e8f0;
  background: #f8fafc;
  color: #64748b;
  font-style: italic;
}
.rm-topic.orphan .rm-topic-dot { border-color: #94a3b8; }
.rm-topic.orphan .rm-topic-count { color: #94a3b8; }

/* 终点 */
.rm-terminal {
  display: flex;
  align-items: center;
  gap: 18px;
  padding-left: 8px;
}
.rm-terminal-dot {
  position: relative;
  z-index: 1;
  width: 18px;
  height: 18px;
  margin-left: 11px;
  border-radius: 50%;
  border: 4px solid #fdba74;
  background: #fff;
  flex-shrink: 0;
}
.rm-terminal-dot.done { border-color: #f97316; background: #f97316; }
.rm-terminal-text {
  font-size: 13px;
  color: #94a3b8;
  font-family: "Noto Serif SC", "Songti SC", serif;
}

/* 抽屉 */
.rm-drawer-head { line-height: 1.4; }
.rm-drawer-title {
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: 16px;
  font-weight: 600;
  color: #0f172a;
}
.rm-drawer-sub { font-size: 12px; color: #94a3b8; margin-top: 2px; }
.rm-drawer-label {
  display: flex;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
  margin-bottom: 10px;
}
.rm-drawer-count { margin-left: 6px; font-weight: 400; color: #94a3b8; }
.rm-entity-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.rm-entity-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 999px;
  background: #fff7ed;
  border: 1px solid #fde7cf;
  font-size: 13px;
  cursor: pointer;
  color: #1f2937;
  transition: border-color 0.15s ease;
}
.rm-entity-chip:hover { border-color: #f97316; }
.rm-entity-chip.visited { background: #ffedd5; }
.rm-entity-chip.missing { opacity: 0.5; cursor: default; }
.rm-chip-check { font-size: 12px; color: #f97316; }

@media (prefers-reduced-motion: reduce) {
  .rm-node, .rm-topic, .rm-chapter-go, .rm-progress-fill { transition: none; }
}
</style>
