import { defineStore } from "pinia";
import { adminApi } from "@/api";

// 统一走 openai_compat,DeepSeek 直连 + 中转站都用这条路
const DEFAULT_LLM = {
  provider: "openai_compat",
  api_key: "",
  base_url: "https://api.deepseek.com/v1",
  model: "deepseek-chat",
  temperature: 0.3,
  max_tokens: 4096,
};

const DEFAULT_EMB = {
  provider: "openai_compat",
  api_key: "",
  base_url: "https://api.deepseek.com/v1",
  model: "BAAI/bge-m3",
  dim: 1024,
};

export const useConfigStore = defineStore("config", {
  state: () => ({
    llm: { ...DEFAULT_LLM },
    embedding: { ...DEFAULT_EMB },
    availableLLMProviders: [],
    availableEmbeddingProviders: [],
    loaded: false,
    saving: false,
  }),
  actions: {
    async load() {
      const data = await adminApi.getConfig();
      this.llm = { ...DEFAULT_LLM, ...data.llm };
      this.embedding = { ...DEFAULT_EMB, ...data.embedding };
      this.availableLLMProviders = data.available_llm_providers || [];
      this.availableEmbeddingProviders = data.available_embedding_providers || [];
      this.loaded = true;
    },
    async saveLLM(payload) {
      this.saving = true;
      try {
        await adminApi.putLLM(payload);
        await this.load();
      } finally {
        this.saving = false;
      }
    },
    async saveEmbedding(payload) {
      this.saving = true;
      try {
        await adminApi.putEmbedding(payload);
        await this.load();
      } finally {
        this.saving = false;
      }
    },
    async testConnection(target) {
      return await adminApi.testConnection(target);
    },
  },
});
