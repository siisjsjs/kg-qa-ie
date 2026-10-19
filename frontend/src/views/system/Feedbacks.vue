<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Delete, Edit, ChatDotSquare } from "@element-plus/icons-vue";
import { feedbackApi } from "@/api";

const list = ref([]);
const loading = ref(false);
const filterStatus = ref("");
const editorVisible = ref(false);

const empty = () => ({
  id: null,
  status: "open",
  admin_note: "",
});
const form = reactive(empty());

const CATEGORY_LABEL = {
  bug: "缺陷",
  feature: "新功能",
  content: "内容相关",
  other: "其他",
};
const STATUS_LABEL = {
  open: "待处理",
  in_progress: "处理中",
  resolved: "已解决",
  dismissed: "已忽略",
};
const STATUS_TYPE = {
  open: "warning",
  in_progress: "primary",
  resolved: "success",
  dismissed: "info",
};

async function load() {
  loading.value = true;
  try {
    const data = await feedbackApi.list(filterStatus.value || undefined);
    list.value = data?.items || data?.data?.items || [];
  } catch (e) {
    ElMessage.error("加载反馈失败：" + (e.response?.data?.detail || e.message));
  } finally {
    loading.value = false;
  }
}
onMounted(load);

function openEdit(row) {
  Object.assign(form, {
    id: row.id,
    status: row.status,
    admin_note: row.admin_note || "",
  });
  editorVisible.value = true;
}

async function save() {
  try {
    await feedbackApi.update(form.id, {
      status: form.status,
      admin_note: form.admin_note || null,
    });
    ElMessage.success("已更新");
    editorVisible.value = false;
    await load();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确定删除该反馈？`, "确认", { type: "warning" });
  } catch (_) { return; }
  try {
    await feedbackApi.delete(row.id);
    ElMessage.success("已删除");
    await load();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  }
}

function fmtDate(s) {
  if (!s) return "—";
  try { return new Date(s).toLocaleString("zh-CN"); }
  catch (_) { return String(s).slice(0, 16); }
}

const counts = computed(() => {
  const c = { all: list.value.length, open: 0, in_progress: 0, resolved: 0, dismissed: 0 };
  for (const f of list.value) c[f.status] = (c[f.status] || 0) + 1;
  return c;
});
</script>
<!-- TPL -->

<template>
  <div class="feedbacks" v-loading="loading">
    <div class="page-head">
      <div>
        <h2 class="page-title">用户反馈</h2>
        <p class="page-sub">查看与处理用户提交的反馈</p>
      </div>
      <el-button @click="load" round>刷新</el-button>
    </div>

    <div class="filter-bar">
      <el-radio-group v-model="filterStatus" @change="load">
        <el-radio-button :value="''">全部 ({{ counts.all }})</el-radio-button>
        <el-radio-button value="open">待处理 ({{ counts.open || 0 }})</el-radio-button>
        <el-radio-button value="in_progress">处理中 ({{ counts.in_progress || 0 }})</el-radio-button>
        <el-radio-button value="resolved">已解决 ({{ counts.resolved || 0 }})</el-radio-button>
        <el-radio-button value="dismissed">已忽略 ({{ counts.dismissed || 0 }})</el-radio-button>
      </el-radio-group>
    </div>

    <el-empty v-if="!list.length" description="暂无反馈" :image-size="100" />

    <ul v-else class="fb-list">
      <li v-for="row in list" :key="row.id" class="fb-card">
        <div class="fb-head">
          <div class="fb-tags">
            <el-tag :type="STATUS_TYPE[row.status]" size="small" effect="dark" round>
              {{ STATUS_LABEL[row.status] }}
            </el-tag>
            <el-tag size="small" effect="plain">{{ CATEGORY_LABEL[row.category] }}</el-tag>
          </div>
          <div class="fb-meta">
            <span>{{ row.username || "匿名" }}</span>
            <span class="dot">·</span>
            <span>{{ fmtDate(row.created_at) }}</span>
          </div>
        </div>
        <div class="fb-content">{{ row.content }}</div>
        <div v-if="row.contact" class="fb-contact">
          联系方式：<span>{{ row.contact }}</span>
        </div>
        <div v-if="row.admin_note" class="fb-note">
          <strong>处理记录：</strong>{{ row.admin_note }}
        </div>
        <div class="fb-actions">
          <el-button link :icon="Edit" @click="openEdit(row)">处理</el-button>
          <el-button link type="danger" :icon="Delete" @click="remove(row)">删除</el-button>
        </div>
      </li>
    </ul>

    <el-dialog v-model="editorVisible" title="处理反馈" width="520">
      <el-form label-position="top">
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio-button value="open">待处理</el-radio-button>
            <el-radio-button value="in_progress">处理中</el-radio-button>
            <el-radio-button value="resolved">已解决</el-radio-button>
            <el-radio-button value="dismissed">已忽略</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="处理记录（可选）">
          <el-input
            v-model="form.admin_note"
            type="textarea"
            :rows="4"
            placeholder="记录如何处理这条反馈"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
<!-- STY -->

<style scoped>
.feedbacks { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; margin: 0; color: #0f172a; }
.page-sub { font-size: 13px; color: var(--text-light); margin: 4px 0 0; }
.filter-bar { display: flex; }

.fb-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 14px; }
.fb-card {
  padding: 18px 20px;
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  transition: all 0.2s;
}
.fb-card:hover {
  border-color: #fdba74;
  box-shadow: var(--card-shadow-hover);
}
.fb-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 10px;
}
.fb-tags { display: flex; gap: 6px; }
.fb-meta {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--text-light);
}
.fb-meta .dot { opacity: 0.5; }
.fb-content {
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-main);
  white-space: pre-wrap;
  word-break: break-word;
}
.fb-contact {
  margin-top: 8px;
  font-size: 12.5px;
  color: var(--text-light);
}
.fb-contact span { color: var(--primary-color); }
.fb-note {
  margin-top: 10px;
  padding: 8px 12px;
  background: #fff7ed;
  border-left: 3px solid var(--primary-color);
  border-radius: 4px;
  font-size: 13px;
  color: var(--text-main);
}
.fb-actions {
  margin-top: 10px;
  display: flex; justify-content: flex-end; gap: 4px;
}
</style>


