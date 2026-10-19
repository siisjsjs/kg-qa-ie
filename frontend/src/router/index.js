import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const routes = [
  {
    path: "/",
    name: "Portal",
    component: () => import("@/views/Portal.vue"),
    meta: { title: "知绘IE · 工业工程智能体平台" },
  },
  {
    path: "/login",
    name: "Login",
    component: () => import("@/views/Login.vue"),
    meta: { requiresGuest: true, title: "登录" },
  },
  {
    path: "/register",
    name: "Register",
    component: () => import("@/views/Register.vue"),
    meta: { requiresGuest: true, title: "注册" },
  },
  // 旧路径兼容：知识库迁到 /kb 后，保留原地址跳转
  { path: "/query", redirect: "/kb/query" },
  { path: "/digest", redirect: "/kb/digest" },
  { path: "/graph", redirect: "/kb/wiki" },
  { path: "/wiki", redirect: "/kb/wiki" },
  { path: "/wiki/book/:book", redirect: (to) => `/kb/wiki/book/${to.params.book}` },
  { path: "/wiki/:slug", redirect: (to) => `/kb/wiki/${to.params.slug}${to.query.kind ? `?kind=${to.query.kind}` : ""}` },
  {
    path: "/kb",
    component: () => import("@/layouts/MainLayout.vue"),
    meta: { requiresAuth: true },
    children: [
      {
        path: "",
        name: "Home",
        component: () => import("@/views/Home.vue"),
        meta: { title: "主页" },
      },
      {
        path: "dashboard",
        name: "Dashboard",
        component: () => import("@/views/GrowthDashboard.vue"),
        meta: { requiresAdmin: true, title: "控制台" },
      },
      {
        path: "query",
        name: "Query",
        component: () => import("@/views/Query.vue"),
        meta: { title: "智能问答" },
      },
      {
        path: "digest",
        name: "Digest",
        component: () => import("@/views/Digest.vue"),
        meta: { title: "深度综合" },
      },
      {
        path: "graph",
        redirect: "/kb/wiki",
      },
      {
        path: "wiki",
        name: "WikiHome",
        component: () => import("@/views/WikiHome.vue"),
        meta: { title: "Wiki 浏览" },
      },
      {
        path: "wiki/book/:book",
        name: "BookView",
        component: () => import("@/views/BookView.vue"),
        props: true,
        meta: { title: "书内浏览" },
      },
      {
        path: "wiki/:slug",
        name: "WikiPage",
        component: () => import("@/views/WikiViewer.vue"),
        props: true,
        meta: { title: "Wiki" },
      },
      {
        path: "qa-data",
        name: "QaData",
        component: () => import("@/views/system/QaData.vue"),
        meta: { requiresAdmin: true, title: "问答数据" },
      },
      {
        path: "sources",
        name: "Sources",
        component: () => import("@/views/Sources.vue"),
        meta: { requiresAdmin: true, title: "素材管理" },
      },
      {
        path: "system/model",
        name: "ModelConfig",
        component: () => import("@/views/system/ModelConfig.vue"),
        meta: { requiresAdmin: true, title: "模型配置" },
      },
      {
        path: "system/schema",
        name: "SchemaEditor",
        component: () => import("@/views/system/SchemaEditor.vue"),
        meta: { requiresAdmin: true, title: "Schema 编辑器" },
      },
      {
        path: "system/purpose",
        name: "PurposeEditor",
        component: () => import("@/views/system/PurposeEditor.vue"),
        meta: { requiresAdmin: true, title: "使命编辑器" },
      },
      {
        path: "system/users",
        name: "Users",
        component: () => import("@/views/system/Users.vue"),
        meta: { requiresAdmin: true, title: "用户管理" },
      },
      {
        path: "system/announcements",
        name: "Announcements",
        component: () => import("@/views/system/Announcements.vue"),
        meta: { requiresAdmin: true, title: "公告管理" },
      },
      {
        path: "system/feedbacks",
        name: "Feedbacks",
        component: () => import("@/views/system/Feedbacks.vue"),
        meta: { requiresAdmin: true, title: "用户反馈" },
      },
      {
        path: "system/crystallize",
        name: "Crystallize",
        component: () => import("@/views/system/Crystallize.vue"),
        meta: { requiresAdmin: true, title: "结晶化" },
      },
      {
        path: "system/scraper",
        name: "Scraper",
        component: () => import("@/views/system/Scraper.vue"),
        meta: { requiresAdmin: true, title: "网页抓取" },
      },
    ],
  },
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!auth.bootstrapped) await auth.bootstrap();

  if (to.meta?.title) {
    document.title =
      to.name === "Portal" ? to.meta.title : `${to.meta.title} · 知绘IE`;
  }

  if (to.meta?.requiresAuth && !auth.isLoggedIn) {
    return {
      path: "/login",
      query: { next: to.fullPath !== "/" ? to.fullPath : undefined },
    };
  }
  if (to.meta?.requiresAdmin && !auth.isAdmin) {
    return { path: "/kb" };
  }
  if (to.meta?.requiresGuest && auth.isLoggedIn) {
    return { path: "/kb" };
  }
});

export default router;
