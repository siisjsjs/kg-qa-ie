<script setup>
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import { User, Lock } from "@element-plus/icons-vue";
import { useAuthStore } from "@/stores/auth";
import AnimatedGraphBg from "@/components/AnimatedGraphBg.vue";

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

const form = ref({ account: "", password: "" });
const loading = ref(false);

const tags = ["统一认证", "全应用通行", "安全可靠"];

async function submit() {
  if (!form.value.account || !form.value.password) {
    ElMessage.warning("请输入用户名/邮箱与密码");
    return;
  }
  loading.value = true;
  try {
    await auth.login(form.value);
    ElMessage.success("登录成功");
    sessionStorage.setItem("kg-show-announcement", "1");
    const next = route.query.next;
    router.push(typeof next === "string" ? next : "/");
  } catch (e) {
    const detail =
      e.response?.data?.detail ||
      e.response?.data?.message ||
      e.message ||
      "网络错误";
    ElMessage.error(typeof detail === "string" ? detail : JSON.stringify(detail));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="auth-page">
    <AnimatedGraphBg class="bg-canvas" />

    <main class="auth-shell">
      <RouterLink to="/" class="back-portal">← 返回知绘IE 门户</RouterLink>
      <div class="logo-badge">
        <svg width="28" height="28" viewBox="0 0 32 32" fill="none" aria-hidden="true">
          <path d="M16 2L4 8V24L16 30L28 24V8L16 2Z" stroke="#F97316" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          <path d="M16 12V22" stroke="#F97316" stroke-width="2" stroke-linecap="round" />
          <path d="M11 15L16 12L21 15" stroke="#F97316" stroke-width="2" stroke-linecap="round" />
          <circle cx="16" cy="12" r="2" fill="#F97316" />
          <circle cx="11" cy="15" r="2" fill="#F97316" />
          <circle cx="21" cy="15" r="2" fill="#F97316" />
          <circle cx="16" cy="22" r="2" fill="#F97316" />
        </svg>
        <span>知绘IE</span>
      </div>

      <h1 class="title">登录知绘IE</h1>
      <p class="subtitle">一个账号，访问知识库与全部工业工程工具；一次登录，全应用通行。</p>

      <ul class="tags" aria-label="产品特色">
        <li v-for="t in tags" :key="t">{{ t }}</li>
      </ul>

      <section class="card">
        <header class="card-head">
          <h2>欢迎回来</h2>
          <p>登录后即可进入知绘IE 各应用</p>
        </header>

        <form class="form" @submit.prevent="submit" novalidate>
          <div class="field">
            <label for="login-account">用户名 / 邮箱</label>
            <el-input
              id="login-account"
              v-model="form.account"
              placeholder="请输入用户名或邮箱"
              autocomplete="username"
              clearable
              size="large"
            >
              <template #prefix><el-icon><User /></el-icon></template>
            </el-input>
          </div>

          <div class="field">
            <label for="login-password">密码</label>
            <el-input
              id="login-password"
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              autocomplete="current-password"
              show-password
              size="large"
              @keyup.enter="submit"
            >
              <template #prefix><el-icon><Lock /></el-icon></template>
            </el-input>
          </div>

          <el-button
            type="primary"
            :loading="loading"
            class="submit-btn"
            size="large"
            @click="submit"
          >立即登录</el-button>

          <p class="footer-link">
            还没有账号？<RouterLink to="/register">注册新用户</RouterLink>
          </p>
        </form>
      </section>

      <footer class="page-footer">
        <p>© 2026 Industrial Engineering Knowledge Graph</p>
        <p>闽 ICP 备 2026010974 号 -1</p>
      </footer>
    </main>
  </div>
</template>

<style scoped>
.auth-page {
  position: relative;
  min-height: 100vh;
  background: #ffffff;
  overflow: hidden;
  isolation: isolate;
}
.bg-canvas {
  position: absolute;
  inset: 0;
  z-index: 0;
}
.auth-page::after {
  /* soft vignette to focus eye on the card */
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background: radial-gradient(ellipse at center, transparent 30%, rgba(255, 255, 255, 0.85) 100%);
}

.auth-shell {
  position: relative;
  z-index: 1;
  max-width: 480px;
  margin: 0 auto;
  padding: 64px 24px 32px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.back-portal {
  align-self: flex-start;
  margin-bottom: 20px;
  font-size: 13px;
  font-weight: 600;
  color: var(--primary-color, #f97316);
}
.back-portal:hover { text-decoration: underline; }

.logo-badge {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  background: #ffffff;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 14px;
  box-shadow: 0 4px 16px rgba(15, 23, 42, 0.05);
  font-weight: 700;
  font-size: 16px;
  color: #0f172a;
  letter-spacing: -0.01em;
  margin-bottom: 28px;
}

.title {
  margin: 0 0 12px;
  font-size: 32px;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.02em;
}
.subtitle {
  margin: 0 0 24px;
  font-size: 14px;
  line-height: 1.7;
  color: #64748b;
  max-width: 380px;
}

.tags {
  list-style: none;
  margin: 0 0 32px;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
}
.tags li {
  padding: 7px 16px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 999px;
  font-size: 13px;
  color: #475569;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.card {
  width: 100%;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 16px;
  box-shadow: 0 12px 36px rgba(15, 23, 42, 0.06);
  padding: 36px 36px 32px;
  text-align: left;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
.card-head {
  text-align: center;
  margin-bottom: 28px;
}
.card-head h2 {
  margin: 0 0 6px;
  font-size: 22px;
  font-weight: 700;
  color: #0f172a;
}
.card-head p {
  margin: 0;
  font-size: 13px;
  color: #64748b;
}

.form .field { margin-bottom: 18px; }
.form .field label {
  display: block;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
}
.form :deep(.el-input__wrapper) {
  background: #f8fafc;
  box-shadow: 0 0 0 1px #e2e8f0 inset;
  border-radius: 10px;
  padding: 0 14px;
  height: 46px;
  transition: box-shadow 160ms ease, background-color 160ms ease;
}
.form :deep(.el-input__wrapper:hover) {
  background: #ffffff;
  box-shadow: 0 0 0 1px #cbd5e1 inset;
}
.form :deep(.el-input__wrapper.is-focus) {
  background: #ffffff;
  box-shadow: 0 0 0 2px var(--primary-color, #f97316) inset !important;
}
.form :deep(.el-input__inner) {
  font-size: 14px;
  color: #0f172a;
}
.form :deep(.el-input__prefix) { color: #94a3b8; }

.submit-btn {
  width: 100%;
  height: 48px;
  margin-top: 8px;
  border-radius: 10px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.05em;
}
.submit-btn:focus-visible {
  outline: 2px solid var(--primary-color, #f97316);
  outline-offset: 3px;
}

.footer-link {
  margin: 22px 0 0;
  font-size: 13px;
  color: #64748b;
  text-align: center;
}
.footer-link :deep(a) {
  color: var(--primary-color, #f97316);
  font-weight: 600;
  margin-left: 4px;
}
.footer-link :deep(a:hover) { text-decoration: underline; }

.page-footer {
  margin-top: 36px;
  text-align: center;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.8;
}
.page-footer p { margin: 0; }

@media (max-width: 520px) {
  .auth-shell { padding: 40px 16px 24px; }
  .title { font-size: 26px; }
  .card { padding: 28px 22px 24px; }
}
</style>
