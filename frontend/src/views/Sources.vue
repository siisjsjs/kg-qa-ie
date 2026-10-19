<script setup>
import { onMounted, reactive, ref, computed } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { ingestionApi, wikiApi } from "@/api";
import { useSourcesStore } from "@/stores/sources";

const router = useRouter();
const sourcesStore = useSourcesStore();
const docs = computed(() => sourcesStore.docs);
const fileList = ref([]);
const uploading = ref(false);
const tags = ref("");
const sourceType = ref("md");
const uploadRef = ref(null);

// 上传目标知识库("书"):上传前必须先选,素材连同 topic/entity 都落到该库。
const book = ref("");
const books = ref([]);
const newBookName = ref("");
const creatingBook = ref(false);

async function loadBooks() {
  try {
    const data = await wikiApi.books();
    books.value = (data?.books || []).map((b) => b.book).filter(Boolean);
  } catch (e) {
    // 概览拉取失败不阻塞上传页,仅置空,用户可现场新建
    books.value = [];
  }
}

async function createBookInline() {
  const name = newBookName.value.trim();
  if (!name) {
    ElMessage.warning("请输入知识库名称");
    return;
  }
  if (name === "未分类") {
    ElMessage.warning("「未分类」为系统保留桶,请换个名字");
    return;
  }
  creatingBook.value = true;
  try {
    const data = await wikiApi.createBook(name);
    const created = data?.book || name;
    if (!books.value.includes(created)) books.value.push(created);
    book.value = created;
    newBookName.value = "";
    ElMessage.success(data?.created === false ? `已存在「${created}」,已选中` : `已新建「${created}」`);
  } catch (e) {
    ElMessage.error("新建知识库失败:" + (e.response?.data?.detail || e.message));
  } finally {
    creatingBook.value = false;
  }
}
const sourceTypeOptions = [
  { label: "通用 Markdown 文档", value: "md" },
  { label: "网页正文", value: "web" },
  { label: "PDF 转 md", value: "pdf" },
  { label: "PPT 转 md", value: "ppt" },
  { label: "微信公众号", value: "wechat" },
  { label: "知乎", value: "zhihu" },
  { label: "小红书", value: "xiaohongshu" },
  { label: "推特", value: "tweet" },
  { label: "纯文本", value: "plain_text" },
];

const privacyDialogVisible = ref(false);
const PRIVACY_CHECKLIST = [
  "已确认上传内容不包含手机号、身份证号等隐私",
  "已确认不包含 API key、密码、登录凭证",
  "已确认不包含他人未授权的私密信息",
];
const privacyChecks = reactive({ 0: false, 1: false, 2: false });
const allChecked = () => Object.values(privacyChecks).every(Boolean);

const STAGE_LABEL = {
  cache_check: "1/4 缓存检查",
  step1_extract: "2/4 结构化分析",
  step2_compose: "3/4 页面生成",
  post_process: "4/4 后处理",
};
const STATUS_TYPE = {
  pending: "info",
  running: "warning",
  succeeded: "success",
  failed: "danger",
};

function onUploadClick() {
  if (!book.value) {
    ElMessage.warning("请先选择目标知识库");
    return;
  }
  if (!fileList.value.length) {
    ElMessage.warning("请先选择 .md 文件");
    return;
  }
  Object.keys(privacyChecks).forEach((k) => (privacyChecks[k] = false));
  privacyDialogVisible.value = true;
}

async function confirmUpload() {
  if (!allChecked()) {
    ElMessage.warning("请勾选所有隐私自查项");
    return;
  }
  privacyDialogVisible.value = false;
  uploading.value = true;
  try {
    const files = fileList.value.map((f) => f.raw || f);
    const queued = [];
    // 1) 串行上传(获取 doc_id),立刻在表头插入"待运行"行
    for (const file of files) {
      try {
        const data = await ingestionApi.upload(file, {
          source_type: sourceType.value,
          tags: tags.value,
          book: book.value,
        });
        const id = data?.document_id || data?.data?.document_id || data?.doc_id || data?.id;
        if (id) {
          queued.push(id);
          sourcesStore.addDoc({
            doc_id: id,
            filename: file.name,
            parse_status: "pending",
            current_stage: null,
            stages_done: 0,
            stages_total: 4,
            progress: 0,
            parse_error: null,
            failed_job_id: null,
            source_slug: null,
          });
        }
      } catch (e) {
        ElMessage.error(`${file.name} 上传失败:` + (e.response?.data?.detail || e.message));
      }
    }
    if (!queued.length) return;

    // 2) 并行触发 run(BG 任务,202 立即返回);触发失败的行直接标失败
    await Promise.all(
      queued.map((id) =>
        ingestionApi.run(id).catch((e) => {
          const row = sourcesStore.findDoc(id);
          if (row) {
            row.parse_status = "failed";
            row.parse_error = `触发失败:${e.response?.data?.detail || e.message}`;
          }
        })
      )
    );

    ElMessage.success(`已加入流水线 ${queued.length} 个素材,正在处理...`);
    fileList.value = [];
    uploadRef.value?.clearFiles?.();

    // 3) 启动短轮询
    sourcesStore.startPolling();
  } finally {
    uploading.value = false;
  }
}

function stageDisplay(row) {
  if (row.parse_status === "succeeded") return "完成";
  if (row.parse_status === "pending") return "等待中";
  if (row.parse_status === "failed") {
    return `失败 · ${STAGE_LABEL[row.current_stage] || row.current_stage || "?"}`;
  }
  return STAGE_LABEL[row.current_stage] || "运行中";
}

async function deleteDoc(row) {
  try {
    if (row.source_slug) {
      // 已成功:级联清 wiki + documents + jobs + raw
      // source 文件可能已被删过(半坏素材)→ 后端现在会继续清残留;
      // 万一仍报错也不该中断,后面还有 doc_id 兜底清理。
      try {
        await ingestionApi.deleteSource(row.source_slug);
      } catch (e) {
        if (e.response?.status !== 404) {
          console.warn("deleteSource failed, fallback to deleteDocument:", e);
        }
      }
    }
    // 不管是否级联过,都按 doc_id 清一次 documents/jobs/raw,
    // 兜底 source_slug=null(失败/未完成)的素材,以及防止 source 删除留 documents 残留
    if (row.doc_id) {
      try {
        await ingestionApi.deleteDocument(row.doc_id);
      } catch (e) {
        // 404 视作已删,其它错误才报
        if (e.response?.status !== 404) throw e;
      }
    }
    sourcesStore.removeDoc(row);
    ElMessage.success("已删除");
  } catch (e) {
    ElMessage.error("删除失败:" + (e.response?.data?.detail || e.message));
  }
}

// ===== 错误详情 Drawer =====
const errorDrawerVisible = ref(false);
const errorDetail = ref(null);
async function openErrorDrawer(row) {
  errorDrawerVisible.value = true;
  errorDetail.value = {
    filename: row.filename,
    stage: row.current_stage,
    error: row.parse_error,
    exception_type: null,
    traceback: null,
    loading: !!row.failed_job_id,
  };
  if (row.failed_job_id) {
    try {
      const d = await ingestionApi.jobDetail(row.failed_job_id);
      errorDetail.value = {
        filename: row.filename,
        stage: d.stage || row.current_stage,
        error: d.error || row.parse_error,
        exception_type: d.exception_type,
        traceback: d.traceback,
        correlation_id: d.correlation_id,
        loading: false,
      };
    } catch (e) {
      errorDetail.value.loading = false;
      errorDetail.value.error =
        (errorDetail.value.error || "") +
        `\n\n[拉取详情失败] ${e.response?.data?.detail || e.message}`;
    }
  }
}

onMounted(async () => {
  // 跨路由 / 跨刷新恢复:从后端拉最近 24h 文档,重建本会话流水线列表 + 接续轮询
  await Promise.all([loadBooks(), sourcesStore.bootstrap()]);
  sourcesStore.startPolling();
});
</script>

<template>
  <div class="sources-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">素材管理</h2>
        <p class="page-subtitle">上传 .md → 隐私自查 → 4 阶段流水线 → 跨 source 合并</p>
      </div>
    </div>

    <el-card shadow="never">
      <el-form inline label-width="90px">
        <el-form-item label="目标知识库" required>
          <el-select
            v-model="book"
            placeholder="请选择素材归属的知识库"
            style="width: 240px"
            filterable
          >
            <el-option v-for="b in books" :key="b" :label="b" :value="b" />
          </el-select>
        </el-form-item>
        <el-form-item label="新建知识库">
          <el-input
            v-model="newBookName"
            placeholder="没有合适的库?现场新建"
            style="width: 220px"
            @keyup.enter="createBookInline"
          />
          <el-button
            style="margin-left: 8px"
            :loading="creatingBook"
            @click="createBookInline"
          >新建</el-button>
        </el-form-item>
        <el-form-item label="素材类型">
          <el-select v-model="sourceType" style="width: 200px">
            <el-option
              v-for="o in sourceTypeOptions"
              :key="o.value"
              :label="o.label"
              :value="o.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="标签">
          <el-input
            v-model="tags"
            placeholder="逗号分隔，如:精益生产,VSM"
            style="width: 280px"
          />
        </el-form-item>
      </el-form>

      <el-upload
        ref="uploadRef"
        v-model:file-list="fileList"
        multiple
        accept=".md,.markdown"
        :auto-upload="false"
        drag
      >
        <el-icon class="upload-icon"><UploadFilled /></el-icon>
        <div class="el-upload__text">
          拖入 / 点击选择 <em>.md</em> 文件
        </div>
        <template #tip>
          <div class="upload-tip">仅支持 .md(V1 限制);其它格式请先转 md</div>
        </template>
      </el-upload>

      <div class="actions-row">
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="!fileList.length || !book"
          @click="onUploadClick"
        >
          开始入库 ({{ fileList.length }})
        </el-button>
        <el-button @click="fileList = []" :disabled="!fileList.length">清空</el-button>
      </div>
    </el-card>

    <el-card shadow="never" style="margin-top: 14px">
      <template #header>
        <div class="card-head">
          <strong>本次会话流水线</strong>
          <span class="hint">每个文档一行 · 进度实时刷新</span>
        </div>
      </template>
      <el-empty v-if="!docs.length" description="暂无任务,选择 .md 文件后点开始入库" :image-size="80" />
      <el-table v-else :data="docs" stripe size="small">
        <el-table-column prop="filename" label="文件" min-width="220" show-overflow-tooltip />
        <el-table-column label="当前阶段" width="180">
          <template #default="{ row }">
            <el-tag size="small" :type="STATUS_TYPE[row.parse_status]">
              {{ stageDisplay(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" min-width="240">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress || 0"
              :status="
                row.parse_status === 'failed'
                  ? 'exception'
                  : row.parse_status === 'succeeded'
                  ? 'success'
                  : ''
              "
              :format="(p) => `${row.stages_done}/${row.stages_total} · ${p}%`"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              :disabled="!row.source_slug"
              @click="router.push(`/kb/wiki/${encodeURIComponent(row.source_slug)}`)"
            >查看</el-button>
            <el-button
              v-if="row.parse_status === 'failed'"
              link
              type="warning"
              size="small"
              @click="openErrorDrawer(row)"
            >详情</el-button>
            <el-popconfirm
              title="删除该素材并级联清理?"
              @confirm="deleteDoc(row)"
            >
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="privacyDialogVisible" title="隐私自查" width="460px">
      <p style="margin-top: 0; color: var(--text-light)">
        上传前请确认以下隐私自查项(任何一项未确认将无法继续):
      </p>
      <el-checkbox
        v-for="(item, idx) in PRIVACY_CHECKLIST"
        :key="idx"
        v-model="privacyChecks[idx]"
        style="display: block; margin-bottom: 8px"
      >
        {{ item }}
      </el-checkbox>
      <template #footer>
        <el-button @click="privacyDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!allChecked()" @click="confirmUpload">
          已确认,开始上传
        </el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="errorDrawerVisible"
      title="错误详情"
      size="640px"
      direction="rtl"
    >
      <div v-if="errorDetail" class="error-drawer">
        <p><strong>文件:</strong> {{ errorDetail.filename }}</p>
        <p>
          <strong>失败阶段:</strong>
          {{ STAGE_LABEL[errorDetail.stage] || errorDetail.stage || "—" }}
        </p>
        <p>
          <strong>异常类型:</strong>
          {{ errorDetail.exception_type || "未知" }}
        </p>
        <p v-if="errorDetail.correlation_id">
          <strong>correlation_id:</strong>
          <code>{{ errorDetail.correlation_id }}</code>
        </p>
        <p><strong>错误信息:</strong></p>
        <pre class="error-msg">{{ errorDetail.error || "(无)" }}</pre>
        <template v-if="errorDetail.loading">
          <p style="color: var(--text-light)">正在加载 traceback...</p>
        </template>
        <template v-else>
          <p v-if="errorDetail.traceback"><strong>Traceback(后端日志摘录):</strong></p>
          <pre v-if="errorDetail.traceback" class="error-tb">{{ errorDetail.traceback }}</pre>
          <p v-else style="color: var(--text-light)">
            未找到 traceback;查看 backend/logs/app.log 中 correlation_id 上下文行。
          </p>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.actions-row {
  margin-top: 12px;
}
.upload-icon {
  font-size: 36px;
  color: var(--primary-color);
}
.upload-tip {
  color: var(--text-light);
  font-size: 12px;
}
.card-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.card-head .hint {
  color: var(--text-light);
  font-size: 12px;
}
.error-drawer {
  padding: 0 20px;
}
.error-drawer pre {
  background: #1f2937;
  color: #f3f4f6;
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 360px;
  overflow: auto;
}
.error-drawer pre.error-msg {
  background: #fef2f2;
  color: #991b1b;
  max-height: 120px;
}
.error-drawer code {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 12px;
}
</style>
