<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { Download, Refresh } from "@element-plus/icons-vue";
import { qaDataApi } from "@/api";

const router = useRouter();

const loading = ref(false);
const items = ref([]);
const counts = ref({ query: 0, synthesis: 0, comparison: 0 });
const tierFilter = ref(""); // "" | gold | silver | bronze

const KIND_LABEL = {
  query: "提问",
  synthesis: "综合",
  comparison: "对比",
};

const filteredItems = computed(() =>
  tierFilter.value
    ? items.value.filter((it) => it.tier === tierFilter.value)
    : items.value
);

const totals = computed(() => ({
  total: items.value.length,
  ...counts.value,
}));

async function load() {
  loading.value = true;
  try {
    const data = await qaDataApi.list();
    const body = data?.data || data || {};
    items.value = body.items || [];
    counts.value = {
      query: body.counts_by_kind?.query || 0,
      synthesis: body.counts_by_kind?.synthesis || 0,
      comparison: body.counts_by_kind?.comparison || 0,
    };
  } catch (e) {
    ElMessage.error("问答数据加载失败:" + (e.response?.data?.detail || e.message));
    items.value = [];
  } finally {
    loading.value = false;
  }
}

function rowClick(row) {
  router.push(`/kb/wiki/${encodeURIComponent(row.slug)}`);
}

function openSession(sessionId) {
  router.push({ path: "/kb/query", query: { session: String(sessionId) } });
}

function formatDate(s) {
  if (!s) return "";
  try {
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return s;
    return d.toLocaleString();
  } catch {
    return s;
  }
}

// 前端本地 CSV 导出(按当前评分筛选的列表)
function csvEscape(v) {
  if (v == null) return "";
  const s = String(v);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function exportCsv() {
  const rows = filteredItems.value;
  if (!rows.length) {
    ElMessage.warning("当前列表为空");
    return;
  }
  const header = [
    "kind",
    "slug",
    "title",
    "user_question",
    "asked_by_username",
    "created",
    "citation_count",
    "answer_quality",
    "tier",
    "gap_flag",
  ];
  const lines = [header.join(",")];
  for (const r of rows) {
    lines.push([
      KIND_LABEL[r.kind] || r.kind,
      r.slug,
      r.title,
      r.user_question || "",
      r.asked_by_username ?? r.asked_by ?? "",
      r.created || "",
      r.citation_count ?? 0,
      r.answer_quality ?? "",
      r.tier || "",
      r.gap_flag == null ? "" : r.gap_flag,
    ].map(csvEscape).join(","));
  }
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const stamp = new Date().toISOString().slice(0, 10);
  a.download = `qa-data-${tierFilter.value || "all"}-${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

onMounted(load);
</script>

<template>
  <div class="qa-data-page">
    <header class="qd-header">
      <div>
        <h1 class="qd-title">问答数据</h1>
        <p class="qd-subtitle">
          全部用户提问、系统综合、对比分析记录,用于后续数据分析。共
          <b>{{ totals.total }}</b> 条
          (提问 {{ totals.query }} / 综合 {{ totals.synthesis }} / 对比 {{ totals.comparison }})。
        </p>
        <p class="qd-score-note">
          评分依据：质量分(0~1)由五维加权得到——证据对齐度(40%,答案与引用素材的匹配程度)、
          来源充分性(20%,独立引用≥3个即满分)、问题覆盖率(20%,答案覆盖提问要点的程度)、
          素材置信度(10%,引用来源平均置信度)、引用有效性(10%,引用链接有效比例)。
          分数 ≥0.80 为金(gold)、≥0.60 为银(silver)、其余为铜(bronze)。
          评分 <0.60、引用来源 <2、覆盖率 <0.50、对齐度 <0.50 或存在无引用支撑的断言时,该条视为知识缺口。
        </p>
      </div>
      <div class="qd-actions">
        <el-radio-group v-model="tierFilter" size="small">
          <el-radio-button label="">全部</el-radio-button>
          <el-radio-button label="gold">金</el-radio-button>
          <el-radio-button label="silver">银</el-radio-button>
          <el-radio-button label="bronze">铜</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Download" @click="exportCsv">导出 CSV</el-button>
      </div>
    </header>

    <el-table
      v-loading="loading"
      :data="filteredItems"
      stripe
      class="qd-table"
      @row-click="rowClick"
      style="width: 100%"
    >
      <el-table-column label="问题 / 标题" min-width="280">
        <template #default="{ row }">
          <div class="qd-q">{{ row.user_question || row.title }}</div>
          <div class="qd-q-slug">
            {{ row.slug }}
            <a
              v-if="row.session_id"
              class="qd-session"
              @click.stop="openSession(row.session_id)"
            >来自会话 #{{ row.session_id }}</a>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="提问人" width="100">
        <template #default="{ row }">
          <span v-if="row.asked_by_username">{{ row.asked_by_username }}</span>
          <span v-else-if="row.asked_by">#{{ row.asked_by }}</span>
          <span v-else class="qd-system">系统</span>
        </template>
      </el-table-column>

      <el-table-column label="时间" width="170">
        <template #default="{ row }">
          <span class="qd-date">{{ formatDate(row.created) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="引用" width="80" align="center">
        <template #default="{ row }">
          <span class="qd-mono">{{ row.citation_count || 0 }}</span>
        </template>
      </el-table-column>

      <el-table-column label="质量" width="120">
        <template #default="{ row }">
          <span v-if="row.answer_quality != null" class="qd-quality">
            <span class="qd-mono">{{ Number(row.answer_quality).toFixed(2) }}</span>
            <el-tag v-if="row.tier" size="small" effect="plain" style="margin-left: 4px">
              {{ row.tier }}
            </el-tag>
          </span>
          <span v-else class="qd-empty-cell">—</span>
        </template>
      </el-table-column>

    </el-table>
  </div>
</template>

<style scoped>
.qa-data-page {
  max-width: 1280px;
  margin: 0 auto;
}
.qd-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid #e2e8f0;
}
.qd-title {
  margin: 0;
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: 20px;
  font-weight: 600;
  color: #0f172a;
}
.qd-subtitle {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: #64748b;
}
.qd-subtitle b { color: #0f172a; }
.qd-score-note {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: #8b94a3;
  max-width: 760px;
}
.qd-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.qd-table {
  cursor: pointer;
}
.qd-table :deep(.el-table__row):hover {
  background: #fff7ed !important;
}

.qd-q {
  font-size: 13px;
  color: #0f172a;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.qd-session {
  margin-left: 8px;
  color: #2563eb;
  cursor: pointer;
  text-decoration: none;
}
.qd-session:hover { text-decoration: underline; }
.qd-q-slug {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
}
.qd-mono {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 12.5px;
  color: #0f172a;
}
.qd-date {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 12px;
  color: #64748b;
}
.qd-quality {
  display: inline-flex;
  align-items: center;
}
.qd-system {
  font-size: 12px;
  color: #94a3b8;
}
.qd-empty-cell {
  color: #cbd5e1;
}
</style>
