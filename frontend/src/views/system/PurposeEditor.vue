<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import MarkdownViewer from "@/components/MarkdownViewer.vue";
import { adminApi } from "@/api";

const content = ref("");
const loading = ref(false);
const saving = ref(false);

async function load() {
  loading.value = true;
  try {
    const data = await adminApi.getPurpose();
    const body = data?.data || data || {};
    content.value = body.raw_markdown || body.content || body.markdown || "";
  } catch (e) {
    ElMessage.error("加载失败：" + (e.response?.data?.detail || e.message));
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  try {
    await adminApi.putPurpose({ raw_markdown: content.value });
    ElMessage.success("已保存");
  } catch (e) {
    ElMessage.error("保存失败：" + (e.response?.data?.detail || e.message));
  } finally {
    saving.value = false;
  }
}

function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    save();
  }
}

onMounted(load);
</script>

<template>
  <div v-loading="loading">
    <div class="page-header">
      <div>
        <h2 class="page-title">purpose.md 编辑</h2>
        <p class="page-subtitle">研究目标 / 关键问题 / 研究范围 → 影响 ingest 取舍。Ctrl+S 保存。</p>
      </div>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </div>

    <el-row :gutter="14" class="editor-row">
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="editor-card">
          <template #header><strong>编辑</strong></template>
          <el-input
            v-model="content"
            type="textarea"
            :rows="22"
            resize="none"
            @keydown="onKeydown"
            placeholder="# 研究目标&#10;...&#10;# 关键问题&#10;...&#10;# 研究范围&#10;..."
          />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="editor-card">
          <template #header><strong>预览</strong></template>
          <MarkdownViewer :source="content" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 12px; }
.editor-card :deep(.el-card__body) { padding: 12px; }
</style>
