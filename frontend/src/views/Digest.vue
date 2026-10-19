<script setup>
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import MarkdownViewer from "@/components/MarkdownViewer.vue";
import { digestApi } from "@/api";

const router = useRouter();

const input = ref("");
const mode = ref("auto"); // auto | deep_dive | comparison | timeline
const result = ref(null);
const loading = ref(false);

const MODE_OPTIONS = [
  { label: "自动判断", value: "auto", hint: '按关键词自动选模板' },
  { label: "深度报告 (deep_dive)", value: "deep_dive", hint: "5 个 H2：背景 / 核心观点 / 视角对比 / 知识脉络 / 尚待解决" },
  { label: "对比 (comparison)", value: "comparison", hint: "IE 6 维表" },
  { label: "时间线 (timeline)", value: "timeline", hint: "Mermaid gantt 或文本时间线" },
];

const detectedMode = computed(() => {
  if (mode.value !== "auto") return mode.value;
  const t = input.value;
  if (/对比|比较|vs|VS/.test(t)) return "comparison";
  if (/时间线|按时间|发展史|演进/.test(t)) return "timeline";
  if (/深度|综述|digest|分析/i.test(t)) return "deep_dive";
  return "deep_dive";
});

async function run() {
  if (!input.value.trim()) {
    ElMessage.warning("请输入主题");
    return;
  }
  loading.value = true;
  try {
    const data = await digestApi.create({
      topic: input.value.trim(),
      mode: detectedMode.value,
    });
    result.value = data?.data || data;
    ElMessage.success("生成完成");
  } catch (e) {
    ElMessage.error("生成失败：" + (e.response?.data?.detail || e.message));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div>
    <div class="page-header">
      <div>
        <h2 class="page-title">深度综合</h2>
        <p class="page-subtitle">三模板：deep_dive / comparison / timeline</p>
      </div>
    </div>

    <el-row :gutter="14">
      <el-col :xs="24" :md="10">
        <el-card shadow="never">
          <template #header><strong>输入</strong></template>
          <el-input
            v-model="input"
            type="textarea"
            :rows="4"
            placeholder="比如：对比精益生产和六西格玛"
          />
          <el-form label-width="80px" style="margin-top: 14px">
            <el-form-item label="模板">
              <el-select v-model="mode" style="width: 100%">
                <el-option
                  v-for="o in MODE_OPTIONS"
                  :key="o.value"
                  :label="o.label"
                  :value="o.value"
                >
                  <span>{{ o.label }}</span>
                  <span class="opt-hint">{{ o.hint }}</span>
                </el-option>
              </el-select>
            </el-form-item>
          </el-form>
          <p class="muted" v-if="mode === 'auto'">
            自动检测：<el-tag size="small">{{ detectedMode }}</el-tag>
          </p>
          <el-button
            type="primary"
            :loading="loading"
            @click="run"
            style="margin-top: 8px"
          >生成报告</el-button>
        </el-card>

        <el-card v-if="result?.gaps?.length" shadow="never" style="margin-top: 12px">
          <template #header><strong>知识缺口（已入 gap-queue）</strong></template>
          <ul class="gap-list">
            <li v-for="g in result.gaps" :key="g.id || g.text">
              {{ g.text || g.reason }}
            </li>
          </ul>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="14">
        <el-card shadow="never" class="report-card">
          <template #header>
            <div class="report-header">
              <strong>{{ result?.title || "报告" }}</strong>
              <el-tag v-if="result?.mode" size="small">{{ result.mode }}</el-tag>
              <el-tag v-if="result?.score !== undefined" size="small" type="success">
                完整度 {{ Math.round((result.score || 0) * 100) }}
              </el-tag>
              <el-button
                v-if="result?.slug"
                size="small"
                link
                type="primary"
                @click="router.push(`/kb/wiki/${encodeURIComponent(result.slug)}`)"
              >在 Wiki 查看</el-button>
            </div>
          </template>
          <el-empty v-if="!result" description="尚未生成" />
          <MarkdownViewer v-else :source="result.markdown || result.content || ''" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.muted { font-size: 12px; color: var(--text-light); margin: 6px 0 0; }
.opt-hint { color: var(--text-light); margin-left: 12px; font-size: 12px; }
.report-card { min-height: 400px; }
.report-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.gap-list { padding-left: 18px; margin: 0; }
.gap-list li { font-size: 13px; line-height: 1.7; }
</style>
