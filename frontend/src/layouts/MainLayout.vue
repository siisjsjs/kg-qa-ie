<script setup>
import { computed, onMounted, onBeforeUnmount, watch } from "vue";
import { useRouter, useRoute } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";
import Logo from "@/components/Logo.vue";
import {
  HomeFilled,
  Odometer,
  ChatLineRound,
  ChatDotSquare,
  Share,
  Notebook,
  FolderOpened,
  DataAnalysis,
  Setting,
  Cpu,
  Connection,
  User,
  Bell,
  Download,
  Fold,
  Expand,
  SwitchButton,
  ArrowDown,
  Grid,
} from "@element-plus/icons-vue";

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();
const ui = useUiStore();

const MENU_BASE = [
  { path: "/kb", label: "主页", icon: HomeFilled },
  { path: "/kb/query", label: "智能问答", icon: ChatLineRound },
  { path: "/kb/wiki", label: "Wiki 浏览", icon: Notebook },
];

const MENU_ADMIN_EXTRA = [
  { path: "/kb/sources", label: "素材管理", icon: FolderOpened, requiresAdmin: true },
  { path: "/kb/qa-data", label: "问答数据", icon: DataAnalysis, requiresAdmin: true },
  { path: "/kb/dashboard", label: "控制台", icon: Odometer, requiresAdmin: true },
  { path: "/kb/system/scraper", label: "网页抓取", icon: Download, requiresAdmin: true },
  {
    path: "/kb/system",
    label: "系统设置",
    icon: Setting,
    requiresAdmin: true,
    children: [
      { path: "/kb/system/model", label: "模型配置", icon: Cpu },
      { path: "/kb/system/schema", label: "Schema 编辑", icon: Connection },
      { path: "/kb/system/users", label: "用户管理", icon: User },
      { path: "/kb/system/announcements", label: "公告管理", icon: Bell },
      { path: "/kb/system/feedbacks", label: "用户反馈", icon: ChatDotSquare },
    ],
  },
];

const visibleMenu = computed(() =>
  auth.isAdmin ? [...MENU_BASE, ...MENU_ADMIN_EXTRA] : MENU_BASE
);

const activeMenu = computed(() => route.path);

function handleResize() {
  ui.setMobile(window.innerWidth < 768);
}
onMounted(() => {
  handleResize();
  window.addEventListener("resize", handleResize);
});
onBeforeUnmount(() => window.removeEventListener("resize", handleResize));

// 移动端:路由切换后自动关闭抽屉
watch(() => route.path, () => {
  if (ui.isMobile) ui.closeMobileDrawer();
});

async function handleLogout() {
  await auth.logout();
  router.push("/login");
}

function go(path) {
  if (route.path !== path) router.push(path);
}

// hamburger 图标:移动端 = 总是展示菜单图标(Fold);桌面端按收起状态切换
const hamburgerIcon = computed(() => {
  if (ui.isMobile) return Fold;
  return ui.sidebarCollapsed ? Expand : Fold;
});

</script>

<template>
  <div class="app-shell">
    <aside
      class="app-side"
      :class="{
        collapsed: !ui.isMobile && ui.sidebarCollapsed,
        mobile: ui.isMobile,
        'mobile-open': ui.isMobile && ui.mobileDrawerOpen,
      }"
    >
      <div class="side-header">
        <Logo :collapsed="!ui.isMobile && ui.sidebarCollapsed" />
      </div>

      <el-scrollbar class="side-scroll">
        <el-menu
          :default-active="activeMenu"
          :collapse="!ui.isMobile && ui.sidebarCollapsed"
          :unique-opened="true"
          background-color="transparent"
          text-color="#334155"
          active-text-color="#f97316"
          @select="go"
        >
          <template v-for="item in visibleMenu" :key="item.path">
            <el-sub-menu v-if="item.children" :index="item.path">
              <template #title>
                <el-icon><component :is="item.icon" /></el-icon>
                <span>{{ item.label }}</span>
              </template>
              <el-menu-item
                v-for="sub in item.children"
                :key="sub.path"
                :index="sub.path"
              >
                <el-icon><component :is="sub.icon" /></el-icon>
                <template #title>{{ sub.label }}</template>
              </el-menu-item>
            </el-sub-menu>

            <el-menu-item v-else :index="item.path">
              <el-icon><component :is="item.icon" /></el-icon>
              <template #title>{{ item.label }}</template>
            </el-menu-item>
          </template>
        </el-menu>
      </el-scrollbar>
    </aside>

    <!-- 移动端遮罩(sidebar 弹出时) -->
    <div
      v-if="ui.isMobile && ui.mobileDrawerOpen"
      class="mobile-mask"
      @click="ui.closeMobileDrawer()"
    ></div>

    <div class="app-body">
      <header class="app-header">
        <el-button
          link
          class="collapse-btn"
          @click="ui.toggleSidebar()"
          :icon="hamburgerIcon"
        />
        <div class="header-title">
          {{ route.meta?.title || "" }}
        </div>
        <div class="header-right">
          <el-button link class="portal-back" @click="router.push('/')">
            <el-icon><Grid /></el-icon>门户
          </el-button>
          <el-dropdown trigger="click">
            <span class="user-chip">
              <el-icon><User /></el-icon>
              <span class="user-name">{{ auth.userInfo?.username || auth.userInfo?.email || "未登录" }}</span>
              <el-tag
                v-if="auth.isAdmin"
                size="small"
                type="warning"
                effect="plain"
                style="margin-left: 6px"
              >admin</el-tag>
              <el-icon class="caret"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  {{ auth.userInfo?.email || "" }}
                </el-dropdown-item>
                <el-dropdown-item divided @click="handleLogout">
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="app-main">
        <RouterView v-slot="{ Component }">
          <component :is="Component" />
        </RouterView>
      </main>
    </div>

  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
}
.app-side {
  width: var(--sidebar-width);
  background: var(--sidebar-bg);
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  transition: width 0.25s;
  flex-shrink: 0;
}
.app-side.collapsed { width: var(--sidebar-width-collapsed); }
.side-header {
  height: var(--header-height);
  display: flex;
  align-items: center;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-color);
}
.side-scroll { flex: 1; }
:deep(.el-menu) { border-right: none; }
:deep(.el-menu-item.is-active) {
  background: #fff7ed !important;
}
.app-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-color);
}
.app-header {
  height: var(--header-height);
  background: #fff;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  padding: 0 16px;
  gap: 12px;
}
.collapse-btn { font-size: 18px; color: var(--text-light); }
.header-title {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
}
.header-right { margin-left: auto; display: flex; align-items: center; gap: 4px; }
.portal-back { color: var(--text-light); font-size: 13px; }
.portal-back:hover { color: var(--primary-color); }
.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-main);
  &:hover { background: #f1f5f9; }
}
.user-chip .caret { font-size: 12px; opacity: 0.7; }
.app-main {
  flex: 1;
  overflow: auto;
  padding: 18px;
}
@media (max-width: 768px) {
  .app-side {
    position: fixed;
    top: 0; left: 0;
    height: 100vh;
    width: 260px !important;
    z-index: 1000;
    box-shadow: 0 4px 24px rgba(15, 23, 42, 0.18);
    transform: translateX(-100%);
    transition: transform 0.25s ease-out;
  }
  .app-side.mobile-open {
    transform: translateX(0);
  }
  .app-main {
    padding: 14px 12px;
  }
  .app-header { padding: 0 12px; }
  .header-title {
    font-size: 14px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .user-chip { padding: 4px 8px; }
  .user-chip .user-name {
    max-width: 80px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.mobile-mask {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(15, 23, 42, 0.45);
  z-index: 999;
  animation: maskIn 0.2s ease-out;
}
@keyframes maskIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
</style>
