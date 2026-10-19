<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  Plus,
  ChatLineRound,
  Edit,
  Delete,
  User,
  MagicStick,
  Promotion,
  CircleClose,
  InfoFilled,
  Refresh,
} from "@element-plus/icons-vue";
import MarkdownViewer from "@/components/MarkdownViewer.vue";
import SidePanel from "@/components/SidePanel.vue";
import { useConversationsStore } from "@/stores/conversations";
import { visibleCitationDetails } from "@/utils/citations";

const route = useRoute();
const router = useRouter();
const store = useConversationsStore();

const question = ref("");
// streaming 直接读 store(全局状态,跨路由保留)
const streaming = computed(() => store.streaming);

const sessions = computed(() => store.sessions);
const currentId = computed(() => store.currentId);
const current = computed(() => store.current);
const messages = computed(() => store.messages);

const messagesEnd = ref(null);
async function scrollToEnd() {
  await nextTick();
  messagesEnd.value?.scrollIntoView({ behavior: "smooth", block: "end" });
}
watch(currentId, () => {
  closeSidePanel();
  scrollToEnd();
});
// 流式 token 追加到 store.messages,组件不一定 deep-watch 到滚动需求,补一个滚动 trigger
watch(
  () => messages.value[messages.value.length - 1]?.content,
  () => { if (streaming.value) scrollToEnd(); }
);

function tierColor(tier) {
  switch (tier) {
    case "gold": return "success";
    case "silver": return "primary";
    case "bronze": return "warning";
    default: return "info";
  }
}
function citationsFor(message) {
  return visibleCitationDetails(message?.content, message?.citations);
}
// 引用列表分组:知识库来源 / 网络来源,编号保持与正文 [n] 一致(全局首现序)
function kbCitationsFor(message) {
  return citationsFor(message)
    .map((c, i) => ({ ...c, number: i + 1 }))
    .filter((c) => c.type !== "web");
}
function webCitationsFor(message) {
  return citationsFor(message)
    .map((c, i) => ({ ...c, number: i + 1 }))
    .filter((c) => c.type === "web");
}
function domainOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (_) {
    return "";
  }
}
function faviconOf(url) {
  const host = domainOf(url);
  return host ? `https://www.google.com/s2/favicons?domain=${host}&sz=32` : "";
}
function wikiHref(slug) {
  return `/kb/wiki/${encodeURIComponent(slug)}`;
}
function gotoCitation(c) {
  if (c.type === "web") {
    if (c.url) window.open(c.url, "_blank", "noopener");
    return;
  }
  const query = {};
  if (c.kind) query.kind = c.kind;
  // chunk 引用 → 带原文片段 + 小节名,wiki 页定位高亮
  if (c.type === "chunk" && c.quote) query.hl = String(c.quote).slice(0, 80);
  if (c.type === "chunk" && c.heading) query.heading = c.heading;
  router.push({ path: `/kb/wiki/${encodeURIComponent(c.slug)}`, query });
}

// ===== 工具状态条(改造计划 §8.3) =====
const TOOL_LABELS = {
  search_kb: { icon: "🔍", running: "正在搜索知识库…", done: "搜索知识库" },
  web_search: { icon: "🌐", running: "正在联网搜索…", done: "联网搜索" },
  generate_image: { icon: "🎨", running: "正在生成示意图…", done: "生成示意图" },
  recommend_chart_templates: { icon: "📊", running: "正在匹配绘图模板…", done: "匹配绘图模板" },
  generate_chart: { icon: "📈", running: "正在绘图平台绘制图表…", done: "绘制图表" },
};
function toolLabel(tool, running) {
  const t = TOOL_LABELS[tool] || { icon: "🔧", running: `正在调用 ${tool}…`, done: tool };
  return `${t.icon} ${running ? t.running : t.done}`;
}
// tool_start/tool_result 事件流配对成时间线:[{tool, reason, status, summary}]
function toolTimeline(message) {
  const timeline = [];
  for (const evt of message?.toolEvents || []) {
    if (evt.type === "tool_start") {
      timeline.push({ tool: evt.tool, reason: evt.reason || "", status: "running", summary: "" });
    } else if (evt.type === "tool_result") {
      const entry = timeline.find((e) => e.tool === evt.tool && e.status === "running");
      if (entry) {
        entry.status = evt.ok === false ? "failed" : "ok";
        entry.summary = evt.summary || "";
      }
    }
  }
  return timeline;
}
function runningTools(message) {
  return toolTimeline(message).filter((e) => e.status === "running");
}
function finishedTools(message) {
  return toolTimeline(message).filter((e) => e.status !== "running");
}
// 流式阶段提示(progress 事件合入 meta.stage)
const STAGE_TEXT = {
  rewrite: "正在理解问题…",
  retrieval: "正在检索知识库…",
  validate: "正在校验引用…",
};
function stageTextFor(message) {
  return STAGE_TEXT[message?.meta?.stage] || "";
}

// ===== 右侧分栏面板 =====
const sidePanel = ref({ open: false, mode: "templates", templates: [], baseUrl: "", image: null });
function openTemplatesPanel(message) {
  sidePanel.value = {
    open: true,
    mode: "templates",
    templates: message.chartTemplates || [],
    baseUrl: message.chartBaseUrl || "",
    image: null,
  };
}
function openImagePanel(message, payload) {
  const info = (message.images || {})[payload?.imageId] || {};
  sidePanel.value = {
    open: true,
    mode: "image",
    templates: [],
    baseUrl: message.chartBaseUrl || "",
    image: {
      url: payload?.url || info.url || "",
      prompt: info.prompt || "",
      caption: info.caption || "",
      chart: info.chart || null,
    },
  };
}
function closeSidePanel() {
  sidePanel.value = { ...sidePanel.value, open: false };
}
function chartTipText(message) {
  const list = message.chartTemplates || [];
  if (!list.length) return "";
  const first = list[0]?.name || "";
  const extra = list.length > 1 ? ` 等 ${list.length} 个` : "";
  return `本方法可用『${first}』${extra}模板绘制，点击查看`;
}

async function newSession() {
  if (streaming.value) return;
  try {
    await store.newSession();
    question.value = "";
  } catch (e) {
    ElMessage.error(`新建对话失败: ${e?.message || e}`);
  }
}

async function selectSession(id) {
  if (streaming.value) return;
  if (id === store.currentId) return;
  try {
    await store.selectSession(id);
  } catch (e) {
    ElMessage.error(`加载对话失败: ${e?.message || e}`);
  }
}

async function renameSession(s) {
  try {
    const { value } = await ElMessageBox.prompt("会话标题", "重命名", {
      inputValue: s.title,
      inputValidator: (v) => (v && v.trim().length > 0) || "不能为空",
    });
    await store.renameSession(s.id, value.trim());
  } catch (_) {}
}

function deleteSession(s) {
  ElMessageBox.confirm(`删除会话「${s.title}」?`, "确认", { type: "warning" })
    .then(async () => {
      try {
        await store.deleteSession(s.id);
        // 删完后没有当前会话 → 自动新建一个
        if (!store.currentId && !store.sessions.length) {
          await store.newSession();
        } else if (!store.currentId && store.sessions.length) {
          await store.selectSession(store.sessions[0].id);
        }
      } catch (e) {
        ElMessage.error(`删除失败: ${e?.message || e}`);
      }
    })
    .catch(() => {});
}

async function regenerateSummary() {
  if (streaming.value || store.summarizing) return;
  try {
    await store.regenerateSummary();
    ElMessage.success("摘要已重新生成");
  } catch (e) {
    ElMessage.error(`摘要生成失败: ${e?.message || e}`);
  }
}

async function jumpToStreaming() {
  if (store.streamingSessionId && store.streamingSessionId !== store.currentId) {
    await store.selectSession(store.streamingSessionId);
  }
}

async function send(overrideText) {
  const q = (typeof overrideText === "string" ? overrideText : question.value).trim();
  if (!q) { ElMessage.warning("请输入问题"); return; }
  if (streaming.value) return;
  question.value = "";
  scrollToEnd();
  try {
    await store.sendMessage(q);
  } catch (e) {
    ElMessage.error(e?.message || String(e));
  }
}

// 回车发送 / Shift+Enter 换行 / 输入法组合期不触发
function onEnterKey(e) {
  if (e.isComposing || e.shiftKey) return;
  e.preventDefault();
  send();
}

// 点击追问 chip → 直接作为新问题发送
function askFollowup(text) {
  if (streaming.value) return;
  send(text);
}

function stop() { store.stopStreaming(); }
// 不在 onBeforeUnmount 里 stop——store 是全局单例,Query.vue 卸载时流应继续在后台跑

onMounted(async () => {
  try {
    await store.loadSessions();
    const wanted = Number(route.query.session);
    if (Number.isFinite(wanted) && store.sessions.find((s) => s.id === wanted)) {
      await store.selectSession(wanted);
    } else if (!store.sessions.length) {
      await store.newSession();
    } else if (store.currentId) {
      try {
        await store.loadSession(store.currentId);
      } catch (_) {
        // 上次记录的 currentId 可能在后端已被删 → 切到第一个
        await store.selectSession(store.sessions[0].id);
      }
    } else {
      await store.selectSession(store.sessions[0].id);
    }
  } catch (e) {
    ElMessage.error(`加载会话失败: ${e?.message || e}`);
  }
  scrollToEnd();
});

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return d.toTimeString().slice(0, 5);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
</script>

<template>
  <div class="chat-shell">
    <!-- 左:会话列表 -->
    <aside class="sidebar">
      <div class="sidebar-head">
        <el-button type="primary" :icon="Plus" plain class="new-btn" @click="newSession">
          新建对话
        </el-button>
      </div>
      <div class="session-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === currentId }"
          @click="selectSession(s.id)"
        >
          <el-icon class="s-icon"><ChatLineRound /></el-icon>
          <div class="s-meta">
            <div class="s-title" :title="s.title">{{ s.title }}</div>
            <div class="s-time">{{ formatTime(s.updated_at) }} · {{ s.message_count }} 条</div>
          </div>
          <div class="s-actions" @click.stop>
            <el-button link size="small" :icon="Edit" @click="renameSession(s)" />
            <el-button link size="small" :icon="Delete" @click="deleteSession(s)" />
          </div>
        </div>
      </div>
    </aside>

    <!-- 右:消息流 -->
    <main class="main">
      <header class="topbar">
        <div class="title-wrap">
          <h2 class="page-title">{{ current?.title || "新对话" }}</h2>
          <span class="page-subtitle">wiki 检索 · 别名展开 · 引用追溯</span>
        </div>
        <div class="topbar-actions" v-if="current?.summary">
          <el-tooltip placement="bottom" content="重新生成对话摘要(后台用,不影响展示)">
            <el-button
              link
              size="small"
              :icon="Refresh"
              :loading="store.summarizing"
              @click="regenerateSummary"
            />
          </el-tooltip>
        </div>
      </header>

      <div class="messages" ref="messagesContainer">
        <div
          v-if="streaming && store.streamingSessionId && store.streamingSessionId !== currentId"
          class="streaming-elsewhere"
        >
          <el-icon><InfoFilled /></el-icon>
          另一个会话正在生成回复
          <el-button link size="small" @click="jumpToStreaming">查看</el-button>
        </div>

        <el-empty
          v-if="!messages.length"
          description="开始一段新对话吧"
          :image-size="100"
          class="empty-state"
        />

        <template v-else>
          <div
            v-for="(m, idx) in messages"
            :key="idx"
            class="msg-row"
            :class="m.role"
          >
            <div class="avatar">
              <el-icon v-if="m.role === 'user'"><User /></el-icon>
              <el-icon v-else><MagicStick /></el-icon>
            </div>
            <div class="bubble">
              <div v-if="m.role === 'user'" class="user-text">{{ m.content }}</div>
              <template v-else>
                <!-- 工具状态条:进行中实时行 + 完成后可展开小字记录 -->
                <div v-if="toolTimeline(m).length" class="tool-bar">
                  <div
                    v-for="(t, ti) in runningTools(m)"
                    :key="'run-' + ti"
                    class="tool-running"
                  >
                    <span class="tool-spinner" />
                    <span class="tool-running-label">{{ toolLabel(t.tool, true) }}</span>
                    <span v-if="t.reason" class="tool-reason" :title="t.reason">{{ t.reason }}</span>
                  </div>
                  <details v-if="finishedTools(m).length" class="tool-log">
                    <summary>🔧 工具调用记录（{{ finishedTools(m).length }}）</summary>
                    <ul>
                      <li v-for="(t, ti) in finishedTools(m)" :key="'fin-' + ti">
                        <span :class="['tool-status-dot', t.status]" />
                        <span class="tool-log-name">{{ toolLabel(t.tool, false) }}</span>
                        <span v-if="t.summary" class="tool-log-summary">{{ t.summary }}</span>
                      </li>
                    </ul>
                  </details>
                </div>

                <!-- 流式阶段提示(还没有正文时) -->
                <div
                  v-if="streaming && idx === messages.length - 1 && !m.content && !runningTools(m).length && stageTextFor(m)"
                  class="stage-hint"
                >
                  <span class="tool-spinner" />
                  {{ stageTextFor(m) }}
                </div>

                <MarkdownViewer
                  :source="m.content || (streaming && idx === messages.length - 1 ? '' : '_(空)_')"
                  :citations="citationsFor(m)"
                  :images="m.images || {}"
                  citation-mode
                  @image-click="(p) => openImagePanel(m, p)"
                />
                <span v-if="streaming && idx === messages.length - 1" class="caret" />

                <!-- 蓝色模板提示条 -->
                <button
                  v-if="m.chartTemplates?.length"
                  class="chart-tip"
                  @click="openTemplatesPanel(m)"
                >
                  <span class="chart-tip-icon">💡</span>
                  <span>{{ chartTipText(m) }}</span>
                </button>
              </template>

              <div
                v-if="m.role === 'assistant' && m.quality?.tier"
                class="meta-row"
              >
                <el-tag
                  v-if="m.quality?.tier && m.quality.tier !== 'unknown'"
                  :type="tierColor(m.quality.tier)"
                  size="small"
                  effect="light"
                >
                  质量 {{ m.quality.tier }} · {{ Math.round((m.quality.score || 0) * 100) }}
                </el-tag>
                <el-tag
                  v-if="m.quality?.gap_flag"
                  type="danger"
                  size="small"
                  effect="plain"
                >已记入 gap-queue</el-tag>
              </div>

              <section
                v-if="m.role === 'assistant' && kbCitationsFor(m).length"
                class="citation-sources"
                aria-label="引用来源"
              >
                <div class="citation-sources-label">引用来源</div>
                <ol>
                  <li v-for="c in kbCitationsFor(m)" :key="c.key || c.slug">
                    <a
                      :href="wikiHref(c.slug)"
                      :title="c.title"
                      @click.prevent="gotoCitation(c)"
                    >
                      <span class="citation-number">{{ c.number }}</span>
                      <span class="citation-body">
                        <span class="citation-title">
                          {{ c.title }}<span v-if="c.heading" class="citation-heading"> · {{ c.heading }}</span>
                        </span>
                        <span v-if="c.quote" class="citation-quote">{{ c.quote }}</span>
                      </span>
                    </a>
                  </li>
                </ol>
              </section>

              <section
                v-if="m.role === 'assistant' && webCitationsFor(m).length"
                class="citation-sources web-sources"
                aria-label="网络来源"
              >
                <div class="citation-sources-label">网络来源</div>
                <ol>
                  <li v-for="c in webCitationsFor(m)" :key="c.key">
                    <a
                      :href="c.url || '#'"
                      target="_blank"
                      rel="noopener"
                      :title="c.title"
                    >
                      <span class="citation-number">{{ c.number }}</span>
                      <span class="citation-body">
                        <span class="citation-title web-title">
                          <img
                            v-if="faviconOf(c.url)"
                            :src="faviconOf(c.url)"
                            class="web-favicon"
                            alt=""
                            loading="lazy"
                          />
                          {{ c.title }}
                          <span v-if="domainOf(c.url)" class="web-domain">{{ domainOf(c.url) }}</span>
                        </span>
                        <span v-if="c.quote" class="citation-quote">{{ c.quote }}</span>
                      </span>
                    </a>
                  </li>
                </ol>
              </section>

              <div
                v-if="m.role === 'assistant' && m.followups?.length && !(streaming && idx === messages.length - 1)"
                class="followups"
              >
                <div class="followups-label">继续追问</div>
                <div class="followups-list">
                  <button
                    v-for="(f, fi) in m.followups"
                    :key="fi"
                    class="followup-chip"
                    :disabled="streaming"
                    @click="askFollowup(f)"
                  >
                    <el-icon class="fu-icon"><ChatLineRound /></el-icon>
                    <span>{{ f }}</span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </template>
        <div ref="messagesEnd" />
      </div>

      <div class="input-area">
        <el-input
          v-model="question"
          type="textarea"
          :rows="3"
          placeholder="问点工业工程相关的吧… (Enter 发送，Shift+Enter 换行)"
          resize="none"
          @keydown.enter="onEnterKey"
        />
        <div class="actions">
          <span class="hint">
            <span v-if="messages.length">已有 {{ messages.length }} 条</span>
            <span v-if="current?.summary"> · 含摘要</span>
          </span>
          <el-button
            v-if="streaming"
            type="danger"
            :icon="CircleClose"
            @click="stop"
          >停止生成</el-button>
          <el-button
            v-else
            type="primary"
            :icon="Promotion"
            :loading="streaming"
            @click="send"
          >发送</el-button>
        </div>
      </div>
    </main>

    <!-- 右侧分栏面板:模板卡 / 图片大图 -->
    <SidePanel
      v-if="sidePanel.open"
      :mode="sidePanel.mode"
      :templates="sidePanel.templates"
      :base-url="sidePanel.baseUrl"
      :image="sidePanel.image"
      @close="closeSidePanel"
    />
  </div>
</template>

<style scoped>
.chat-shell {
  display: flex;
  height: calc(100vh - 56px - 36px);
  background: #fafbfc;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid var(--border-color);
}

/* 左侧 */
.sidebar {
  width: 260px;
  flex-shrink: 0;
  background: #f4f6fa;
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
}
.sidebar-head { padding: 12px; }
.new-btn { width: 100%; }
.session-list { flex: 1; overflow-y: auto; padding: 0 8px 8px; }
.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
  margin-bottom: 4px;
}
.session-item:hover { background: #eaeef5; }
.session-item.active { background: #e0e7ff; }
.s-icon { color: #6b7280; flex-shrink: 0; }
.s-meta { flex: 1; min-width: 0; }
.s-title {
  font-size: 13px;
  font-weight: 500;
  color: #111827;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.s-time { font-size: 11px; color: #9ca3af; margin-top: 2px; }
.s-actions { opacity: 0; transition: opacity 0.15s; flex-shrink: 0; }
.session-item:hover .s-actions { opacity: 1; }

/* 右侧 */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
  min-width: 0;
}
.topbar {
  padding: 14px 22px;
  border-bottom: 1px solid var(--border-color);
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.title-wrap { display: flex; flex-direction: column; gap: 2px; }
.page-title { margin: 0; font-size: 16px; font-weight: 600; color: #111827; }
.page-subtitle { font-size: 12px; color: #9ca3af; }
.topbar-actions { display: flex; align-items: center; gap: 4px; }

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.empty-state { margin: auto; }

.streaming-elsewhere {
  align-self: center;
  font-size: 12px;
  color: #b45309;
  background: #fef3c7;
  padding: 6px 14px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.msg-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  max-width: 92%;
  width: fit-content;
}
.msg-row.user { flex-direction: row-reverse; align-self: flex-end; }
.msg-row.assistant { align-self: flex-start; }

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #f1f5f9;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--primary-color);
}
.msg-row.user .avatar { background: var(--primary-color); color: #fff; }

.bubble {
  padding: 10px 14px;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  font-size: 14px;
  line-height: 1.65;
  min-width: 0;
  overflow-wrap: break-word;
  word-break: normal;
}
.msg-row.user .bubble {
  background: var(--primary-color);
  color: #fff;
  border-color: var(--primary-color);
  border-top-right-radius: 4px;
}
.msg-row.assistant .bubble { border-top-left-radius: 4px; }
.user-text { white-space: pre-wrap; }

.caret {
  display: inline-block;
  width: 8px;
  height: 16px;
  background: var(--primary-color);
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: blink 1s step-end infinite;
  border-radius: 1px;
}
@keyframes blink { 50% { opacity: 0; } }

.meta-row {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed #e5e7eb;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
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
.citation-body {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.citation-title {
  min-width: 0;
  overflow-wrap: anywhere;
}
.citation-heading { color: #94a3b8; font-size: 12px; }
.citation-quote {
  color: #9ca3af;
  font-size: 12px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.web-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.web-favicon {
  width: 14px;
  height: 14px;
  border-radius: 3px;
  flex-shrink: 0;
}
.web-domain { color: #94a3b8; font-size: 12px; }

/* ===== 工具状态条 ===== */
.tool-bar {
  margin-bottom: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tool-running {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: #4f6ef7;
  background: #f5f7ff;
  border: 1px solid #e0e7ff;
  border-radius: 8px;
  padding: 6px 10px;
  min-width: 0;
}
.tool-running-label { flex-shrink: 0; font-weight: 500; }
.tool-reason {
  color: #8b9ac9;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.tool-spinner {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  border: 2px solid #c7d2fe;
  border-top-color: #4f6ef7;
  border-radius: 50%;
  animation: tool-spin 0.9s linear infinite;
}
@keyframes tool-spin { to { transform: rotate(360deg); } }
.tool-log { font-size: 12px; color: #9ca3af; }
.tool-log summary {
  cursor: pointer;
  user-select: none;
  list-style: none;
}
.tool-log summary::-webkit-details-marker { display: none; }
.tool-log[open] summary { color: #6b7280; }
.tool-log ul {
  margin: 6px 0 0;
  padding: 0 0 0 4px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tool-log li {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.tool-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  align-self: center;
}
.tool-status-dot.ok { background: #34d399; }
.tool-status-dot.failed { background: #f87171; }
.tool-log-name { flex-shrink: 0; color: #6b7280; }
.tool-log-summary {
  color: #b0b7c3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.stage-hint {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #6b7280;
  padding: 2px 0;
}

/* ===== 蓝色模板提示条 ===== */
.chart-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin-top: 10px;
  padding: 9px 12px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 13px;
  line-height: 1.5;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.chart-tip:hover {
  background: #dbeafe;
  border-color: #93c5fd;
}
.chart-tip-icon { flex-shrink: 0; }

/* 追问建议 */
.followups {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #e5e7eb;
}
.followups-label {
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 8px;
  font-weight: 500;
}
.followups-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.followup-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  max-width: 100%;
  padding: 8px 14px;
  background: #fff;
  border: 1px solid #d9e1ec;
  border-radius: 999px;
  color: var(--primary-color);
  font-size: 13px;
  line-height: 1.4;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, transform 0.15s;
}
.followup-chip:hover:not(:disabled) {
  background: #eef2ff;
  border-color: var(--primary-color);
  transform: translateY(-1px);
}
.followup-chip:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.fu-icon { flex-shrink: 0; font-size: 14px; }

.input-area {
  border-top: 1px solid var(--border-color);
  padding: 12px 22px 14px;
  background: #fff;
}
.actions {
  margin-top: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.hint { font-size: 12px; color: #9ca3af; }

/* deep markdown 样式微调 */
.bubble :deep(p) { margin: 0 0 8px; }
.bubble :deep(p:last-child) { margin-bottom: 0; }
.bubble :deep(pre) {
  background: #1e293b;
  color: #e2e8f0;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 12.5px;
  overflow-x: auto;
}
.bubble :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12.5px;
}
.bubble :deep(pre code) { background: transparent; padding: 0; }
.msg-row.user .bubble :deep(code) { background: rgba(255, 255, 255, 0.18); }

@media (max-width: 768px) {
  .chat-shell {
    height: calc(100vh - 56px - 28px);
    border-radius: 6px;
  }
  /* 移动端隐藏会话列表(避免主 sidebar+会话双 sidebar 把内容挤光) */
  .sidebar {
    display: none;
  }
  .topbar { padding: 8px 10px; }
  .page-title { font-size: 14px; }
  .page-subtitle { display: none; }
  .bubble {
    max-width: 88% !important;
    font-size: 14px;
  }
  .composer {
    padding: 8px;
  }
}
</style>
