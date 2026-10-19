<script setup>
// 右侧分栏面板(改造计划 §8.3):
//   mode=templates → 智绘图模板卡列表(缩略图 + 名称/描述/分类 + 前往编辑器)
//   mode=image     → 生成图片大图预览
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { Close, Picture, DataAnalysis, Download, EditPen, Document } from "@element-plus/icons-vue";
import http from "@/api/http";
import { openDrawingPlatform } from "@/utils/sso";

const props = defineProps({
  mode: { type: String, default: "templates" }, // templates | image
  templates: { type: Array, default: () => [] },
  baseUrl: { type: String, default: "" },
  // {url, prompt, caption, chart:{template_id,name,base_url,editor_path}}
  image: { type: Object, default: null },
});

const emit = defineEmits(["close"]);

// ===== 图片模式:下载 / 双击原图 / 跳转绘图编辑器 =====
const downloading = ref(false);
async function downloadImage() {
  const url = props.image?.url;
  if (!url || downloading.value) return;
  downloading.value = true;
  try {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const a = document.createElement("a");
    const objectUrl = URL.createObjectURL(blob);
    a.href = objectUrl;
    a.download = `示意图_${(props.image?.chart?.name || "").trim() || Date.now()}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (e) {
    ElMessage.error("下载失败，请稍后重试");
  } finally {
    downloading.value = false;
  }
}

function openImageInNewTab() {
  const url = props.image?.url;
  if (url) window.open(url, "_blank", "noopener");
}

// 模板声明支持 xlsx 且图表已存进「我的图表」→ 显示 Excel 导出。
// 走后端代理直接下载(kgqa 换用户身份 token → 智绘图服务端生成),
// 产物与编辑器内手动点「导出 Excel」完全一致,不跳任何页面。
const canExportExcel = computed(
  () =>
    (props.image?.chart?.export_formats || []).includes("xlsx") &&
    !!props.image?.chart?.chart_id
);
const exportingExcel = ref(false);
async function exportExcel() {
  const chart = props.image?.chart;
  if (!chart?.chart_id || exportingExcel.value) return;
  exportingExcel.value = true;
  try {
    const resp = await http.get(`/files/chart-excel/${chart.chart_id}`, {
      responseType: "blob",
    });
    // 优先用后端 Content-Disposition 里的文件名(与编辑器导出同名)
    let filename = `${(chart.name || "图表").trim()}.xlsx`;
    const disposition = resp.headers?.["content-disposition"] || "";
    const star = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const plain = disposition.match(/filename="?([^";]+)"?/i);
    if (star) filename = decodeURIComponent(star[1]);
    else if (plain) filename = plain[1];
    const a = document.createElement("a");
    const objectUrl = URL.createObjectURL(resp.data);
    a.href = objectUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (e) {
    ElMessage.error("Excel 导出失败，请稍后重试");
  } finally {
    exportingExcel.value = false;
  }
}

const editorJumping = ref(false);
async function goChartEditor() {
  const chart = props.image?.chart;
  if (!chart || editorJumping.value) return;
  editorJumping.value = true;
  try {
    const ok = await openDrawingPlatform(
      chart.editor_path || `/mode-select/${chart.template_id}`,
      chart.base_url || props.baseUrl
    );
    if (!ok) ElMessage.warning("请先登录后再前往绘图平台");
  } finally {
    editorJumping.value = false;
  }
}

// 缩略图加载失败 → 占位图(按模板 id 记)
const brokenPreviews = ref(new Set());
watch(
  () => props.templates,
  () => { brokenPreviews.value = new Set(); }
);

function previewSrc(tpl) {
  if (tpl.preview_url) return tpl.preview_url;
  const base = (props.baseUrl || "").replace(/\/+$/, "");
  return `${base}/api/v1/templates/${tpl.id}/preview`;
}

function onPreviewError(tpl) {
  brokenPreviews.value = new Set([...brokenPreviews.value, tpl.id]);
}

const jumping = ref("");
async function goEditor(tpl) {
  if (jumping.value) return;
  jumping.value = tpl.id;
  try {
    const ok = await openDrawingPlatform(
      tpl.editor_path || `/mode-select/${tpl.id}`,
      props.baseUrl
    );
    if (!ok) ElMessage.warning("请先登录后再前往绘图平台");
  } finally {
    jumping.value = "";
  }
}
</script>

<template>
  <aside class="side-panel">
    <header class="sp-head">
      <div class="sp-title">
        <el-icon v-if="mode === 'image'"><Picture /></el-icon>
        <el-icon v-else><DataAnalysis /></el-icon>
        <span>{{
          mode === "image"
            ? (image?.chart ? "图表预览" : "示意图预览")
            : "推荐绘图模板"
        }}</span>
      </div>
      <el-button link :icon="Close" @click="emit('close')" aria-label="关闭面板" />
    </header>

    <div class="sp-body">
      <!-- 图片大图 -->
      <template v-if="mode === 'image'">
        <div v-if="image?.url" class="sp-image-wrap">
          <img
            :src="image.url"
            :alt="image.caption || '示意图'"
            class="sp-image"
            title="双击在新标签页查看原图"
            @dblclick="openImageInNewTab"
          />
          <p v-if="image.caption" class="sp-image-prompt">{{ image.caption }}</p>
          <p class="sp-image-tip">双击图片可在新标签页放大查看</p>
          <div class="sp-image-actions">
            <el-button
              size="small"
              :icon="Download"
              :loading="downloading"
              @click="downloadImage"
            >下载图片</el-button>
            <el-button
              v-if="canExportExcel"
              size="small"
              :icon="Document"
              :loading="exportingExcel"
              @click="exportExcel"
            >导出 Excel 数据</el-button>
            <el-button
              v-if="image.chart"
              type="primary"
              size="small"
              :icon="EditPen"
              :loading="editorJumping"
              @click="goChartEditor"
            >前往绘图编辑器编辑{{ image.chart.name ? `「${image.chart.name}」` : "" }}</el-button>
          </div>
        </div>
        <el-empty v-else description="图片不可用" :image-size="80" />
      </template>

      <!-- 模板卡列表 -->
      <template v-else>
        <el-empty v-if="!templates.length" description="暂无推荐模板" :image-size="80" />
        <div v-for="tpl in templates" :key="tpl.id" class="tpl-card">
          <div class="tpl-preview">
            <img
              v-if="!brokenPreviews.has(tpl.id)"
              :src="previewSrc(tpl)"
              :alt="tpl.name"
              loading="lazy"
              @error="onPreviewError(tpl)"
            />
            <div v-else class="tpl-preview-fallback">
              <el-icon><Picture /></el-icon>
              <span>暂无缩略图</span>
            </div>
          </div>
          <div class="tpl-info">
            <div class="tpl-name-row">
              <span class="tpl-name">{{ tpl.name }}</span>
              <el-tag v-if="tpl.category" size="small" effect="plain">{{ tpl.category }}</el-tag>
            </div>
            <p v-if="tpl.description" class="tpl-desc">{{ tpl.description }}</p>
            <el-button
              type="primary"
              size="small"
              class="tpl-go"
              :loading="jumping === tpl.id"
              @click="goEditor(tpl)"
            >前往绘图编辑器</el-button>
          </div>
        </div>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.side-panel {
  width: 380px;
  flex-shrink: 0;
  border-left: 1px solid var(--border-color);
  background: #fff;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.sp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
}
.sp-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #111827;
}
.sp-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sp-image-wrap { text-align: center; }
.sp-image {
  max-width: 100%;
  border-radius: 10px;
  border: 1px solid #e5e7eb;
  cursor: zoom-in;
}
.sp-image-prompt {
  margin: 10px 0 0;
  font-size: 12.5px;
  color: #6b7280;
  line-height: 1.6;
  text-align: left;
}
.sp-image-tip {
  margin: 6px 0 0;
  font-size: 11.5px;
  color: #b0b7c3;
}
.sp-image-actions {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sp-image-actions .el-button { width: 100%; margin-left: 0; }

.tpl-card {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
}
.tpl-preview {
  height: 170px;
  background: #f8fafc;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid #eef2f7;
}
.tpl-preview img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.tpl-preview-fallback {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: #cbd5e1;
  font-size: 12px;
}
.tpl-preview-fallback .el-icon { font-size: 28px; }
.tpl-info { padding: 12px 14px 14px; }
.tpl-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.tpl-name { font-size: 14px; font-weight: 600; color: #111827; }
.tpl-desc {
  margin: 8px 0 0;
  font-size: 12.5px;
  color: #6b7280;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.tpl-go { margin-top: 10px; width: 100%; }

@media (max-width: 768px) {
  .side-panel {
    position: fixed;
    inset: 0;
    width: 100%;
    z-index: 30;
  }
}
</style>
