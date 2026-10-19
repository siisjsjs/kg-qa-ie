<script setup>
import { ref, computed } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { User, Lock, Message } from "@element-plus/icons-vue";
import { useAuthStore } from "@/stores/auth";
import AnimatedGraphBg from "@/components/AnimatedGraphBg.vue";

const router = useRouter();
const auth = useAuthStore();

const form = ref({
  username: "",
  email: "",
  password: "",
  password2: "",
});
const loading = ref(false);

const tags = ["管理员审核", "用户名 + 邮箱", "强密码保护"];

const passwordStrength = computed(() => {
  const p = form.value.password;
  if (!p) return { score: 0, label: "" };
  let score = 0;
  if (p.length >= 8) score += 1;
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) score += 1;
  if (/\d/.test(p)) score += 1;
  if (/[^a-zA-Z0-9]/.test(p)) score += 1;
  const labels = ["弱", "弱", "中", "强", "强"];
  return { score, label: labels[score] };
});

async function submit() {
  if (!form.value.username || !form.value.email || !form.value.password) {
    ElMessage.warning("请完整填写注册信息");
    return;
  }
  if (form.value.password !== form.value.password2) {
    ElMessage.warning("两次输入的密码不一致");
    return;
  }
  if (form.value.password.length < 8) {
    ElMessage.warning("密码至少 8 位");
    return;
  }
  loading.value = true;
  try {
    const data = await auth.register({
      username: form.value.username,
      email: form.value.email,
      password: form.value.password,
    });
    const status = data?.status;
    if (status === "pending") {
      ElMessage.success("注册成功，账号已提交管理员审核，待审核通过后方可登录");
    } else {
      ElMessage.success("注册成功，请使用账号登录");
    }
    router.push("/");
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

      <h1 class="title">加入知绘IE</h1>
      <p class="subtitle">注册后由管理员审核，审核通过后即可访问知识库与全部工业工程工具。</p>

      <ul class="tags" aria-label="注册说明">
        <li v-for="t in tags" :key="t">{{ t }}</li>
      </ul>

      <section class="card">
        <header class="card-head">
          <h2>创建账号</h2>
          <p>填写信息提交后等待管理员审核</p>
        </header>

        <form class="form" @submit.prevent="submit" novalidate>
          <div class="field">
            <label for="reg-username">用户名</label>
            <el-input
              id="reg-username"
              v-model="form.username"
              placeholder="中文 / 英文 / 数字 / _.- ,2-32 位"
              autocomplete="username"
              clearable
              size="large"
            >
              <template #prefix><el-icon><User /></el-icon></template>
            </el-input>
          </div>

          <div class="field">
            <label for="reg-email">邮箱</label>
            <el-input
              id="reg-email"
              v-model="form.email"
              placeholder="请输入邮箱"
              type="email"
              autocomplete="email"
              clearable
              size="large"
            >
              <template #prefix><el-icon><Message /></el-icon></template>
            </el-input>
          </div>

          <div class="field">
            <label for="reg-password">密码</label>
            <el-input
              id="reg-password"
              v-model="form.password"
              type="password"
              placeholder="≥8 位，建议含大小写、数字、符号"
              autocomplete="new-password"
              show-password
              size="large"
            >
              <template #prefix><el-icon><Lock /></el-icon></template>
            </el-input>
            <div class="strength-row" v-if="form.password">
              <div class="strength-bar" :data-score="passwordStrength.score" />
              <span>强度：{{ passwordStrength.label }}</span>
            </div>
          </div>

          <div class="field">
            <label for="reg-password2">确认密码</label>
            <el-input
              id="reg-password2"
              v-model="form.password2"
              type="password"
              placeholder="再次输入密码"
              autocomplete="new-password"
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
          >立即注册</el-button>

          <p class="footer-link">
            已有账号？<RouterLink to="/login">直接登录</RouterLink>
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
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background: radial-gradient(ellipse at center, transparent 30%, rgba(255, 255, 255, 0.85) 100%);
}

/* ===== shell ===== */
.auth-shell {
  position: relative;
  z-index: 1;
  max-width: 480px;
  margin: 0 auto;
  padding: 56px 24px 32px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.back-portal {
  align-self: flex-start;
  margin-bottom: 16px;
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
  margin-bottom: 24px;
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
  max-width: 400px;
}

.tags {
  list-style: none;
  margin: 0 0 28px;
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
  margin-bottom: 24px;
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

.form .field {
  margin-bottom: 16px;
}
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
.form :deep(.el-input__prefix) {
  color: #94a3b8;
}

.strength-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #64748b;
  margin-top: 8px;
}
.strength-bar {
  flex: 1;
  height: 4px;
  background: #e2e8f0;
  border-radius: 2px;
  position: relative;
  overflow: hidden;
}
.strength-bar::before {
  content: "";
  position: absolute;
  inset: 0;
  width: calc(var(--w, 0) * 25%);
  background: var(--c, #ef4444);
  transition: width 0.3s;
}
.strength-bar[data-score="0"] { --w: 0; --c: #ef4444; }
.strength-bar[data-score="1"] { --w: 1; --c: #ef4444; }
.strength-bar[data-score="2"] { --w: 2; --c: #f59e0b; }
.strength-bar[data-score="3"] { --w: 3; --c: #84cc16; }
.strength-bar[data-score="4"] { --w: 4; --c: #10b981; }

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
.footer-link :deep(a:hover) {
  text-decoration: underline;
}

.page-footer {
  margin-top: 36px;
  text-align: center;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.8;
}
.page-footer p {
  margin: 0;
}

@media (max-width: 520px) {
  .auth-shell {
    padding: 40px 16px 24px;
  }
  .title { font-size: 26px; }
  .card { padding: 28px 22px 24px; }
}
</style>
