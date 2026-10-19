<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Search, Loading } from "@element-plus/icons-vue";
import { wikiApi } from "@/api";
import { KIND_LABELS } from "@/constants/graphTheme";
import { colorOf } from "@/constants/typeColors";

const props = defineProps({
  placeholder: { type: String, default: "搜索实体 / 主题 / 素材…" },
  // compact: 嵌在页头时更紧凑
  compact: { type: Boolean, default: false },
});

const router = useRouter();
const keyword = ref("");
const results = ref([]);
const loading = ref(false);
const open = ref(false);
const activeIndex = ref(-1);
const rootEl = ref(null);

let debounceTimer = null;
let reqSeq = 0;

const KIND_ORDER = ["entity", "topic", "source", "synthesis", "comparison", "query"];

// 按 kind 分组,保留后端排序;每组内顺序不打乱
const grouped = computed(() => {
  const by = {};
  for (const it of results.value) {
    (by[it.kind] ||= []).push(it);
  }
  return KIND_ORDER.filter((k) => by[k]?.length).map((k) => ({
    kind: k,
    label: KIND_LABELS[k] || k,
    items: by[k],
  }));
});

// 扁平列表(用于键盘上下导航,与渲染顺序一致)
const flat = computed(() => grouped.value.flatMap((g) => g.items));

function doSearch(q) {
  const seq = ++reqSeq;
  loading.value = true;
  wikiApi
    .search(q, null, 20)
    .then((data) => {
      if (seq !== reqSeq) return; // 丢弃过期响应
      results.value = data?.items || [];
      activeIndex.value = results.value.length ? 0 : -1;
      open.value = true;
    })
    .catch(() => {
      if (seq !== reqSeq) return;
      results.value = [];
    })
    .finally(() => {
      if (seq === reqSeq) loading.value = false;
    });
}

watch(keyword, (val) => {
  clearTimeout(debounceTimer);
  const q = val.trim();
  if (!q) {
    results.value = [];
    open.value = false;
    loading.value = false;
    activeIndex.value = -1;
    return;
  }
  debounceTimer = setTimeout(() => doSearch(q), 250);
});

function go(item) {
  if (!item) return;
  open.value = false;
  keyword.value = "";
  results.value = [];
  router.push({
    path: `/kb/wiki/${encodeURIComponent(item.slug)}`,
    query: item.kind ? { kind: item.kind } : {},
  });
}

function onEnter() {
  if (activeIndex.value >= 0 && flat.value[activeIndex.value]) {
    go(flat.value[activeIndex.value]);
  } else if (flat.value.length) {
    go(flat.value[0]);
  }
}

function move(delta) {
  if (!flat.value.length) return;
  open.value = true;
  const n = flat.value.length;
  activeIndex.value = (activeIndex.value + delta + n) % n;
}

function onFocus() {
  if (results.value.length) open.value = true;
}

function isActive(item) {
  return flat.value[activeIndex.value] === item;
}

function onClickOutside(e) {
  if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false;
}
document.addEventListener("click", onClickOutside);
onBeforeUnmount(() => {
  document.removeEventListener("click", onClickOutside);
  clearTimeout(debounceTimer);
});
</script>

<template>
  <div ref="rootEl" class="wiki-search" :class="{ compact }">
    <el-input
      v-model="keyword"
      :placeholder="placeholder"
      :prefix-icon="Search"
      clearable
      @focus="onFocus"
      @keydown.down.prevent="move(1)"
      @keydown.up.prevent="move(-1)"
      @keydown.enter.prevent="onEnter"
      @keydown.esc="open = false"
    >
      <template #suffix>
        <el-icon v-if="loading" class="is-loading spin"><Loading /></el-icon>
      </template>
    </el-input>

    <div v-if="open" class="dropdown" role="listbox">
      <div v-if="!flat.length && !loading" class="empty-hint">
        未找到匹配的页面
      </div>
      <template v-else>
        <div v-for="g in grouped" :key="g.kind" class="group">
          <div class="group-head">
            <span class="group-dot" :style="{ background: colorOf(g.kind) }" />
            {{ g.label }}
            <span class="group-count">{{ g.items.length }}</span>
          </div>
          <a
            v-for="item in g.items"
            :key="item.slug"
            class="result-row"
            :class="{ active: isActive(item) }"
            role="option"
            @click.prevent="go(item)"
            @mouseenter="activeIndex = flat.indexOf(item)"
          >
            <span class="result-title">{{ item.title || item.slug }}</span>
            <span v-if="item.alias_hit" class="alias-hit">别名 “{{ item.alias_hit }}”</span>
          </a>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.wiki-search { position: relative; width: 100%; }
.wiki-search.compact :deep(.el-input__wrapper) { border-radius: 999px; }
.spin { animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .spin { animation: none; }
}
.dropdown {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  max-height: 60vh;
  overflow-y: auto;
  background: #fff;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 10px;
  box-shadow: 0 8px 28px rgba(15, 23, 42, 0.12);
  z-index: 2200;
  padding: 6px;
}
.empty-hint {
  padding: 18px 12px;
  text-align: center;
  color: var(--text-light, #94a3b8);
  font-size: 13px;
}
.group { padding: 2px 0 6px; }
.group-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-light, #64748b);
  letter-spacing: 0.02em;
}
.group-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.group-count {
  margin-left: auto;
  font-weight: 400;
  color: var(--text-light, #94a3b8);
}
.result-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--text-main, #1f2937);
  text-decoration: none;
}
.result-row.active { background: #fff7ed; }
.result-title { font-size: 14px; }
.result-row.active .result-title { color: var(--primary-color, #f97316); }
.alias-hit {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-light, #94a3b8);
  white-space: nowrap;
}
</style>
