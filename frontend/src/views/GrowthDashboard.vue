<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import {
  User,
  UserFilled,
  ChatLineRound,
  Notebook,
  Bell,
  TrendCharts,
  Calendar,
} from "@element-plus/icons-vue";
import { adminApi, statusApi } from "@/api";

const router = useRouter();

const metrics = reactive({
  total_users: 0,
  new_users_7d: 0,
  active_users_7d: 0,
  total_qa_30d: 0,
  total_qa_all: 0,
  total_wiki_pages: 0,
  total_announcements: 0,
  qa_daily_14d: [],
  recent_active_users: [],
  loaded: false,
});

const followups = ref([]);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    const [m, f] = await Promise.allSettled([
      adminApi.dashboardMetrics(),
      statusApi.followups(),
    ]);
    if (m.status === "fulfilled") {
      const data = m.value?.data || m.value || {};
      Object.assign(metrics, data, { loaded: true });
    } else {
      ElMessage.error("加载指标失败：" + (m.reason?.response?.data?.detail || m.reason?.message));
    }
    if (f.status === "fulfilled") {
      followups.value = f.value?.data?.items || f.value?.items || f.value || [];
    }
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const stats = computed(() => [
  {
    key: "total_users",
    label: "总用户数",
    value: metrics.total_users,
    icon: UserFilled,
    accent: "#f97316",
    sub: `近 7 天新增 +${metrics.new_users_7d}`,
  },
  {
    key: "active_users_7d",
    label: "近 7 天活跃用户",
    value: metrics.active_users_7d,
    icon: User,
    accent: "#10b981",
    sub: "有登录记录的用户",
  },
  {
    key: "total_qa_30d",
    label: "近 30 天问答数",
    value: metrics.total_qa_30d,
    icon: ChatLineRound,
    accent: "#3b82f6",
    sub: "用户提问消息数",
  },
  {
    key: "total_qa_all",
    label: "累计总提问数",
    value: metrics.total_qa_all,
    icon: ChatLineRound,
    accent: "#06b6d4",
    sub: "全部历史用户提问",
  },
  {
    key: "total_wiki_pages",
    label: "Wiki 页面数",
    value: metrics.total_wiki_pages,
    icon: Notebook,
    accent: "#8b5cf6",
    sub: `公告 ${metrics.total_announcements} 条`,
  },
]);

// QA 14 天 sparkline 计算
const sparkMax = computed(() => {
  return Math.max(1, ...metrics.qa_daily_14d.map((d) => d.count || 0));
});
const sparkPath = computed(() => {
  const items = metrics.qa_daily_14d || [];
  if (!items.length) return "";
  const w = 100, h = 40;
  const stepX = w / Math.max(1, items.length - 1);
  return items
    .map((d, i) => {
      const x = i * stepX;
      const y = h - (d.count / sparkMax.value) * h;
      return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
});
const sparkArea = computed(() => {
  const p = sparkPath.value;
  if (!p) return "";
  const items = metrics.qa_daily_14d || [];
  const w = 100;
  return `${p} L${w},40 L0,40 Z`;
});

function fmtRole(r) {
  return r === "admin" ? "管理员" : "用户";
}
function fmtDateTime(s) {
  if (!s) return "—";
  try { return new Date(s).toLocaleString("zh-CN"); }
  catch (_) { return String(s).slice(0, 16); }
}
</script>
<!-- TPL_PLACEHOLDER -->

<template>
  <div class="dashboard" v-loading="loading">
    <!-- 页面标题 -->
    <div class="page-head">
      <div>
        <h2 class="page-title">控制台</h2>
        <p class="page-sub">用户、问答、知识库的整体指标</p>
      </div>
      <el-button @click="load" :loading="loading" round>刷新</el-button>
    </div>

    <!-- 4 张 stat 卡 -->
    <div class="stats">
      <div
        v-for="s in stats"
        :key="s.key"
        class="stat-card"
        :style="{ '--accent': s.accent }"
      >
        <div class="stat-top">
          <div class="stat-icon-wrap">
            <el-icon class="stat-icon"><component :is="s.icon" /></el-icon>
          </div>
        </div>
        <div class="stat-num">{{ s.value ?? "—" }}</div>
        <div class="stat-label">{{ s.label }}</div>
        <div class="stat-sub">{{ s.sub }}</div>
      </div>
    </div>

    <!-- 图表 + 活跃用户 -->
    <el-row :gutter="20" class="row">
      <el-col :xs="24" :md="14">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <el-icon><TrendCharts /></el-icon>
              <span>近 14 天提问趋势</span>
            </div>
            <span class="panel-meta">峰值 {{ sparkMax }} / 天</span>
          </div>
          <div class="spark-wrap">
            <svg
              v-if="metrics.qa_daily_14d.length"
              viewBox="0 0 100 40"
              preserveAspectRatio="none"
              class="spark-svg"
            >
              <defs>
                <linearGradient id="spark-grad" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stop-color="#f97316" stop-opacity="0.4" />
                  <stop offset="100%" stop-color="#f97316" stop-opacity="0" />
                </linearGradient>
              </defs>
              <path :d="sparkArea" fill="url(#spark-grad)" />
              <path
                :d="sparkPath"
                fill="none"
                stroke="#f97316"
                stroke-width="0.7"
                stroke-linejoin="round"
                stroke-linecap="round"
                vector-effect="non-scaling-stroke"
              />
            </svg>
            <el-empty
              v-else
              description="暂无数据"
              :image-size="60"
            />
            <div class="spark-axis">
              <span v-for="(d, i) in metrics.qa_daily_14d" :key="d.date" class="axis-tick">
                <template v-if="i % 2 === 0">{{ d.date.slice(5) }}</template>
              </span>
            </div>
          </div>
        </section>
      </el-col>

      <el-col :xs="24" :md="10">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <el-icon><User /></el-icon>
              <span>最近活跃用户</span>
            </div>
            <el-button link @click="router.push('/kb/system/users')">用户管理</el-button>
          </div>
          <el-empty v-if="!metrics.recent_active_users.length" description="暂无登录记录" :image-size="60" />
          <ul v-else class="user-list">
            <li v-for="u in metrics.recent_active_users" :key="u.id" class="user-row">
              <div class="avatar">{{ (u.username || "?").slice(0, 1).toUpperCase() }}</div>
              <div class="u-info">
                <div class="u-line1">
                  <span class="u-name">{{ u.username }}</span>
                  <el-tag :type="u.role === 'admin' ? 'warning' : 'info'" size="small" effect="plain">
                    {{ fmtRole(u.role) }}
                  </el-tag>
                </div>
                <div class="u-line2">
                  <el-icon><Calendar /></el-icon>
                  <span>{{ fmtDateTime(u.last_login_at) }}</span>
                </div>
              </div>
            </li>
          </ul>
        </section>
      </el-col>
    </el-row>

    <!-- 公告快捷 + 待跟进 -->
    <el-row :gutter="20" class="row">
      <el-col :xs="24" :md="12">
        <section class="panel quick-card" @click="router.push('/kb/system/announcements')">
          <div class="quick-icon"><el-icon><Bell /></el-icon></div>
          <div class="quick-body">
            <div class="quick-title">公告管理</div>
            <div class="quick-sub">当前 {{ metrics.total_announcements }} 条公告 · 用户主页可见</div>
          </div>
          <el-button type="primary" round size="small">前往</el-button>
        </section>
      </el-col>
      <el-col :xs="24" :md="12">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <el-icon><ChatLineRound /></el-icon>
              <span>待跟进问题</span>
            </div>
          </div>
          <el-empty v-if="!followups.length" description="暂无 followups" :image-size="60" />
          <ul v-else class="follow-list">
            <li v-for="f in followups.slice(0, 5)" :key="f.id || f.text">
              {{ f.text || f.followup_text }}
              <small class="muted">{{ f.source_page || "" }}</small>
            </li>
          </ul>
        </section>
      </el-col>
    </el-row>
  </div>
</template>
<!-- STY_PLACEHOLDER -->

<style scoped>
.dashboard { display: flex; flex-direction: column; gap: 18px; }

.page-head {
  display: flex; align-items: flex-end; justify-content: space-between;
}
.page-title { font-size: 22px; font-weight: 700; margin: 0; color: #0f172a; }
.page-sub { font-size: 13px; color: var(--text-light); margin: 4px 0 0; }

/* ============ Stat cards ============ */
.stats {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px;
}
.stat-card {
  position: relative;
  padding: 22px 22px 18px;
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  overflow: hidden;
  transition: all 0.2s;
}
.stat-card::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: var(--accent, var(--primary-color));
  opacity: 0.85;
}
.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--card-shadow-hover);
}
.stat-top { display: flex; justify-content: flex-end; }
.stat-icon-wrap {
  width: 40px; height: 40px;
  display: flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--accent);
  border-radius: 10px;
}
.stat-icon { font-size: 20px; }
.stat-num {
  font-size: 32px;
  font-weight: 700;
  color: #0f172a;
  margin-top: 6px;
  line-height: 1.05;
  letter-spacing: -0.5px;
}
.stat-label { font-size: 13px; color: var(--text-main); margin-top: 6px; font-weight: 500; }
.stat-sub { font-size: 12px; color: var(--text-light); margin-top: 4px; }

/* ============ Panel ============ */
.row + .row { margin-top: 0; }
.row :deep(.el-col) { display: flex; }
.row :deep(.el-col > *) { width: 100%; }

.panel {
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  padding: 20px;
}
.panel-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 14px;
}
.panel-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 15px; font-weight: 600; color: #0f172a;
}
.panel-title .el-icon { color: var(--primary-color); font-size: 18px; }
.panel-meta { font-size: 12px; color: var(--text-light); }

/* ============ Sparkline ============ */
.spark-wrap { position: relative; }
.spark-svg {
  width: 100%; height: 180px;
  display: block;
}
.spark-axis {
  display: grid;
  grid-template-columns: repeat(14, 1fr);
  font-size: 10px; color: var(--text-light);
  margin-top: 4px;
}
.axis-tick { text-align: center; }

/* ============ User list ============ */
.user-list { list-style: none; padding: 0; margin: 0; }
.user-row {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 0;
  border-bottom: 1px dashed var(--border-color);
}
.user-row:last-child { border-bottom: none; }
.avatar {
  width: 36px; height: 36px;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #fff7ed, #fed7aa);
  color: var(--primary-hover);
  font-weight: 700;
  border-radius: 50%;
  flex-shrink: 0;
}
.u-info { flex: 1; min-width: 0; }
.u-line1 { display: flex; align-items: center; gap: 8px; }
.u-name { font-weight: 600; color: #0f172a; font-size: 13.5px; }
.u-line2 {
  display: flex; align-items: center; gap: 4px;
  font-size: 11.5px; color: var(--text-light); margin-top: 2px;
}

/* ============ Quick / followups ============ */
.quick-card {
  display: flex; align-items: center; gap: 14px;
  cursor: pointer;
  transition: all 0.2s;
}
.quick-card:hover {
  border-color: #fdba74;
  box-shadow: var(--card-shadow-hover);
}
.quick-icon {
  width: 48px; height: 48px;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #fff7ed, #fed7aa);
  color: var(--primary-color);
  border-radius: 12px;
  font-size: 24px;
}
.quick-body { flex: 1; }
.quick-title { font-size: 15px; font-weight: 600; color: #0f172a; }
.quick-sub { font-size: 12.5px; color: var(--text-light); margin-top: 2px; }

.follow-list { list-style: none; padding: 0; margin: 0; }
.follow-list li {
  padding: 8px 0;
  border-bottom: 1px dashed var(--border-color);
  font-size: 13px;
}
.follow-list .muted { color: var(--text-light); margin-left: 6px; }

@media (max-width: 768px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
}
</style>


