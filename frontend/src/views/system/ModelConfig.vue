<script setup>
import { onMounted, reactive, ref, computed } from "vue";
import { ElMessage } from "element-plus";
import { useConfigStore } from "@/stores/config";
import { adminApi } from "@/api";

const cfg = useConfigStore();

// 表单本地副本
const llmForm = reactive({ ...cfg.llm });
const embForm = reactive({ ...cfg.embedding });

const llmKeyMasked = computed(() => llmForm.api_key && llmForm.api_key.includes("*"));
const embKeyMasked = computed(() => embForm.api_key && embForm.api_key.includes("*"));

const llmTesting = ref(false);
const embTesting = ref(false);
const llmTestResult = ref(null);
const embTestResult = ref(null);

// 高级设置(成长配置)
const advancedOpen = ref([]);
const growth = reactive({
  hot_topic_threshold: 3,
  answer_quality_gap_cutoff: 0.5,
  auto_merge_cosine_threshold: 0.92,
  suggest_merge_cosine_threshold: 0.75,
  deep_dive_required_sections: 5,
  domain_ie: true,
});
const growthLoading = ref(false);
const growthSaving = ref(false);

async function loadGrowth() {
  growthLoading.value = true;
  try {
    const data = await adminApi.getGrowthConfig();
    const cfgObj = (data?.data || data || {}).config || {};
    Object.assign(growth, cfgObj);
  } catch (e) {
    // 静默失败,保留默认值
  } finally {
    growthLoading.value = false;
  }
}

async function saveGrowth() {
  growthSaving.value = true;
  try {
    await adminApi.putGrowthConfig({ ...growth });
    ElMessage.success("高级设置已保存");
  } catch (e) {
    ElMessage.error("保存失败：" + (e.response?.data?.detail || e.message));
  } finally {
    growthSaving.value = false;
  }
}

// ---- 工具配置(Agentic:联网搜索 / 图片生成 / 绘图平台) ----
const tavilyForm = reactive({ api_key: "" });
const imagegenForm = reactive({ base_url: "", api_key: "", model: "", size: "" });
const zhihuituForm = reactive({ base_url: "" });
const tavilyKeyMasked = computed(() => tavilyForm.api_key && tavilyForm.api_key.includes("*"));
const imgKeyMasked = computed(() => imagegenForm.api_key && imagegenForm.api_key.includes("*"));
const toolsSaving = ref(false);
const tavilyTesting = ref(false);
const imgTesting = ref(false);
const zhtTesting = ref(false);
const tavilyTestResult = ref(null);
const imgTestResult = ref(null);
const zhtTestResult = ref(null);

async function loadTools() {
  try {
    const [tv, img, zht] = await Promise.all([
      adminApi.getTavilyConfig(),
      adminApi.getImagegenConfig(),
      adminApi.getZhihuituConfig(),
    ]);
    Object.assign(tavilyForm, tv || {});
    Object.assign(imagegenForm, img || {});
    Object.assign(zhihuituForm, zht || {});
  } catch (e) {
    // 静默失败,保留空表单
  }
}

function keyForWrite(raw) {
  // 屏蔽串/空串 → 不修改(后端同样防御)
  if (!raw || raw.includes("*")) return "";
  return raw;
}

async function saveTavily() {
  toolsSaving.value = true;
  try {
    await adminApi.putTavilyConfig({ api_key: keyForWrite(tavilyForm.api_key) });
    ElMessage.success("Tavily 配置已保存并立即生效");
    await loadTools();
  } catch (e) {
    ElMessage.error("保存失败：" + (e.response?.data?.detail || e.message));
  } finally {
    toolsSaving.value = false;
  }
}

async function saveImagegen() {
  toolsSaving.value = true;
  try {
    await adminApi.putImagegenConfig({
      base_url: imagegenForm.base_url || "",
      api_key: keyForWrite(imagegenForm.api_key),
      model: imagegenForm.model || "",
      size: imagegenForm.size || "",
    });
    ElMessage.success("画图接口配置已保存并立即生效");
    await loadTools();
  } catch (e) {
    ElMessage.error("保存失败：" + (e.response?.data?.detail || e.message));
  } finally {
    toolsSaving.value = false;
  }
}

async function saveZhihuitu() {
  toolsSaving.value = true;
  try {
    await adminApi.putZhihuituConfig({ base_url: zhihuituForm.base_url || "" });
    ElMessage.success("绘图平台配置已保存并立即生效");
    await loadTools();
  } catch (e) {
    ElMessage.error("保存失败：" + (e.response?.data?.detail || e.message));
  } finally {
    toolsSaving.value = false;
  }
}

async function testZhihuitu() {
  zhtTesting.value = true;
  zhtTestResult.value = null;
  try {
    zhtTestResult.value = await cfg.testConnection("zhihuitu");
  } catch (e) {
    zhtTestResult.value = { ok: false, error: e.response?.data?.detail || e.message };
  } finally {
    zhtTesting.value = false;
  }
}

async function testTavily() {
  tavilyTesting.value = true;
  tavilyTestResult.value = null;
  try {
    tavilyTestResult.value = await cfg.testConnection("tavily");
  } catch (e) {
    tavilyTestResult.value = { ok: false, error: e.response?.data?.detail || e.message };
  } finally {
    tavilyTesting.value = false;
  }
}

async function testImagegen() {
  imgTesting.value = true;
  imgTestResult.value = null;
  try {
    imgTestResult.value = await cfg.testConnection("imagegen");
  } catch (e) {
    imgTestResult.value = { ok: false, error: e.response?.data?.detail || e.message };
  } finally {
    imgTesting.value = false;
  }
}

async function refresh() {
  try {
    await cfg.load();
    Object.assign(llmForm, cfg.llm);
    Object.assign(embForm, cfg.embedding);
  } catch (e) {
    ElMessage.error("读取配置失败：" + (e.response?.data?.message || e.message));
  }
  await Promise.all([loadGrowth(), loadTools()]);
}

onMounted(refresh);

async function saveLLM() {
  // 统一走 openai_compat;temperature / max_tokens 隐藏不显示但仍保留默认
  const payload = {
    provider: "openai_compat",
    base_url: llmForm.base_url || "",
    model: llmForm.model || "",
    api_key: llmForm.api_key,
    temperature: llmForm.temperature ?? 0.3,
    max_tokens: llmForm.max_tokens ?? 4096,
  };
  if (!payload.api_key || payload.api_key.includes("*")) payload.api_key = "";
  try {
    await cfg.saveLLM(payload);
    ElMessage.success("LLM 配置已保存并立即生效");
    Object.assign(llmForm, cfg.llm);
  } catch (e) {
    ElMessage.error("保存失败：" + (e.response?.data?.detail || e.response?.data?.message || e.message));
  }
}

async function saveEmbedding() {
  const payload = {
    provider: "openai_compat",
    base_url: embForm.base_url || "",
    model: embForm.model || "",
    api_key: embForm.api_key,
    dim: embForm.dim ?? 1024,
  };
  if (!payload.api_key || payload.api_key.includes("*")) payload.api_key = "";
  try {
    await cfg.saveEmbedding(payload);
    ElMessage.success("Embedding 配置已保存并立即生效");
    Object.assign(embForm, cfg.embedding);
  } catch (e) {
    ElMessage.error("保存失败：" + (e.response?.data?.detail || e.response?.data?.message || e.message));
  }
}

async function testLLM() {
  llmTesting.value = true;
  llmTestResult.value = null;
  try {
    llmTestResult.value = await cfg.testConnection("llm");
  } catch (e) {
    llmTestResult.value = { ok: false, error: e.response?.data?.detail || e.message };
  } finally {
    llmTesting.value = false;
  }
}

async function testEmbedding() {
  embTesting.value = true;
  embTestResult.value = null;
  try {
    embTestResult.value = await cfg.testConnection("embedding");
  } catch (e) {
    embTestResult.value = { ok: false, error: e.response?.data?.detail || e.message };
  } finally {
    embTesting.value = false;
  }
}
</script>

<template>
  <div>
    <h2 class="page-title">模型配置</h2>
    <div class="page-hint">
      统一使用 OpenAI 兼容协议，可直连 DeepSeek 官方 API、中转站或自托管服务。
    </div>

    <el-row :gutter="16">
      <el-col :span="12">
        <el-card>
          <template #header><strong>LLM</strong></template>
          <el-form label-width="110px" size="default">
            <el-form-item label="Base URL">
              <el-input v-model="llmForm.base_url" placeholder="如 https://api.deepseek.com/v1" />
            </el-form-item>
            <el-form-item label="Model">
              <el-input v-model="llmForm.model" placeholder="如 deepseek-chat" />
            </el-form-item>
            <el-form-item label="API Key">
              <el-input
                v-model="llmForm.api_key"
                type="password"
                show-password
                :placeholder="llmKeyMasked ? '已保存（留空则保留原值）' : '在此粘贴 API key'"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="cfg.saving" @click="saveLLM">保存</el-button>
              <el-button :loading="llmTesting" @click="testLLM">测试连通</el-button>
            </el-form-item>
            <el-alert
              v-if="llmTestResult"
              :type="llmTestResult.ok ? 'success' : 'error'"
              :closable="false"
              show-icon
            >
              <template v-if="llmTestResult.ok">
                ✓ 连通正常 · 延迟 {{ llmTestResult.latency_ms }}ms<br />
                示例输出：{{ llmTestResult.sample_output }}
              </template>
              <template v-else>
                ✗ 连通失败：{{ llmTestResult.error }}
              </template>
            </el-alert>
          </el-form>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card>
          <template #header><strong>Embedding</strong></template>
          <el-form label-width="110px" size="default">
            <el-form-item label="Base URL">
              <el-input v-model="embForm.base_url" placeholder="如 https://api.deepseek.com/v1" />
            </el-form-item>
            <el-form-item label="Model">
              <el-input v-model="embForm.model" placeholder="如 BAAI/bge-m3" />
            </el-form-item>
            <el-form-item label="API Key">
              <el-input
                v-model="embForm.api_key"
                type="password"
                show-password
                :placeholder="embKeyMasked ? '已保存（留空则保留原值）' : '在此粘贴 API key'"
              />
            </el-form-item>
            <el-form-item label="Dim">
              <el-input-number v-model="embForm.dim" :min="64" :max="4096" :step="64" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="cfg.saving" @click="saveEmbedding">保存</el-button>
              <el-button :loading="embTesting" @click="testEmbedding">测试连通</el-button>
            </el-form-item>
            <el-alert
              v-if="embTestResult"
              :type="embTestResult.ok ? 'success' : 'error'"
              :closable="false"
              show-icon
            >
              <template v-if="embTestResult.ok">
                ✓ 连通正常 · 延迟 {{ embTestResult.latency_ms }}ms
                <div v-if="embTestResult.sample_output">{{ embTestResult.sample_output }}</div>
              </template>
              <template v-else>
                ✗ 连通失败：{{ embTestResult.error }}
              </template>
            </el-alert>
          </el-form>
        </el-card>
      </el-col>
    </el-row>

    <h3 class="section-title">工具配置</h3>
    <div class="page-hint">
      问答 Agent 的扩展工具:联网搜索、示意图生成、绘图平台模板推荐。保存后立即生效,无需重启;
      留空对应工具会自动从 Agent 的可用工具列表中消失。
    </div>
    <el-row :gutter="16">
      <el-col :span="8">
        <el-card>
          <template #header><strong>联网搜索（Tavily）</strong></template>
          <el-form label-width="90px" size="default">
            <el-form-item label="API Key">
              <el-input
                v-model="tavilyForm.api_key"
                type="password"
                show-password
                :placeholder="tavilyKeyMasked ? '已保存（留空则保留原值）' : '在此粘贴 Tavily key'"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="toolsSaving" @click="saveTavily">保存</el-button>
              <el-button :loading="tavilyTesting" @click="testTavily">测试连通</el-button>
            </el-form-item>
            <el-alert
              v-if="tavilyTestResult"
              :type="tavilyTestResult.ok ? 'success' : 'error'"
              :closable="false"
              show-icon
            >
              <template v-if="tavilyTestResult.ok">
                ✓ 连通正常 · 延迟 {{ tavilyTestResult.latency_ms }}ms
              </template>
              <template v-else>✗ 连通失败：{{ tavilyTestResult.error }}</template>
            </el-alert>
          </el-form>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card>
          <template #header><strong>图片生成（OpenAI 兼容）</strong></template>
          <el-form label-width="90px" size="default">
            <el-form-item label="Base URL">
              <el-input v-model="imagegenForm.base_url" placeholder="如 https://api.xxx.com/v1" />
            </el-form-item>
            <el-form-item label="Model">
              <el-input v-model="imagegenForm.model" placeholder="如 gpt-image-1" />
            </el-form-item>
            <el-form-item label="API Key">
              <el-input
                v-model="imagegenForm.api_key"
                type="password"
                show-password
                :placeholder="imgKeyMasked ? '已保存（留空则保留原值）' : '在此粘贴 API key'"
              />
            </el-form-item>
            <el-form-item label="尺寸">
              <el-input v-model="imagegenForm.size" placeholder="默认 1024x1024" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="toolsSaving" @click="saveImagegen">保存</el-button>
              <el-button :loading="imgTesting" @click="testImagegen">测试连通</el-button>
            </el-form-item>
            <el-alert
              v-if="imgTestResult"
              :type="imgTestResult.ok ? 'success' : 'error'"
              :closable="false"
              show-icon
            >
              <template v-if="imgTestResult.ok">
                ✓ {{ imgTestResult.sample_output || "连通正常" }} · 延迟 {{ imgTestResult.latency_ms }}ms
              </template>
              <template v-else>✗ 连通失败：{{ imgTestResult.error }}</template>
            </el-alert>
          </el-form>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card>
          <template #header><strong>绘图平台（模板推荐 / 服务端绘图）</strong></template>
          <el-form label-width="90px" size="default">
            <el-form-item label="平台地址">
              <el-input v-model="zhihuituForm.base_url" placeholder="如 https://draw.example.com" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="toolsSaving" @click="saveZhihuitu">保存</el-button>
              <el-button :loading="zhtTesting" @click="testZhihuitu">测试连通</el-button>
            </el-form-item>
            <el-alert
              v-if="zhtTestResult"
              :type="zhtTestResult.ok ? 'success' : 'error'"
              :closable="false"
              show-icon
            >
              <template v-if="zhtTestResult.ok">
                ✓ {{ zhtTestResult.sample_output || "连通正常" }} · 延迟 {{ zhtTestResult.latency_ms }}ms
              </template>
              <template v-else>✗ 连通失败：{{ zhtTestResult.error }}</template>
            </el-alert>
            <div class="muted-tip" style="margin-left: 0">
              模板列表与缩略图实时从该平台拉取,新模板上架自动出现。账号无需配置:
              问答触发绘图时以提问用户自己的登录态同步到绘图平台,生成的图表自动存入其「我的图表」。
            </div>
          </el-form>
        </el-card>
      </el-col>
    </el-row>

    <el-collapse v-model="advancedOpen" style="margin-top: 16px" v-loading="growthLoading">
      <el-collapse-item title="高级设置（飞轮 / 合并阈值）" name="growth">
        <p class="page-hint">
          这些参数影响知识库自动消化与去重行为。除非你明确知道在调什么,否则保留默认值。
        </p>
        <el-form label-position="top" size="small" style="max-width: 720px">
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="实体合并自动阈值（余弦相似度）">
                <el-input-number
                  v-model="growth.auto_merge_cosine_threshold"
                  :min="0"
                  :max="1"
                  :step="0.01"
                  :precision="2"
                />
                <div class="muted-tip">≥ 该阈值时直接合并到已有实体。默认 0.92。</div>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="实体合并审核阈值">
                <el-input-number
                  v-model="growth.suggest_merge_cosine_threshold"
                  :min="0"
                  :max="1"
                  :step="0.01"
                  :precision="2"
                />
                <div class="muted-tip">该阈值～自动阈值之间会进入待审核。默认 0.75。</div>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="answer-quality 入队下限">
                <el-input-number
                  v-model="growth.answer_quality_gap_cutoff"
                  :min="0"
                  :max="1"
                  :step="0.05"
                  :precision="2"
                />
                <div class="muted-tip">答得不好(score &lt; 该值)自动入 gap-queue。</div>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="热门主题阈值（30 天提问次数）">
                <el-input-number v-model="growth.hot_topic_threshold" :min="1" :max="100" />
                <div class="muted-tip">超过该次数 + gap_share &gt; 0.5 → 升格候选。</div>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="deep-dive 必填章节数">
                <el-input-number v-model="growth.deep_dive_required_sections" :min="1" :max="10" />
                <div class="muted-tip">少于该数量触发 gap_flag。</div>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="启用 IE 领域风格 prompt">
                <el-switch v-model="growth.domain_ie" />
                <div class="muted-tip">影响 query / digest 答题骨架。</div>
              </el-form-item>
            </el-col>
          </el-row>
          <el-button type="primary" :loading="growthSaving" @click="saveGrowth">
            保存高级设置
          </el-button>
        </el-form>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.page-title { margin-bottom: 6px; }
.section-title { margin: 20px 0 6px; }
.page-hint {
  margin-bottom: 16px;
  color: var(--text-light);
  font-size: 13px;
}
.muted-tip {
  margin-left: 12px;
  color: var(--text-light);
  font-size: 12px;
}
</style>
