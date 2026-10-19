import { defineStore } from "pinia";
import { ingestionApi } from "@/api";

// 跨路由保留:本次会话上传的素材流水线进度(组件销毁后任务还在跑)。
export const useSourcesStore = defineStore("sources", {
  state: () => ({
    docs: [],
    pollTimer: null,
    bootstrapped: false,
    bootstrapping: null, // 进行中的 promise,避免并发重入
  }),
  actions: {
    addDoc(row) {
      this.docs.unshift(row);
    },
    removeDoc(row) {
      this.docs = this.docs.filter((x) => x !== row);
    },
    findDoc(docId) {
      return this.docs.find((d) => d.doc_id === docId);
    },
    /**
     * 从后端拉最近 24h 的文档,合并进 docs 列表 — 用于刷新页面 / 切路由后恢复进度。
     * - 已有的 doc(同 doc_id)只补字段,不覆盖
     * - 跑完(succeeded/failed)的也带回来,但只展示;如果用户希望"只看进行中",
     *   可改 only_open=true,这里默认全量,跟"本次会话流水线"语义一致
     */
    async bootstrap({ force = false } = {}) {
      if (this.bootstrapping) return this.bootstrapping;
      if (this.bootstrapped && !force) return;
      this.bootstrapping = (async () => {
        let recents = [];
        try {
          recents = await ingestionApi.recentDocuments({ limit: 50, hours: 24 });
        } catch (_) {
          // 后端不支持就忽略,不影响后续手动上传
          this.bootstrapped = true;
          return;
        }
        // 把 recents 合并进 docs(已存在的不动)
        for (const r of recents) {
          if (this.findDoc(r.document_id)) continue;
          this.docs.push({
            doc_id: r.document_id,
            filename: r.original_filename,
            parse_status: r.parse_status,
            current_stage: null,
            stages_done: 0,
            stages_total: 4,
            progress: r.parse_status === "succeeded" ? 100 : 0,
            parse_error: r.parse_error,
            failed_job_id: null,
            source_slug: null,
          });
        }
        // 按 doc_id 倒序(最新在前)
        this.docs.sort((a, b) => (b.doc_id || 0) - (a.doc_id || 0));
        // 立即拉一轮 progress 把 current_stage / stages_done / source_slug 填上
        await Promise.all(
          this.docs.map(async (d) => {
            try {
              const p = await ingestionApi.documentProgress(d.doc_id);
              const { original_filename, ...rest } = p || {};
              Object.assign(d, rest);
              if (!d.filename && original_filename) d.filename = original_filename;
            } catch (_) {
              /* 忽略,polling 会重试 */
            }
          })
        );
        this.bootstrapped = true;
      })();
      try {
        await this.bootstrapping;
      } finally {
        this.bootstrapping = null;
      }
    },
    startPolling() {
      if (this.pollTimer) return;
      this.pollTimer = setInterval(async () => {
        const open = this.docs.filter(
          (d) => !["succeeded", "failed"].includes(d.parse_status)
        );
        if (!open.length) {
          this.stopPolling();
          return;
        }
        await Promise.all(
          open.map(async (d) => {
            try {
              const p = await ingestionApi.documentProgress(d.doc_id);
              const { original_filename, ...rest } = p || {};
              Object.assign(d, rest);
              if (!d.filename && original_filename) d.filename = original_filename;
            } catch (_) {
              /* 单轮失败忽略,下一轮再试 */
            }
          })
        );
      }, 1500);
    },
    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer);
        this.pollTimer = null;
      }
    },
    clearAll() {
      this.stopPolling();
      this.docs = [];
      this.bootstrapped = false;
    },
  },
});
