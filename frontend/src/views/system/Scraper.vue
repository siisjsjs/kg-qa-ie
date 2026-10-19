<script setup>
import { onMounted, reactive, ref, computed } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import MarkdownViewer from "@/components/MarkdownViewer.vue";
import { adminApi, scraperApi } from "@/api";

// ===== firecrawl 配置 =====
const cfgForm = reactive({
  api_key: "",
  base_url: "",
});
const cfgLoading = ref(false);
const cfgSaving = ref(false);
const cfgTesting = ref(false);
const cfgTestResult = ref(null);
const keyMasked = computed(
  () => cfgForm.api_key && cfgForm.api_key.includes("*")
);

async function loadConfig() {
  cfgLoading.value = true;
  try {
    const data = await adminApi.getFirecrawl();
    Object.assign(cfgForm, {
      api_key: data?.api_key ?? "",
      base_url: data?.base_url ?? "",
    });
  } catch (e) {
    ElMessage.error(
      "读取配置失败：" + (e.response?.data?.detail || e.message)
    );
  } finally {
    cfgLoading.value = false;
  }
}

async function saveConfig() {
  cfgSaving.value = true;
  try {
    const payload = {
      base_url: cfgForm.base_url || "",
    };
    // 屏蔽串视为不修改;空串清空(后端 _api_key_for_write 会忽略空串,所以"清空"暂不支持,符合预期)
    if (cfgForm.api_key && !cfgForm.api_key.includes("*")) {
      payload.api_key = cfgForm.api_key;
    }
    await adminApi.putFirecrawl(payload);
    ElMessage.success("已保存");
    await loadConfig();
  } catch (e) {
    ElMessage.error(
      "保存失败：" + (e.response?.data?.detail || e.message)
    );
  } finally {
    cfgSaving.value = false;
  }
}

async function testConfig() {
  cfgTesting.value = true;
  cfgTestResult.value = null;
  try {
    cfgTestResult.value = await adminApi.testConnection("firecrawl");
  } catch (e) {
    cfgTestResult.value = {
      ok: false,
      error: e.response?.data?.detail || e.message,
    };
  } finally {
    cfgTesting.value = false;
  }
}

// ===== URL 抓取 =====
const url = ref("");
const scraping = ref(false);
const result = ref(null); // { markdown, title, source_url, scraped_at, metadata }

function isValidUrl(s) {
  try {
    const u = new URL(s);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

async function doScrape() {
  const target = url.value.trim();
  if (!target) {
    ElMessage.warning("请粘贴一个 URL");
    return;
  }
  if (!isValidUrl(target)) {
    ElMessage.warning("URL 格式不对,只支持 http(s)://");
    return;
  }
  scraping.value = true;
  result.value = null;
  try {
    const data = await scraperApi.scrape(target);
    result.value = data;
    ElMessage.success(`抓取完成:${data.title}`);
  } catch (e) {
    const msg = e.response?.data?.detail || e.message;
    ElMessage.error("抓取失败:" + msg);
  } finally {
    scraping.value = false;
  }
}

function clearResult() {
  result.value = null;
}

// ===== 下载 =====
function safeFilename(s) {
  // 文件名禁用字符全替换为下划线
  return (s || "scraped")
    .replace(/[\\/:*?"<>|\r\n\t]/g, "_")
    .replace(/^\.+/, "")
    .slice(0, 120) || "scraped";
}

function buildMarkdownWithFrontmatter(r) {
  // LLM 整合后的 md 已自带 frontmatter,直接返回原内容
  const md = r.markdown || "";
  if (md.trimStart().startsWith("---")) return md;
  // fallback:LLM 没产 frontmatter 时手动补一层
  const fm = [
    "---",
    `title: ${(r.title || "").replace(/"/g, '\\"')}`,
    `source_url: ${r.source_url || ""}`,
    `scraped_at: ${r.scraped_at || ""}`,
    "source_type: web",
    "---",
    "",
  ].join("\n");
  return fm + md;
}

function download() {
  if (!result.value) return;
  const text = buildMarkdownWithFrontmatter(result.value);
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = `${safeFilename(result.value.title)}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // 下个 tick 释放
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

async function copyMarkdown() {
  if (!result.value) return;
  try {
    await navigator.clipboard.writeText(
      buildMarkdownWithFrontmatter(result.value)
    );
    ElMessage.success("已复制 markdown 到剪贴板");
  } catch (e) {
    ElMessage.error("复制失败:" + e.message);
  }
}

onMounted(loadConfig);
</script>

<template>
  <div>
    <div class="page-header">
      <div>
        <h2 class="page-title">网页抓取</h2>
        <p class="page-subtitle">
          firecrawl 抓原始 → LLM 抽取工业工程相关内容并自起标题 → 输出标准 md。不入库,需要时手动到素材管理上传。
        </p>
      </div>
    </div>

    <!-- 1. firecrawl API 配置 -->
    <el-card shadow="never" class="config-card" v-loading="cfgLoading">
      <template #header>
        <div class="card-header">
          <strong>firecrawl API 配置</strong>
          <el-tag
            v-if="cfgTestResult"
            :type="cfgTestResult.ok ? 'success' : 'danger'"
            size="small"
          >
            {{
              cfgTestResult.ok
                ? `连接 OK (${cfgTestResult.latency_ms} ms)`
                : "连接失败"
            }}
          </el-tag>
        </div>
      </template>
      <el-form :model="cfgForm" label-width="100px" label-position="left">
        <el-form-item label="API Key">
          <el-input
            v-model="cfgForm.api_key"
            type="password"
            show-password
            placeholder="fc-xxxx... ; 留空表示不修改已有 key"
            :disabled="cfgLoading"
          />
          <div class="hint" v-if="keyMasked">
            当前显示的是已保存的屏蔽值,直接保存不会覆盖真 key;要改请先清空再粘贴新值。
          </div>
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input
            v-model="cfgForm.base_url"
            placeholder="留空使用 https://api.firecrawl.dev"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="cfgSaving"
            @click="saveConfig"
            >保存</el-button
          >
          <el-button
            :loading="cfgTesting"
            @click="testConfig"
            >测试连通性</el-button
          >
          <span class="hint" v-if="cfgTestResult && !cfgTestResult.ok">
            {{ cfgTestResult.error }}
          </span>
          <span class="hint" v-else-if="cfgTestResult && cfgTestResult.ok">
            {{ cfgTestResult.sample_output }}
          </span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 2. URL 抓取 -->
    <el-row :gutter="14" class="scrape-row">
      <el-col :xs="24" :md="10">
        <el-card shadow="never">
          <template #header><strong>URL 抓取</strong></template>
          <el-input
            v-model="url"
            placeholder="https://www.example.com/article"
            clearable
            @keyup.enter="doScrape"
          />
          <div class="actions">
            <el-button
              type="primary"
              :loading="scraping"
              @click="doScrape"
              >抓取并预览</el-button
            >
            <el-button v-if="result" @click="clearResult">清空</el-button>
          </div>
          <div class="hint">
            支持公众号 / 知乎 / 普通网页等公开页面;小红书、视频字幕这类反爬站点可能失败。整合需要 LLM 调用,通常 30-90 秒。
          </div>
        </el-card>
      </el-col>

      <!-- 3. 预览 + 下载 -->
      <el-col :xs="24" :md="14">
        <el-card shadow="never" v-loading="scraping">
          <template #header>
            <div class="card-header">
              <strong>预览</strong>
              <div v-if="result" class="preview-actions">
                <el-button size="small" @click="copyMarkdown">复制</el-button>
                <el-button size="small" type="primary" @click="download"
                  >下载 .md</el-button
                >
              </div>
            </div>
          </template>
          <el-empty
            v-if="!result"
            description="抓取后这里会显示 markdown 预览"
          />
          <template v-else>
            <div class="meta">
              <div><b>标题:</b>{{ result.title }}</div>
              <div>
                <b>来源:</b>
                <a :href="result.source_url" target="_blank" rel="noopener">{{
                  result.source_url
                }}</a>
              </div>
              <div>
                <b>抓取时间:</b>{{ new Date(result.scraped_at).toLocaleString() }}
                · 字数 {{ result.markdown.length }}
              </div>
            </div>
            <div class="preview-body">
              <MarkdownViewer :source="result.markdown" />
            </div>
          </template>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 12px;
}
.page-subtitle {
  color: var(--el-text-color-secondary);
  margin: 4px 0 0;
  font-size: 13px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.config-card {
  margin-bottom: 14px;
}
.scrape-row {
  margin-top: 0;
}
.actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
.hint {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.preview-actions {
  display: flex;
  gap: 6px;
}
.meta {
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
  margin-bottom: 10px;
  word-break: break-all;
}
.meta a {
  color: var(--el-color-primary);
}
.preview-body {
  max-height: 60vh;
  overflow: auto;
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 10px;
}
</style>
