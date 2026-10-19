<script setup>
import { computed, nextTick, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Delete, Refresh, MagicStick } from "@element-plus/icons-vue";
import { adminApi, voidLinksApi } from "@/api";

const activeTab = ref("aliases");
const loading = ref(false);

const aliases = ref([]); // [{canonical, members:[], sources:[]}]
const relations = ref([]); // [{name, description, sources:[]}]

async function load() {
  loading.value = true;
  try {
    const data = await adminApi.getSchema();
    const body = data?.data || data || {};
    aliases.value = (body.aliases || []).map((a) => ({
      canonical: a.canonical || "",
      members: a.members || [],
      sources: a.sources || [],
    }));
    relations.value = (body.relation_types || body.relations || []).map((r) => ({
      name: r.name || "",
      description: r.description || "",
      sources: r.sources || [],
    }));
  } catch (e) {
    ElMessage.error("加载 schema 失败：" + (e.response?.data?.detail || e.message));
  } finally {
    loading.value = false;
  }
}

async function deleteAlias(key) {
  try {
    await ElMessageBox.confirm(
      `确认删除整个别名组 "${key}"？后续 ingest 可能再次自动写入同名条目。`,
      "确认",
      { type: "warning" }
    );
  } catch {
    return;
  }
  try {
    await adminApi.deleteAlias(key);
    ElMessage.success(`已删除整组 "${key}"`);
    await load();
  } catch (e) {
    ElMessage.error("删除失败：" + (e.response?.data?.detail || e.message));
  }
}

async function deleteAliasMember(canonical, member) {
  if (member === canonical) {
    // 删除规范名等价于整组删,走整组确认
    return deleteAlias(canonical);
  }
  try {
    await adminApi.deleteAliasMember(canonical, member);
    ElMessage.success(`已从 "${canonical}" 中移除 "${member}"`);
    await load();
  } catch (e) {
    ElMessage.error("移除失败：" + (e.response?.data?.detail || e.message));
  }
}

async function deleteRelation(key) {
  try {
    await ElMessageBox.confirm(
      `确认删除关系类型 "${key}"？`,
      "确认",
      { type: "warning" }
    );
  } catch {
    return;
  }
  try {
    await adminApi.deleteRelation(key);
    ElMessage.success(`已删除 "${key}"`);
    await load();
  } catch (e) {
    ElMessage.error("删除失败：" + (e.response?.data?.detail || e.message));
  }
}

onMounted(load);

// ===== 虚空连接清理 =====
const voidVisible = ref(false);
const voidScanning = ref(false);
const voidRunning = ref(false);
const voidScan = reactive({
  aliasable: [],
  fillable: [],
  total_broken_targets: 0,
  total_broken_refs: 0,
});
const aliasSelected = ref([]);   // 勾选的 aliasable 行
const fillSelected = ref([]);    // 勾选的 fillable 行
const aliasTableRef = ref(null);
const fillTableRef = ref(null);

async function openVoidLinks() {
  voidVisible.value = true;
  if (voidScan.aliasable.length === 0 && voidScan.fillable.length === 0) {
    await runScan();
  }
}

async function runScan() {
  voidScanning.value = true;
  try {
    const data = await voidLinksApi.scan();
    Object.assign(voidScan, data);
    aliasSelected.value = [];
    fillSelected.value = [];
    // 默认全选 aliasable(用户大多数情况下都会想把它们都加进去)
    await nextTick();
    aliasTableRef.value?.toggleAllSelection();
  } catch (e) {
    ElMessage.error("扫描失败:" + (e.response?.data?.detail || e.message));
  } finally {
    voidScanning.value = false;
  }
}

async function runFix() {
  if (aliasSelected.value.length === 0 && fillSelected.value.length === 0) {
    ElMessage.warning("请至少勾选一项");
    return;
  }
  // 二次确认 LLM 生成数量(每个约 20-60 秒)
  if (fillSelected.value.length > 5) {
    try {
      await ElMessageBox.confirm(
        `你选了 ${fillSelected.value.length} 个 slug 让 LLM 生成,大约耗时 ${fillSelected.value.length * 30} 秒。确定继续?`,
        "确认", { type: "warning" }
      );
    } catch (_) { return; }
  }
  voidRunning.value = true;
  try {
    // 高分(≥0.95)直接采纳;中等分进 pending 等审核
    const auto_apply = [];
    const accept = [];
    for (const r of aliasSelected.value) {
      const item = { canonical: r.suggested_canonical, alias: r.broken_target };
      if ((r.score || 0) >= 0.95) {
        auto_apply.push(item);
      } else {
        accept.push(item);
      }
    }
    const payload = {
      auto_apply_aliases: auto_apply,
      accept_aliases: accept,
      generate_slugs: fillSelected.value.map((r) => r.broken_target),
    };
    const result = await voidLinksApi.run(payload);
    ElMessage.success(
      `完成:自动采纳 ${result.auto_applied || 0}、待审核 ${result.pending_added || 0}、生成页 ${result.pages_generated.length}、失败 ${result.pages_failed.length}`
    );
    if (result.pages_failed.length > 0) {
      console.warn("[void_links] failed:", result.pages_failed);
    }
    // 刷新一遍 schema(pending 别名要看到)
    await load();
    // 重新扫描
    await runScan();
  } catch (e) {
    ElMessage.error("执行失败:" + (e.response?.data?.detail || e.message));
  } finally {
    voidRunning.value = false;
  }
}

// 把"未勾选的 aliasable"项 转移到 fillable 列表(让 LLM 生成它们)
function moveUnselectedAliasToFill() {
  const selectedTargets = new Set(aliasSelected.value.map((r) => r.broken_target));
  const unselected = voidScan.aliasable.filter((r) => !selectedTargets.has(r.broken_target));
  if (unselected.length === 0) {
    ElMessage.info("没有未勾选的别名候选");
    return;
  }
  // 转移:从 aliasable 删,加到 fillable
  voidScan.aliasable = voidScan.aliasable.filter((r) => selectedTargets.has(r.broken_target));
  for (const r of unselected) {
    voidScan.fillable.unshift({
      broken_target: r.broken_target,
      ref_count: r.ref_count,
      src_slugs: r.src_slugs,
    });
  }
  ElMessage.success(`已移动 ${unselected.length} 个候选到「待 LLM 生成」`);
}
</script>

<template>
  <div v-loading="loading">
    <div class="page-header">
      <div>
        <h2 class="page-title">.wiki-schema 总览</h2>
        <p class="page-subtitle">
          别名词表与关系类型由 ingest 流水线自动抽取并直接写入。
          点同义词上的 × 可单独删除该词,「删整组」按钮删除整个别名组。
          高级参数(合并阈值等)请到「模型配置 → 高级设置」。
        </p>
      </div>
      <div style="display:flex; gap:8px;">
        <el-button type="primary" @click="openVoidLinks">清理虚空连接</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane :label="`别名词表（${aliases.length}）`" name="aliases">
        <el-card shadow="never">
          <el-empty v-if="!aliases.length" :image-size="60" description="尚无别名,上传素材后自动填充" />
          <el-table v-else :data="aliases" stripe>
            <el-table-column label="规范名" width="180">
              <template #default="{ row }">
                <el-tag size="small" type="primary" effect="dark">
                  {{ row.canonical }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="同义词(点 × 单独删除)">
              <template #default="{ row }">
                <el-tag
                  v-for="m in row.members.filter((x) => x !== row.canonical)"
                  :key="m"
                  size="small"
                  effect="plain"
                  closable
                  style="margin-right: 4px; margin-bottom: 2px"
                  @close="deleteAliasMember(row.canonical, m)"
                >{{ m }}</el-tag>
                <span
                  v-if="row.members.filter((x) => x !== row.canonical).length === 0"
                  class="muted"
                >(无同义词)</span>
              </template>
            </el-table-column>
            <el-table-column label="来源页" width="240">
              <template #default="{ row }">
                <a
                  v-for="s in row.sources"
                  :key="s"
                  :href="`/kb/wiki/${encodeURIComponent(s)}`"
                  class="src-link"
                >{{ s }}</a>
                <span v-if="!row.sources?.length" class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button
                  type="danger"
                  size="small"
                  link
                  :icon="Delete"
                  @click="deleteAlias(row.canonical)"
                >删整组</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane :label="`关系类型（${relations.length}）`" name="relations">
        <el-card shadow="never">
          <el-empty v-if="!relations.length" :image-size="60" description="尚无关系类型" />
          <el-table v-else :data="relations" stripe>
            <el-table-column label="关系类型" width="200" prop="name" />
            <el-table-column label="说明" prop="description" />
            <el-table-column label="来源页" width="240">
              <template #default="{ row }">
                <a
                  v-for="s in row.sources"
                  :key="s"
                  :href="`/kb/wiki/${encodeURIComponent(s)}`"
                  class="src-link"
                >{{ s }}</a>
                <span v-if="!row.sources?.length" class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button
                  type="danger"
                  size="small"
                  link
                  :icon="Delete"
                  @click="deleteRelation(row.name)"
                >删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 虚空连接清理 dialog -->
    <el-dialog
      v-model="voidVisible"
      title="清理虚空连接"
      width="900"
      align-center
      class="void-dialog"
    >
      <div v-loading="voidScanning" class="void-body">
        <div v-if="!voidScanning" class="void-stat">
          总计 <b>{{ voidScan.total_broken_targets }}</b> 个虚空目标 · 共 <b>{{ voidScan.total_broken_refs }}</b> 处引用 ·
          可别名 {{ voidScan.aliasable.length }} · 需 LLM 生成 {{ voidScan.fillable.length }}
          <el-button link @click="runScan">重新扫描</el-button>
        </div>

        <el-tabs class="void-tabs">
          <el-tab-pane :label="`别名召回(${voidScan.aliasable.length})`">
            <div class="void-tip-row">
              <p class="void-tip" style="margin: 0; flex: 1;">
                这些 broken target 在已有 wiki 里找到了相似的 canonical 页。
                <strong style="color:#10b981">置信 ≥ 0.95 直接采纳</strong>,
                0.7-0.95 进 pending 等审核。
                未勾选的可一键转去 LLM 生成。
              </p>
              <el-button size="small" @click="moveUnselectedAliasToFill">
                未勾选 → 让 LLM 生成
              </el-button>
            </div>
            <el-table
              ref="aliasTableRef"
              :data="voidScan.aliasable"
              stripe
              @selection-change="aliasSelected = $event"
              max-height="380"
            >
              <el-table-column type="selection" width="44" />
              <el-table-column label="虚空目标" prop="broken_target" min-width="180">
                <template #default="{ row }">
                  <code>[[{{ row.broken_target }}]]</code>
                </template>
              </el-table-column>
              <el-table-column label="建议指向" prop="suggested_canonical" min-width="180">
                <template #default="{ row }">
                  <el-tag size="small" type="success">{{ row.suggested_canonical }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="置信" prop="score" width="80" align="center">
                <template #default="{ row }">
                  <span :style="{ color: row.score >= 0.9 ? '#10b981' : (row.score >= 0.8 ? '#f59e0b' : '#94a3b8') }">
                    {{ row.score }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="处置" width="100" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.score >= 0.95" size="small" type="success" effect="dark">自动采纳</el-tag>
                  <el-tag v-else size="small" type="warning" effect="plain">待审核</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="引用数" prop="ref_count" width="80" align="center" />
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="`待 LLM 生成(${voidScan.fillable.length})`">
            <p class="void-tip">
              这些目标找不到近似页,只能让 LLM 凭已有上下文生成 entity 页(标 <code>auto_filled=true confidence=low</code>)。
              <strong>每条耗时 20-60 秒</strong>,建议每次最多 5-10 条。
            </p>
            <el-table
              ref="fillTableRef"
              :data="voidScan.fillable"
              stripe
              @selection-change="fillSelected = $event"
              max-height="380"
            >
              <el-table-column type="selection" width="44" />
              <el-table-column label="虚空目标" prop="broken_target" min-width="200">
                <template #default="{ row }">
                  <code>[[{{ row.broken_target }}]]</code>
                </template>
              </el-table-column>
              <el-table-column label="引用数" prop="ref_count" width="100" align="center" />
              <el-table-column label="引用来源(部分)" min-width="280">
                <template #default="{ row }">
                  <small style="color:#64748b">{{ row.src_slugs?.slice(0, 4).join(', ') }}{{ row.src_slugs?.length > 4 ? ' …' : '' }}</small>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </div>
      <template #footer>
        <span style="margin-right:auto; color:#64748b; font-size:12px">
          已选:别名 {{ aliasSelected.length }} · 生成 {{ fillSelected.length }}
        </span>
        <el-button @click="voidVisible = false">关闭</el-button>
        <el-button type="primary" :loading="voidRunning" @click="runFix">执行</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 12px;
}
.muted { color: var(--text-light); font-size: 12px; }
.src-link {
  display: inline-block;
  font-size: 12px;
  color: var(--primary-color, #1e3a5f);
  margin-right: 4px;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.src-link:hover { color: var(--accent-color, #a87b3d); }

.void-body { display: flex; flex-direction: column; gap: 12px; }
.void-stat {
  font-size: 13px;
  color: #475569;
  background: #f8fafc;
  padding: 10px 14px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.void-stat b { color: #ea580c; font-weight: 600; }
.void-tip {
  font-size: 12.5px;
  color: #64748b;
  margin: 0 0 8px;
  line-height: 1.6;
}
.void-tip-row {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
}
.void-tip code {
  background: #fff7ed;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
}
</style>
