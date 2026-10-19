import { defineStore } from "pinia";
import { conversationApi, streamQuery } from "@/api";

// P6+ 改造:
//   - 不再用 localStorage 持久化对话,所有 CRUD 走后端 API
//   - 仅缓存 currentId 到 localStorage(用户体验:刷新页面回到上次会话)
//   - 流式期间把 token 累计写入本地 messages,流完后 backend 已落库,无需手动 sync
//   - 摘要由后端 _stream_answer 自动触发,前端只负责展示
//   - 暴露 regenerateSummary 给"重新生成摘要"按钮
//   - sendMessage 在 store 层管理,Query.vue 卸载也不会中断流式

const CURRENT_KEY = "kg_current_conversation_id";

function loadCurrentId() {
  try {
    const v = localStorage.getItem(CURRENT_KEY);
    return v ? Number(v) : null;
  } catch (_) {
    return null;
  }
}
function saveCurrentId(id) {
  try {
    if (id == null) localStorage.removeItem(CURRENT_KEY);
    else localStorage.setItem(CURRENT_KEY, String(id));
  } catch (_) {}
}

// 老版 localStorage 数据已不兼容 → 启动时清掉,避免误读
try {
  localStorage.removeItem("kg_conversations_v1");
} catch (_) {}

export function genImageUrl(imageId) {
  return `/api/v1/files/genimg/${imageId}.png`;
}

// meta.images([{image_id,status,prompt}]) → 前端 images map({id: {..., url}})
// 历史加载时 pending 视为 maybe-ready:直接给 url,由 <img> onerror 降级
function imagesFromMeta(metaImages) {
  const map = {};
  for (const img of metaImages || []) {
    if (!img?.image_id) continue;
    map[img.image_id] = {
      image_id: img.image_id,
      status: img.status === "failed" ? "failed" : "ready",
      prompt: img.prompt || "",
      caption: img.caption || "",
      chart: img.chart || null,
      url: img.status === "failed" ? "" : genImageUrl(img.image_id),
    };
  }
  return map;
}

function emptySession() {
  // 创建中占位:正式 id 由后端给
  return {
    id: null,
    title: "新对话",
    summary: "",
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

export const useConversationsStore = defineStore("conversations", {
  state: () => ({
    sessions: [],          // [{id, title, summary, last_message_preview, message_count, ...}]
    currentId: loadCurrentId(),
    currentDetail: null,   // {id, title, summary, messages: [{id, role, content, meta}]}
    loading: false,
    summarizing: false,
    // —— 流式状态(后台运行,跨路由切换保留)——
    streaming: false,           // 全局是否在流式
    streamingSessionId: null,   // 当前流属于哪个会话
    streamingDetail: null,      // 流式会话的 detail 缓存(切走再切回不丢失)
    abortCtl: null,             // 当前流的 AbortController
  }),
  getters: {
    current(state) {
      return state.currentDetail;
    },
    messages(state) {
      return state.currentDetail?.messages || [];
    },
    summary(state) {
      return state.currentDetail?.summary || "";
    },
  },
  actions: {
    // 拉会话列表
    async loadSessions() {
      this.loading = true;
      try {
        const list = await conversationApi.list();
        this.sessions = Array.isArray(list) ? list : [];
        // 当前 id 不在列表里 → 重置
        if (this.currentId && !this.sessions.find((s) => s.id === this.currentId)) {
          this.currentId = null;
          saveCurrentId(null);
          this.currentDetail = null;
        }
        return this.sessions;
      } finally {
        this.loading = false;
      }
    },

    async loadSession(id) {
      if (id == null) {
        this.currentDetail = null;
        return null;
      }
      // 流式会话切回:用 streamingDetail 而不是去 DB 拉
      // (DB 此时只有 user 消息,assistant 还在 stream 没落库,拉了会丢正在追加的 token)
      if (this.streaming && this.streamingSessionId === id && this.streamingDetail) {
        this.currentDetail = this.streamingDetail;
        this.currentId = id;
        saveCurrentId(id);
        return this.currentDetail;
      }
      this.loading = true;
      try {
        const detail = await conversationApi.get(id);
        this.currentDetail = {
          id: detail.id,
          title: detail.title || "新对话",
          summary: detail.summary || "",
          messages: (detail.messages || []).map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content || "",
            // 把 meta 平铺成前端用的 citations / quality / path / followups
            citations: m.meta?.citations || [],
            quality: m.meta?.quality || null,
            path: m.meta?.path || null,
            followups: m.meta?.followups || [],
            // Agentic 富媒体字段(从 meta 恢复;流结束后 loadSession 会覆盖本地状态,
            // 所以后端 meta 必须是全量数据)
            toolEvents: m.meta?.tool_events || [],
            images: imagesFromMeta(m.meta?.images),
            chartTemplates: m.meta?.chart_templates?.templates || [],
            chartBaseUrl: m.meta?.chart_templates?.base_url || "",
            webCitations: m.meta?.web_citations || [],
            meta: m.meta || {},
          })),
        };
        this.currentId = id;
        saveCurrentId(id);
        return this.currentDetail;
      } finally {
        this.loading = false;
      }
    },

    async selectSession(id) {
      if (id === this.currentId && this.currentDetail) return;
      await this.loadSession(id);
    },

    async newSession() {
      const created = await conversationApi.create(null);
      // 列表里 prepend 一个简略项,然后切到详情
      this.sessions.unshift({
        id: created.id,
        title: created.title || "新对话",
        summary: "",
        last_message_preview: "",
        message_count: 0,
        created_at: created.created_at,
        updated_at: created.updated_at,
      });
      this.currentId = created.id;
      saveCurrentId(created.id);
      this.currentDetail = {
        id: created.id,
        title: created.title || "新对话",
        summary: "",
        messages: [],
      };
      return this.currentDetail;
    },

    async ensureCurrent() {
      if (this.currentId && this.currentDetail) return this.currentDetail;
      if (this.currentId) {
        try {
          return await this.loadSession(this.currentId);
        } catch (_) {
          // 可能后端已删
          this.currentId = null;
          saveCurrentId(null);
        }
      }
      return await this.newSession();
    },

    async renameSession(id, title) {
      const trimmed = (title || "").trim().slice(0, 120) || "新对话";
      await conversationApi.rename(id, trimmed);
      const s = this.sessions.find((x) => x.id === id);
      if (s) s.title = trimmed;
      if (this.currentDetail?.id === id) this.currentDetail.title = trimmed;
    },

    async deleteSession(id) {
      await conversationApi.delete(id);
      this.sessions = this.sessions.filter((s) => s.id !== id);
      if (this.currentId === id) {
        this.currentId = null;
        this.currentDetail = null;
        saveCurrentId(null);
      }
    },

    async regenerateSummary() {
      if (!this.currentId) return;
      this.summarizing = true;
      try {
        const resp = await conversationApi.summarize(this.currentId);
        if (this.currentDetail) {
          this.currentDetail.summary = resp.summary || "";
        }
        const s = this.sessions.find((x) => x.id === this.currentId);
        if (s) s.summary = resp.summary || "";
        return resp;
      } finally {
        this.summarizing = false;
      }
    },

    // === 流式期间用的本地 buffer 操作 ===
    pushUserMessage(content) {
      if (!this.currentDetail) return;
      this.currentDetail.messages.push({
        id: null,
        role: "user",
        content,
        citations: [],
        quality: null,
        path: null,
        meta: {},
      });
      // 第一条用户消息 → 自动用问题前 24 字作 title
      if (this.currentDetail.title === "新对话" && content) {
        const title = content.trim().slice(0, 24);
        this.currentDetail.title = title;
        const s = this.sessions.find((x) => x.id === this.currentId);
        if (s) s.title = title;
      }
    },
    pushAssistantPlaceholder() {
      if (!this.currentDetail) return;
      this.currentDetail.messages.push({
        id: null,
        role: "assistant",
        content: "",
        citations: [],
        quality: null,
        path: null,
        followups: [],
        toolEvents: [],
        images: {},
        chartTemplates: [],
        chartBaseUrl: "",
        webCitations: [],
        meta: {},
      });
    },
    appendLastAssistantContent(text) {
      if (!this.currentDetail || !this.currentDetail.messages.length) return;
      const last = this.currentDetail.messages[this.currentDetail.messages.length - 1];
      if (last.role !== "assistant") return;
      last.content = (last.content || "") + text;
    },
    updateLastAssistant(patch) {
      if (!this.currentDetail || !this.currentDetail.messages.length) return;
      const last = this.currentDetail.messages[this.currentDetail.messages.length - 1];
      if (last.role !== "assistant") return;
      Object.assign(last, patch);
    },
    addCitation(c) {
      if (!this.currentDetail || !this.currentDetail.messages.length) return;
      const last = this.currentDetail.messages[this.currentDetail.messages.length - 1];
      if (last.role !== "assistant") return;
      const arr = last.citations || [];
      last.citations = [...arr, typeof c === "string" ? { slug: c, title: c } : c];
    },

    // 流式结束后,从后端拉一次最新会话状态(拿到自动摘要 + 真实 message id)
    async refreshAfterStream() {
      if (!this.currentId) return;
      try {
        await this.loadSession(this.currentId);
        // 同步列表里的预览 / message_count / updated_at
        await this.loadSessions();
      } catch (_) {}
    },

    // ===== 流式发送消息(在 store 层管理,跨路由切换不中断)=====
    //
    // 设计要点:
    //   - AbortController 在 store 上,Query.vue 卸载时不会被 onBeforeUnmount stop
    //   - streamingSessionId 记录流属于哪个会话;切到别的会话再切回来仍能看到流式进度
    //   - 流式过程中追加到 currentDetail.messages(响应式),离开页面后 token 仍在积累
    //   - 只允许同时一个流(避免并发重复扣 LLM 配额)
    async sendMessage(question) {
      const q = (question || "").trim();
      if (!q) throw new Error("empty question");
      if (this.streaming) throw new Error("正在生成中,请稍候或停止");

      await this.ensureCurrent();
      const sessionId = this.currentId;

      // 推用户消息 + assistant 占位(本地立即可见)
      this.pushUserMessage(q);
      this.pushAssistantPlaceholder();

      this.streaming = true;
      this.streamingSessionId = sessionId;
      this.streamingDetail = this.currentDetail;
      this.abortCtl = new AbortController();

      try {
        const writeToStreamingLast = (patch) => {
          // 写到 streamingDetail 的最后一条 assistant(无论用户当前在哪个会话)
          const det = this.streamingDetail;
          if (!det || !det.messages?.length) return;
          const last = det.messages[det.messages.length - 1];
          if (last.role !== "assistant") return;
          Object.assign(last, patch);
        };
        const appendStreamingContent = (text) => {
          const det = this.streamingDetail;
          if (!det || !det.messages?.length) return;
          const last = det.messages[det.messages.length - 1];
          if (last.role !== "assistant") return;
          last.content = (last.content || "") + text;
        };
        const addStreamingCitation = (c) => {
          const det = this.streamingDetail;
          if (!det || !det.messages?.length) return;
          const last = det.messages[det.messages.length - 1];
          if (last.role !== "assistant") return;
          const arr = last.citations || [];
          last.citations = [...arr, typeof c === "string" ? { slug: c, title: c } : c];
        };
        await streamQuery(
          q,
          {
            onMeta: (m) => {
              const det = this.streamingDetail;
              const last = det?.messages?.[det.messages.length - 1];
              const prevMeta = last?.meta || {};
              writeToStreamingLast({ meta: { ...prevMeta, ...m } });
            },
            onToken: (t) => appendStreamingContent(t),
            onReplaceAnswer: (text) => writeToStreamingLast({ content: text }),
            onCitation: (c) => addStreamingCitation(c),
            onPath: (p) => writeToStreamingLast({ path: p }),
            onQuality: (qty) => writeToStreamingLast({ quality: qty }),
            onEvidenceValidation: (validation) => {
              const det = this.streamingDetail;
              const last = det?.messages?.[det.messages.length - 1];
              const prevMeta = last?.meta || {};
              writeToStreamingLast({ meta: { ...prevMeta, evidence_validation: validation } });
            },
            onFollowups: (list) => writeToStreamingLast({ followups: Array.isArray(list) ? list : [] }),
            // ---- Agentic 工具事件 ----
            onToolEvent: (evt) => {
              const det = this.streamingDetail;
              const last = det?.messages?.[det.messages.length - 1];
              if (!last || last.role !== "assistant") return;
              last.toolEvents = [...(last.toolEvents || []), evt];
            },
            onImage: (img) => {
              const det = this.streamingDetail;
              const last = det?.messages?.[det.messages.length - 1];
              if (!last || last.role !== "assistant" || !img?.image_id) return;
              const prev = (last.images || {})[img.image_id] || {};
              last.images = {
                ...(last.images || {}),
                [img.image_id]: {
                  image_id: img.image_id,
                  status: img.status,
                  prompt: img.prompt || prev.prompt || "",
                  caption: img.caption || prev.caption || "",
                  chart: img.chart || prev.chart || null,
                  url: img.status === "ready" ? (img.url || genImageUrl(img.image_id)) : "",
                  error: img.error || "",
                },
              };
            },
            onChartTemplates: (payload) => {
              writeToStreamingLast({
                chartTemplates: payload?.templates || [],
                chartBaseUrl: payload?.base_url || "",
              });
            },
            onWebCitations: (results) => {
              const det = this.streamingDetail;
              const last = det?.messages?.[det.messages.length - 1];
              if (!last || last.role !== "assistant") return;
              const existing = new Set((last.webCitations || []).map((w) => w.url));
              last.webCitations = [
                ...(last.webCitations || []),
                ...(results || []).filter((w) => w?.url && !existing.has(w.url)),
              ];
            },
            onDone: () => {},
            onError: (e) => {
              const det = this.streamingDetail;
              const last = det?.messages?.[det.messages.length - 1];
              const old = last?.content || "";
              const label = e?.level === "warn" ? "提示" : "出错";
              writeToStreamingLast({ content: old + `\n\n> ${label}: ${e?.message || e}` });
            },
          },
          { signal: this.abortCtl.signal, sessionId },
        );
      } catch (e) {
        if (e?.name !== "AbortError") {
          const det = this.streamingDetail;
          const last = det?.messages?.[det.messages.length - 1];
          const old = last?.content || "";
          if (last) last.content = old + `\n\n> ${e.message || e}`;
        }
        // 不 rethrow:store action 的错误对路由切换没意义
      } finally {
        this.streaming = false;
        this.abortCtl = null;
        const finishedSid = this.streamingSessionId;
        this.streamingSessionId = null;
        this.streamingDetail = null;
        // 流式结束 → 从 DB 拉最新状态(无论用户当前在哪个会话)
        try {
          await this.loadSessions();
          if (this.currentId === finishedSid) {
            // 强制走 DB 路径(此时已经不是 streaming 了,上面 loadSession 的快路径不会命中)
            await this.loadSession(finishedSid);
          }
        } catch (_) {}
      }
    },

    stopStreaming() {
      try {
        this.abortCtl?.abort();
      } catch (_) {}
    },
  },
});
