import { defineStore } from "pinia";
import { graphApi, wikiApi } from "@/api";
import { CORE_KINDS } from "@/constants/graphTheme";

const CORE_KIND_SET = new Set(CORE_KINDS);

const LS_THEME = "kg_graph_theme";       // light | dark
const LS_MODE = "kg_graph_mode";         // 2d | 3d
const LS_LABELS = "kg_graph_labels";     // 1 | 0

const readLS = (k, fallback) => {
  const v = localStorage.getItem(k);
  return v == null ? fallback : v;
};

export const useGraphStore = defineStore("graph", {
  state: () => ({
    // 数据
    nodes: [],
    edges: [],
    communities: {},     // { [cid]: string[] }
    insights: [],
    generatedAt: null,
    // 加载/错误
    loading: false,
    rebuilding: false,
    loadError: null,
    // 视图
    mode: readLS(LS_MODE, "2d"),
    theme: readLS(LS_THEME, "light"),
    showLabels: readLS(LS_LABELS, "1") === "1",
    // 搜索
    search: "",
    // 选中 / 高亮
    selectedNodeId: null,
    hoveredNodeId: null,
    pathNodes: [],             // 来自路由的问答路径
    bookScope: null,           // null = 全局图谱; 否则限定到该书的子图(走 wikiApi.bookGraph)
  }),
  getters: {
    nodeIndex(state) {
      const m = new Map();
      for (const n of state.nodes) m.set(n.id, n);
      return m;
    },
    /** slug → 第一个匹配的 node id(用于把 query 引用 / ?path= 旧 slug 兼容到新 id) */
    slugIndex(state) {
      const m = new Map();
      const order = ["entity", "topic", "source", "synthesis", "comparison", "query"];
      // 同 slug 多 kind 时按 order 优先取
      const buckets = new Map();
      for (const n of state.nodes) {
        const slug = n.slug || n.id;
        if (!buckets.has(slug)) buckets.set(slug, {});
        buckets.get(slug)[n.kind || "entity"] = n.id;
      }
      for (const [slug, byKind] of buckets) {
        for (const k of order) {
          if (byKind[k]) {
            m.set(slug, byKind[k]);
            break;
          }
        }
        if (!m.has(slug)) m.set(slug, Object.values(byKind)[0]);
      }
      return m;
    },
    adjacency(state) {
      const m = new Map();
      for (const e of state.edges) {
        if (!m.has(e.source)) m.set(e.source, new Set());
        if (!m.has(e.target)) m.set(e.target, new Set());
        m.get(e.source).add(e.target);
        m.get(e.target).add(e.source);
      }
      return m;
    },
    kindList() {
      // 知识库固定三类(素材/主题/实体),顺序即由内到外圈层
      return [...CORE_KINDS];
    },
    visibleNodeIds(state) {
      // 只保留三类核心节点;搜索时再按关键字过滤
      const q = state.search.trim().toLowerCase();
      const ids = new Set();
      for (const n of state.nodes) {
        if (!CORE_KIND_SET.has(n.kind || "entity")) continue;
        if (q) {
          const hay = `${n.id} ${n.slug || ""} ${n.title || ""}`.toLowerCase();
          if (!hay.includes(q)) continue;
        }
        ids.add(n.id);
      }
      return ids;
    },
    filteredGraph(state) {
      const ids = this.visibleNodeIds;
      const nodes = state.nodes.filter((n) => ids.has(n.id));
      const edges = state.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
      return { nodes, edges };
    },
  },
  actions: {
    async load(force = false, opts = {}) {
      // opts.book: 可选,限定到某本书的子图(走 GET /wiki/books/{book}/graph)
      const nextBook = opts.book ?? this.bookScope;
      const scopeChanged = nextBook !== this.bookScope;
      if (this.loading) return;
      if (this.nodes.length && !force && !scopeChanged) return;
      this.loading = true;
      this.loadError = null;
      this.bookScope = nextBook || null;
      try {
        const res = this.bookScope
          ? await wikiApi.bookGraph(this.bookScope)
          : await graphApi.data();
        const body = res?.data || res || {};
        this.nodes = body.nodes || [];
        this.edges = body.edges || body.links || [];
        this.communities = body.communities || {};
        this.insights = body.insights || [];
        this.generatedAt = body.generated_at || null;
        // 切换 scope 时清空过滤/选中,避免视图沿用上次状态
        if (scopeChanged) {
          this.selectedNodeId = null;
          this.hoveredNodeId = null;
          this.pathNodes = [];
        }
      } catch (e) {
        this.loadError = e?.response?.data?.detail || e.message || "加载失败";
      } finally {
        this.loading = false;
      }
    },
    async rebuild() {
      this.rebuilding = true;
      try {
        await graphApi.rebuild();
        await this.load(true);
      } finally {
        this.rebuilding = false;
      }
    },
    setMode(m) {
      this.mode = m === "3d" ? "3d" : "2d";
      localStorage.setItem(LS_MODE, this.mode);
    },
    setTheme(t) {
      this.theme = t === "dark" ? "dark" : "light";
      localStorage.setItem(LS_THEME, this.theme);
    },
    setShowLabels(b) {
      this.showLabels = !!b;
      localStorage.setItem(LS_LABELS, this.showLabels ? "1" : "0");
    },
    setSearch(s) { this.search = s || ""; },
    select(id) { this.selectedNodeId = id; },
    hover(id) { this.hoveredNodeId = id; },
    setPath(nodes) {
      // 兼容旧 ?path=slug,slug 形式:把 slug 映射到 `{kind}:{slug}` 形态的真实 node id
      const list = (nodes || []).filter(Boolean);
      const idx = this.nodeIndex;
      const slugIdx = this.slugIndex;
      this.pathNodes = list.map((s) => (idx.has(s) ? s : slugIdx.get(s) || s));
    },
    neighborsOf(id) {
      const set = this.adjacency.get(id);
      if (!set) return [];
      return [...set];
    },
  },
});
