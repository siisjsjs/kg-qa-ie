import http, { tokenStore } from "./http";

const unwrap = (r) => r.data;

// ===== auth =====
export const authApi = {
  login: (payload) => http.post("/auth/login", payload, { skipErrorNotify: true }).then(unwrap),
  register: (payload) => http.post("/auth/register", payload, { skipErrorNotify: true }).then(unwrap),
  logout: () => http.post("/auth/logout").then(unwrap).catch(() => null),
  refresh: (refresh_token) => http.post("/auth/refresh", { refresh_token }).then(unwrap),
  me: () => http.get("/auth/me").then(unwrap),
  changePassword: (payload) =>
    http.post("/auth/change-password", payload, { skipErrorNotify: true }).then(unwrap),
};

// ===== ingestion / sources =====
export const ingestionApi = {
  init: () => http.post("/init").then(unwrap),
  upload: (file, params) => {
    const fd = new FormData();
    fd.append("file", file);
    return http
      .post("/ingestion/upload", fd, {
        params,
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then(unwrap);
  },
  run: (docId, payload = {}) => http.post(`/ingestion/${docId}/run`, payload).then(unwrap),
  batch: (payload) => http.post("/ingestion/batch", payload).then(unwrap),
  job: (jobId) => http.get(`/ingestion/jobs/${jobId}`, { skipErrorNotify: true }).then(unwrap),
  recentJobs: (limit = 100) =>
    http.get(`/ingestion/jobs`, { params: { limit }, skipErrorNotify: true }).then(unwrap),
  documentJobs: (docId) =>
    http.get(`/ingestion/documents/${docId}/jobs`, { skipErrorNotify: true }).then(unwrap),
  documentProgress: (docId) =>
    http.get(`/ingestion/documents/${docId}/progress`, { skipErrorNotify: true }).then(unwrap),
  recentDocuments: (params = {}) =>
    http.get(`/ingestion/documents/recent`, { params, skipErrorNotify: true }).then(unwrap),
  jobDetail: (jobId) =>
    http.get(`/ingestion/jobs/${jobId}/detail`, { skipErrorNotify: true }).then(unwrap),
  deleteSource: (slug) =>
    http.delete(`/sources/${encodeURIComponent(slug)}`).then(unwrap),
  deleteDocument: (docId) =>
    http.delete(`/ingestion/documents/${docId}`).then(unwrap),
};

// ===== query =====
export const queryApi = {
  list: () => http.get("/queries").then(unwrap),
  get: (slug) => http.get(`/queries/${encodeURIComponent(slug)}`).then(unwrap),
};

// ===== conversations (P6+ 持久化多轮对话) =====
export const conversationApi = {
  list: () => http.get("/conversations").then(unwrap),
  create: (title) =>
    http.post("/conversations", { title: title || null }).then(unwrap),
  get: (id) => http.get(`/conversations/${id}`).then(unwrap),
  rename: (id, title) =>
    http.patch(`/conversations/${id}`, { title }).then(unwrap),
  delete: (id) => http.delete(`/conversations/${id}`).then(unwrap),
  summarize: (id) => http.post(`/conversations/${id}/summarize`).then(unwrap),
  meta: () => http.get("/conversations/_meta").then(unwrap),
};

// SSE 流式查询：fetch + ReadableStream + JWT。
// 401 时手动 refresh + 重试一次。
export async function streamQuery(question, handlers = {}, options = {}) {
  const {
    onMeta, onToken, onReplaceAnswer, onCitation, onPath, onQuality,
    onEvidenceValidation, onFollowups, onDone, onError,
    // Agentic 扩展事件
    onToolEvent, onImage, onChartTemplates, onWebCitations,
  } = handlers;
  const { signal, sessionId } = options;
  let attempt = 0;

  async function once() {
    const token = tokenStore.getAccess();
    const resp = await fetch("/api/v1/query", {
      method: "POST",
      signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        Authorization: token ? `Bearer ${token}` : "",
      },
      body: JSON.stringify({
        question,
        session_id: sessionId,
      }),
    });
    if (resp.status === 401 && attempt === 0) {
      attempt += 1;
      // 复用 axios 的 refresh 逻辑：直接调一次 /auth/refresh
      const refreshToken = tokenStore.getRefresh();
      if (!refreshToken) throw new Error("unauthorized");
      const r = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!r.ok) throw new Error("refresh failed");
      const json = await r.json();
      const data = json?.data || json;
      tokenStore.set(data.access_token, data.refresh_token || refreshToken);
      return once();
    }
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`SSE 请求失败 (${resp.status}) ${text}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 事件分隔符:规范上是 \n\n,但 sse-starlette / 多数后端用 \r\n\r\n。
      // 统一把 \r\n 归一成 \n,再按 \n\n 切。
      buffer = buffer.replace(/\r\n/g, "\n");
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        if (!part.trim()) continue;
        let event = "message";
        let data = "";
        for (const line of part.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        let payload = data;
        try {
          payload = JSON.parse(data);
        } catch (_) {
          /* 普通字符串 */
        }
        switch (event) {
          case "meta": onMeta?.(payload); break;
          case "progress": onMeta?.(payload); break;
          case "token": onToken?.(typeof payload === "string" ? payload : payload.text || payload.content || ""); break;
          case "replace_answer": onReplaceAnswer?.(payload?.text || ""); break;
          case "citation": onCitation?.(payload); break;
          case "citations":
            if (Array.isArray(payload?.citations)) {
              for (const c of payload.citations) {
                onCitation?.(typeof c === "string" ? { slug: c, title: c } : c);
              }
            }
            break;
          case "path": onPath?.(payload); break;
          case "quality": onQuality?.(payload); break;
          case "score": onQuality?.(payload); break;
          case "evidence_validation": onEvidenceValidation?.(payload); break;
          case "followup_suggestions":
            onFollowups?.(Array.isArray(payload?.suggestions) ? payload.suggestions : []);
            break;
          case "persist": onMeta?.({ persist: payload }); break;
          // ---- Agentic 工具事件 ----
          case "tool_start": onToolEvent?.({ type: "tool_start", ...payload }); break;
          case "tool_result": onToolEvent?.({ type: "tool_result", ...payload }); break;
          case "image_pending": onImage?.({ status: "pending", ...payload }); break;
          case "image_ready": onImage?.({ status: "ready", ...payload }); break;
          case "image_failed": onImage?.({ status: "failed", ...payload }); break;
          case "chart_templates": onChartTemplates?.(payload); break;
          case "web_citations":
            onWebCitations?.(Array.isArray(payload?.results) ? payload.results : []);
            break;
          case "warn": onError?.({ ...payload, level: "warn" }); break;
          case "done": onDone?.(payload); break;
          case "error": onError?.(payload); break;
          default: break;
        }
      }
    }
  }

  try {
    await once();
  } catch (e) {
    if (e.name !== "AbortError") onError?.({ message: e.message });
    throw e;
  }
}

// ===== digest =====
export const digestApi = {
  create: (payload) => http.post("/digest", payload).then(unwrap),
  get: (slug) => http.get(`/synthesis/${encodeURIComponent(slug)}`).then(unwrap),
};

// ===== graph =====
export const graphApi = {
  data: () => http.get("/graph/data").then(unwrap),
  rebuild: () => http.post("/graph/rebuild").then(unwrap),
  insights: () => http.get("/graph/insights").then(unwrap),
};

// ===== wiki =====
export const wikiApi = {
  index: () => http.get("/wiki/index").then(unwrap),
  page: (slug, kind) =>
    http
      .get(`/wiki/pages/${encodeURIComponent(slug)}`, {
        params: kind ? { kind } : {},
        skipErrorNotify: true,
      })
      .then(unwrap),
  // 百科搜索 + 关系聚合
  search: (q, kind, limit = 20) =>
    http
      .get("/wiki/search", { params: { q, ...(kind ? { kind } : {}), limit }, skipErrorNotify: true })
      .then(unwrap),
  relations: (slug, kind) =>
    http
      .get(`/wiki/pages/${encodeURIComponent(slug)}/relations`, {
        params: kind ? { kind } : {},
        skipErrorNotify: true,
      })
      .then(unwrap),
  updatePage: (slug, payload) =>
    http.put(`/wiki/pages/${encodeURIComponent(slug)}`, payload).then(unwrap),
  deletePage: (slug) =>
    http.delete(`/wiki/pages/${encodeURIComponent(slug)}`).then(unwrap),
  // 结构化浏览
  books: () => http.get("/wiki/books").then(unwrap),
  bookTree: (book) =>
    http.get(`/wiki/books/${encodeURIComponent(book)}/tree`).then(unwrap),
  bookGraph: (book) =>
    http.get(`/wiki/books/${encodeURIComponent(book)}/graph`).then(unwrap),
  // 知识库管理(管理员)
  createBook: (name) => http.post("/wiki/books", { name }).then(unwrap),
  // 导航 map(智能问答检索路由用)
  navMap: (book) =>
    http.get("/wiki/nav-map", { params: book ? { book } : {} }).then(unwrap),
  rebuildNavMap: () => http.post("/wiki/nav-map/rebuild").then(unwrap),
  reorderChapters: (book, slugs) =>
    http.put(`/wiki/books/${encodeURIComponent(book)}/chapters/order`, { slugs }).then(unwrap),
  renamePage: (slug, title, kind) =>
    http
      .put(`/wiki/pages/${encodeURIComponent(slug)}/rename`, { title }, { params: kind ? { kind } : {} })
      .then(unwrap),
  moveChapter: (slug, book) =>
    http.put(`/wiki/chapters/${encodeURIComponent(slug)}/book`, { book }).then(unwrap),
};

// ===== qa-data (admin) =====
export const qaDataApi = {
  list: (kind) =>
    http.get("/qa-data", { params: kind ? { kind } : {} }).then(unwrap),
};

// ===== status / lint / followups =====
export const statusApi = {
  status: () => http.get("/status").then(unwrap),
  lint: () => http.get("/lint").then(unwrap),
  trend: () => http.get("/trend").then(unwrap),
  followups: () => http.get("/followups").then(unwrap),
};

// ===== lint void-links 专用 (虚空连接清理) =====
export const voidLinksApi = {
  scan: () => http.post("/lint/void-links/scan", {}, { timeout: 60000 }).then(unwrap),
  // accept_aliases: [{canonical, alias}], generate_slugs: [slug,...]
  // LLM 生成耗时长,timeout 给到 10 分钟
  run: (payload) =>
    http.post("/lint/void-links/run", payload, { timeout: 600000 }).then(unwrap),
};

// ===== crystallize =====
export const crystallizeApi = {
  create: (payload) => http.post("/crystallize", payload).then(unwrap),
};

// ===== scraper =====
export const scraperApi = {
  // firecrawl 抓 + LLM 整合,可能 1-3 分钟
  scrape: (url) => http.post("/scraper/scrape", { url }, { timeout: 300000 }).then(unwrap),
};

// ===== announcements =====
export const announcementApi = {
  list: () => http.get("/announcements").then(unwrap),
  listAdmin: () => http.get("/announcements/admin").then(unwrap),
  get: (id) => http.get(`/announcements/${id}`).then(unwrap),
  create: (payload) => http.post("/announcements", payload).then(unwrap),
  update: (id, payload) => http.put(`/announcements/${id}`, payload).then(unwrap),
  delete: (id) => http.delete(`/announcements/${id}`).then(unwrap),
  uploadImage: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return http
      .post("/announcements/upload-image", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then(unwrap);
  },
};

// ===== feedback =====
export const feedbackApi = {
  submit: (payload) => http.post("/feedbacks", payload).then(unwrap),
  list: (status) =>
    http
      .get("/feedbacks", { params: status ? { status } : {} })
      .then(unwrap),
  update: (id, payload) => http.put(`/feedbacks/${id}`, payload).then(unwrap),
  delete: (id) => http.delete(`/feedbacks/${id}`).then(unwrap),
};

// ===== admin =====
export const adminApi = {
  getConfig: () => http.get("/admin/config").then(unwrap),
  putLLM: (payload) => http.put("/admin/config/llm", payload).then(unwrap),
  putEmbedding: (payload) => http.put("/admin/config/embedding", payload).then(unwrap),
  getIngestionConfig: () => http.get("/admin/ingestion-config").then(unwrap),
  putIngestionConfig: (payload) => http.put("/admin/ingestion-config", payload).then(unwrap),
  // ---- 工具配置(Agentic:Tavily 联网搜索 / 画图接口 / 绘图平台) ----
  getTavilyConfig: () => http.get("/admin/config/tavily").then(unwrap),
  putTavilyConfig: (payload) => http.put("/admin/config/tavily", payload).then(unwrap),
  getImagegenConfig: () => http.get("/admin/config/imagegen").then(unwrap),
  putImagegenConfig: (payload) => http.put("/admin/config/imagegen", payload).then(unwrap),
  getZhihuituConfig: () => http.get("/admin/config/zhihuitu").then(unwrap),
  putZhihuituConfig: (payload) => http.put("/admin/config/zhihuitu", payload).then(unwrap),
  testConnection: (target) =>
    http.post("/admin/config/test", { target }, { timeout: 60000 }).then(unwrap),
  getSchema: () => http.get("/admin/schema").then(unwrap),
  putSchema: (payload) => http.put("/admin/schema", payload).then(unwrap),
  deleteAlias: (key) =>
    http.post("/admin/schema/aliases/delete", { key }).then(unwrap),
  deleteAliasMember: (canonical, member) =>
    http.post("/admin/schema/aliases/member/delete", { canonical, member }).then(unwrap),
  deleteRelation: (key) =>
    http.post("/admin/schema/relations/delete", { key }).then(unwrap),
  getGrowthConfig: () => http.get("/admin/schema/growth").then(unwrap),
  putGrowthConfig: (config) =>
    http.put("/admin/schema/growth", { config }).then(unwrap),
  getPurpose: () => http.get("/admin/purpose").then(unwrap),
  putPurpose: (payload) => http.put("/admin/purpose", payload).then(unwrap),
  gapQueue: (status = "open") =>
    http.get("/admin/gap-queue", { params: { status } }).then(unwrap),
  setGapStatus: (id, status) =>
    http.put(`/admin/gap-queue/${id}`, null, { params: { status } }).then(unwrap),
  // users
  listUsers: () => http.get("/admin/users").then(unwrap),
  pendingUsers: () => http.get("/admin/users/pending").then(unwrap),
  approveUser: (id) => http.post(`/admin/users/${id}/approve`).then(unwrap),
  rejectUser: (id) => http.post(`/admin/users/${id}/reject`).then(unwrap),
  setUserStatus: (id, status) =>
    http.put(`/admin/users/${id}`, { status }).then(unwrap),
  deleteUser: (id) => http.delete(`/admin/users/${id}`).then(unwrap),
  // firecrawl
  getFirecrawl: () => http.get("/admin/config/firecrawl").then(unwrap),
  putFirecrawl: (payload) => http.put("/admin/config/firecrawl", payload).then(unwrap),
  // dashboard
  dashboardMetrics: () => http.get("/admin/dashboard/metrics").then(unwrap),
};
