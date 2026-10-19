<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { adminApi } from "@/api";

const activeTab = ref("list");
const userList = ref([]);
const pendingList = ref([]);
const loading = ref(false);

async function loadUsers() {
  try {
    const data = await adminApi.listUsers();
    userList.value = data?.data?.items || data?.items || data || [];
  } catch (e) {
    ElMessage.error("加载用户失败：" + (e.response?.data?.detail || e.message));
  }
}

async function loadPending() {
  try {
    const data = await adminApi.pendingUsers();
    pendingList.value = data?.data?.items || data?.items || data || [];
  } catch (e) {
    /* 后端未实现时安静失败 */
  }
}

async function approve(u) {
  try {
    await adminApi.approveUser(u.id);
    ElMessage.success("已通过");
    await Promise.all([loadUsers(), loadPending()]);
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  }
}

async function reject(u) {
  try {
    await adminApi.rejectUser(u.id);
    ElMessage.success("已拒绝");
    await loadPending();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  }
}

async function setStatus(u, status) {
  try {
    await adminApi.setUserStatus(u.id, status);
    ElMessage.success("已更新");
    await loadUsers();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  }
}

async function remove(u) {
  try {
    await adminApi.deleteUser(u.id);
    ElMessage.success("已删除");
    await loadUsers();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  }
}

onMounted(async () => {
  loading.value = true;
  await Promise.all([loadUsers(), loadPending()]);
  loading.value = false;
});
</script>

<template>
  <div v-loading="loading">
    <div class="page-header">
      <div>
        <h2 class="page-title">用户管理</h2>
        <p class="page-subtitle">用户列表 + 注册审核（admin）</p>
      </div>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane name="list" label="用户列表">
        <el-card shadow="never">
          <el-table :data="userList" stripe>
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="username" label="用户名" />
            <el-table-column prop="email" label="邮箱" />
            <el-table-column prop="role" label="角色" width="100">
              <template #default="{ row }">
                <el-tag :type="row.role === 'admin' ? 'warning' : 'info'" size="small">
                  {{ row.role }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="120">
              <template #default="{ row }">
                <el-tag
                  size="small"
                  :type="row.status === 'active' ? 'success' : row.status === 'pending' ? 'warning' : 'danger'"
                >{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="qa_count" label="累计提问数" width="130" sortable align="center">
              <template #default="{ row }">
                <span class="qa-count">{{ row.qa_count ?? 0 }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="注册时间" width="180" />
            <el-table-column label="操作" width="280">
              <template #default="{ row }">
                <el-button
                  v-if="row.status !== 'active'"
                  size="small"
                  type="success"
                  link
                  @click="setStatus(row, 'active')"
                >启用</el-button>
                <el-button
                  v-else
                  size="small"
                  type="warning"
                  link
                  @click="setStatus(row, 'disabled')"
                >禁用</el-button>
                <el-popconfirm title="确认删除该用户？" @confirm="remove(row)">
                  <template #reference>
                    <el-button size="small" type="danger" link>删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane :label="`注册审核 (${pendingList.length})`" name="pending">
        <el-card shadow="never">
          <el-empty v-if="!pendingList.length" description="暂无待审核账号" />
          <el-table v-else :data="pendingList" stripe>
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="username" label="用户名" />
            <el-table-column prop="email" label="邮箱" />
            <el-table-column prop="created_at" label="申请时间" width="180" />
            <el-table-column label="操作" width="200">
              <template #default="{ row }">
                <el-button size="small" type="success" @click="approve(row)">通过</el-button>
                <el-button size="small" type="danger" @click="reject(row)">拒绝</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 12px; }
.qa-count { font-weight: 600; color: #0f172a; }
</style>
