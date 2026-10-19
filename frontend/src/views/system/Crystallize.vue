<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import MarkdownViewer from "@/components/MarkdownViewer.vue";
import { adminApi, crystallizeApi } from "@/api";

const router = useRouter();

const activeTab = ref("merge");

const candidates = ref([]); // gap-queue origin=merge_suggestion
const candidatesLoading = ref(false);

const userText = ref("");
const draft = ref(null);
const generating = ref(false);

async function loadCandidates() {
  candidatesLoading.value = true;
  try {
    const data = await adminApi.gapQueue("open");
    const items = data?.data?.items || data?.items || data || [];
    candidates.value = items.filter(
      (it) => it.origin === "merge_suggestion" || it.kind === "merge_suggestion"
    );
  } catch (e) {
    /* 后端未就绪时静默 */
  } finally {
    candidatesLoading.value = false;
  }
}

async function approveCandidate(item) {
  try {
    await adminApi.setGapStatus(item.id, "approved");
    ElMessage.success("已合并");
    await loadCandidates();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  }
}
async function rejectCandidate(item) {
  try {
    await adminApi.setGapStatus(item.id, "rejected");
    ElMessage.success("已忽略");
    await loadCandidates();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  }
}

async function generateDraft() {
  if (!userText.value.trim()) {
    ElMessage.warning("请输入要结晶化的文本");
    return;
  }
  generating.value = true;
  try {
    const data = await crystallizeApi.create({ text: userText.value });
    draft.value = data?.data || data;
    ElMessage.success("已生成草稿");
  } catch (e) {
    ElMessage.error("生成失败：" + (e.response?.data?.detail || e.message));
  } finally {
    generating.value = false;
  }
}

onMounted(loadCandidates);
</script>

<template>
  <div>
    <div class="page-header">
      <div>
        <h2 class="page-title">结晶化 · 合并审核</h2>
        <p class="page-subtitle">候选实体合并 + 用户文本沉淀为 synthesis</p>
      </div>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane :label="`合并候选 (${candidates.length})`" name="merge">
        <el-card shadow="never" v-loading="candidatesLoading">
          <el-empty v-if="!candidates.length" description="暂无合并候选" />
          <el-table v-else :data="candidates" stripe>
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="user_question" label="原始问题 / 来源" min-width="200" show-overflow-tooltip />
            <el-table-column prop="suggested_seeds" label="候选合并对" min-width="220">
              <template #default="{ row }">
                <span v-if="Array.isArray(row.suggested_seeds)">
                  <el-tag
                    v-for="s in row.suggested_seeds"
                    :key="s"
                    size="small"
                    style="margin-right:4px"
                  >{{ s }}</el-tag>
                </span>
                <span v-else>{{ row.suggested_seeds }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="180">
              <template #default="{ row }">
                <el-button size="small" type="success" @click="approveCandidate(row)">合并</el-button>
                <el-button size="small" type="danger" @click="rejectCandidate(row)">忽略</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="文本结晶化" name="text">
        <el-row :gutter="14">
          <el-col :xs="24" :md="12">
            <el-card shadow="never">
              <template #header><strong>输入文本</strong></template>
              <el-input
                v-model="userText"
                type="textarea"
                :rows="14"
                placeholder="把零碎想法 / 心得 / 笔记粘进来，crystallize 会沉淀为 synthesis 页"
              />
              <el-button
                type="primary"
                :loading="generating"
                @click="generateDraft"
                style="margin-top:10px"
              >生成 synthesis 草稿</el-button>
            </el-card>
          </el-col>
          <el-col :xs="24" :md="12">
            <el-card shadow="never">
              <template #header><strong>草稿预览</strong></template>
              <el-empty v-if="!draft" description="尚未生成" />
              <template v-else>
                <MarkdownViewer :source="draft.markdown || draft.content || ''" />
                <el-button
                  v-if="draft.slug"
                  type="primary"
                  size="small"
                  @click="router.push(`/kb/wiki/${encodeURIComponent(draft.slug)}`)"
                  style="margin-top:8px"
                >查看 wiki 页面</el-button>
              </template>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 12px; }
</style>
