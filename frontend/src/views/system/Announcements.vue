<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Plus, Edit, Delete, View, Picture, StarFilled } from "@element-plus/icons-vue";
import { announcementApi } from "@/api";
import MarkdownViewer from "@/components/MarkdownViewer.vue";

const list = ref([]);
const loading = ref(false);
const editorVisible = ref(false);
const previewVisible = ref(false);
const uploading = ref(false);

const empty = () => ({
  id: null,
  title: "",
  body_md: "",
  cover_image_url: "",
  pinned: false,
  visibility: "published",
});
const form = reactive(empty());

async function load() {
  loading.value = true;
  try {
    const data = await announcementApi.listAdmin();
    list.value = data?.items || data?.data?.items || [];
  } catch (e) {
    ElMessage.error("加载公告失败：" + (e.response?.data?.detail || e.message));
  } finally {
    loading.value = false;
  }
}
onMounted(load);

function openCreate() {
  Object.assign(form, empty());
  editorVisible.value = true;
}
function openEdit(a) {
  Object.assign(form, a);
  editorVisible.value = true;
}

async function save() {
  if (!form.title.trim()) {
    ElMessage.warning("请填标题");
    return;
  }
  try {
    const payload = {
      title: form.title.trim(),
      body_md: form.body_md || "",
      cover_image_url: form.cover_image_url || null,
      pinned: !!form.pinned,
      visibility: form.visibility,
    };
    if (form.id) {
      await announcementApi.update(form.id, payload);
      ElMessage.success("已更新");
    } else {
      await announcementApi.create(payload);
      ElMessage.success("已发布");
    }
    editorVisible.value = false;
    await load();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  }
}

async function remove(a) {
  try {
    await ElMessageBox.confirm(`确定删除公告「${a.title}」？`, "确认删除", {
      type: "warning",
    });
  } catch (_) {
    return;
  }
  try {
    await announcementApi.delete(a.id);
    ElMessage.success("已删除");
    await load();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  }
}

async function onUpload(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  uploading.value = true;
  try {
    const res = await announcementApi.uploadImage(file);
    const url = res?.url || res?.data?.url;
    if (url) {
      form.cover_image_url = url;
      ElMessage.success("图片已上传");
    }
  } catch (err) {
    ElMessage.error("上传失败：" + (err.response?.data?.detail || err.message));
  } finally {
    uploading.value = false;
    e.target.value = "";
  }
}

async function insertImageToBody(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  uploading.value = true;
  try {
    const res = await announcementApi.uploadImage(file);
    const url = res?.url || res?.data?.url;
    if (url) {
      const snippet = `\n\n![](${url})\n\n`;
      form.body_md = (form.body_md || "") + snippet;
      ElMessage.success("已插入图片到正文");
    }
  } catch (err) {
    ElMessage.error("上传失败：" + (err.response?.data?.detail || err.message));
  } finally {
    uploading.value = false;
    e.target.value = "";
  }
}

function fmtDate(s) {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString("zh-CN");
  } catch (_) {
    return String(s).slice(0, 16);
  }
}
</script>
<!-- TEMPLATE_PLACEHOLDER -->

<template>
  <div class="announcements" v-loading="loading">
    <div class="page-head">
      <div>
        <h2 class="page-title">公告管理</h2>
        <p class="page-sub">管理员发布、编辑、删除主页公告</p>
      </div>
      <el-button type="primary" :icon="Plus" round @click="openCreate">
        新建公告
      </el-button>
    </div>

    <el-empty v-if="!list.length" description="还没有公告" :image-size="100" />

    <el-table v-else :data="list" stripe class="ann-table">
      <el-table-column label="封面" width="90">
        <template #default="{ row }">
          <img v-if="row.cover_image_url" :src="row.cover_image_url" class="thumb" />
          <div v-else class="thumb thumb-empty">
            <el-icon><Picture /></el-icon>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="标题" min-width="240">
        <template #default="{ row }">
          <div class="t-cell">
            <el-icon v-if="row.pinned" class="pin"><StarFilled /></el-icon>
            <span class="t-title">{{ row.title }}</span>
          </div>
          <div class="t-excerpt">{{ (row.body_md || "").slice(0, 60) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="row.visibility === 'published' ? 'success' : 'info'" size="small">
            {{ row.visibility === "published" ? "已发布" : "草稿" }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="170">
        <template #default="{ row }">{{ fmtDate(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" align="right">
        <template #default="{ row }">
          <el-button link :icon="Edit" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" :icon="Delete" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
<!-- DIALOG_PLACEHOLDER -->

    <!-- 编辑器 -->
    <el-dialog
      v-model="editorVisible"
      :title="form.id ? '编辑公告' : '新建公告'"
      width="820"
      align-center
      destroy-on-close
    >
      <div class="editor">
        <el-form label-position="top" size="default">
          <el-form-item label="标题" required>
            <el-input v-model="form.title" placeholder="公告标题" maxlength="200" show-word-limit />
          </el-form-item>

          <el-form-item label="封面图（可选）">
            <div class="cover-row">
              <img v-if="form.cover_image_url" :src="form.cover_image_url" class="cover-preview" />
              <div v-else class="cover-empty">
                <el-icon><Picture /></el-icon>
                <span>无封面</span>
              </div>
              <div class="cover-actions">
                <label class="upload-btn">
                  <el-icon><Plus /></el-icon>
                  <span>{{ uploading ? "上传中..." : "上传封面" }}</span>
                  <input
                    type="file"
                    accept="image/*"
                    style="display:none"
                    @change="onUpload"
                    :disabled="uploading"
                  />
                </label>
                <el-button
                  v-if="form.cover_image_url"
                  link
                  type="danger"
                  @click="form.cover_image_url = ''"
                >移除</el-button>
              </div>
            </div>
          </el-form-item>

          <el-form-item label="正文（Markdown）">
            <div class="body-toolbar">
              <label class="upload-link">
                <el-icon><Picture /></el-icon> 插入图片
                <input
                  type="file"
                  accept="image/*"
                  style="display:none"
                  @change="insertImageToBody"
                  :disabled="uploading"
                />
              </label>
              <el-button link :icon="View" @click="previewVisible = true">预览</el-button>
            </div>
            <el-input
              v-model="form.body_md"
              type="textarea"
              :rows="14"
              placeholder="支持 markdown 语法，可在工具栏插入图片"
            />
          </el-form-item>

          <div class="form-row">
            <el-form-item label="状态">
              <el-radio-group v-model="form.visibility">
                <el-radio-button value="published">已发布</el-radio-button>
                <el-radio-button value="draft">草稿</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="置顶">
              <el-switch v-model="form.pinned" />
            </el-form-item>
          </div>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 预览 -->
    <el-dialog v-model="previewVisible" title="预览" width="720" align-center>
      <img v-if="form.cover_image_url" :src="form.cover_image_url" class="preview-cover" />
      <h2 style="margin: 8px 0 16px">{{ form.title || "(未命名)" }}</h2>
      <MarkdownViewer :source="form.body_md || ''" />
    </el-dialog>

  </div>
</template>
<!-- STYLE_PLACEHOLDER -->

<style scoped>
.announcements { display: flex; flex-direction: column; gap: 16px; }
.page-head {
  display: flex; align-items: center; justify-content: space-between;
}
.page-title { font-size: 22px; font-weight: 700; margin: 0; color: #0f172a; }
.page-sub { font-size: 13px; color: var(--text-light); margin: 4px 0 0; }

.thumb {
  width: 64px; height: 64px;
  object-fit: cover;
  border-radius: 6px;
  background: #f1f5f9;
}
.thumb-empty {
  display: flex; align-items: center; justify-content: center;
  color: var(--text-light);
  font-size: 22px;
  background: linear-gradient(135deg, #fff7ed, #fed7aa);
}

.t-cell { display: flex; align-items: center; gap: 6px; }
.pin { color: var(--primary-color); font-size: 14px; }
.t-title { font-weight: 600; color: #0f172a; }
.t-excerpt {
  font-size: 12px; color: var(--text-light); margin-top: 2px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.editor { padding: 4px 0; }
.cover-row {
  display: flex; gap: 14px; align-items: flex-start;
}
.cover-preview {
  width: 160px; height: 100px;
  object-fit: cover; border-radius: 6px;
  border: 1px solid var(--border-color);
}
.cover-empty {
  width: 160px; height: 100px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 4px;
  color: var(--text-light);
  border: 1px dashed var(--border-color);
  border-radius: 6px;
  background: #f8fafc;
  font-size: 12px;
}
.cover-actions { display: flex; flex-direction: column; gap: 6px; }
.upload-btn {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 6px 14px;
  background: var(--primary-color); color: #fff;
  border-radius: 6px; cursor: pointer;
  font-size: 13px;
  transition: background 0.18s;
}
.upload-btn:hover { background: var(--primary-hover); }

.body-toolbar {
  display: flex; gap: 12px; align-items: center;
  margin-bottom: 6px;
}
.upload-link {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 13px;
  color: var(--primary-color);
  cursor: pointer;
}
.upload-link:hover { color: var(--primary-hover); }

.form-row { display: flex; gap: 24px; }
.form-row .el-form-item { margin-bottom: 0; }

.preview-cover {
  width: 100%;
  max-height: 280px;
  object-fit: cover;
  border-radius: 8px;
}
</style>


