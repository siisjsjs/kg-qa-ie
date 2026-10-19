<script setup>
import { ref, onMounted, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { useAuthStore } from "@/stores/auth";
import { authApi } from "@/api";
import { openDrawingPlatform as ssoOpenDrawingPlatform } from "@/utils/sso";

const router = useRouter();
const auth = useAuthStore();

const bannerEl = ref(null);
const netCanvasEl = ref(null);
const appRowEl = ref(null);

const helpItems = ref([
  {
    q: "如何开始使用知绘IE？",
    a: "知绘IE 所有应用共用同一账号。点击右上角「登录 / 注册」完成一次登录，即可在门户中访问知识库、绘图工具等全部应用；未登录时点进任何应用，也会先跳转到统一的登录页面。",
    open: false,
  },
  {
    q: "登录与账号问题",
    a: "账号由统一认证中心签发访问令牌，一次登录即可通行所有应用，无需重复登录。令牌默认 60 分钟有效，到期后系统会自动静默续期；忘记密码请联系平台管理员重置。",
    open: false,
  },
  {
    q: "如何在门户中切换应用？",
    a: "回到门户首页「平台应用」区，点击对应应用图标即可直达。网关会根据请求路径自动分流到正确的后端服务，无需手动切换站点，也无需再次登录。",
    open: false,
  },
  {
    q: "如何快速上手各工具？",
    a: "「知识库」支持智能问答、知识图谱与 Wiki 浏览：直接在问答框提问，答案会附带可溯源的知识来源。「绘图工具」提供图表模板与 SOP 生成，可从模板中心开始，再逐步定制数据与样式；「产线平衡」工作台支持款式、工序与工位建模及寻优评估。",
    open: false,
  },
  {
    q: "常见问题（FAQ）",
    a: "问：知识库答案可靠吗？—— 答案基于 Wiki 语料生成并附来源，可在问答页核对引用。\n问：绘图工具能用自己的数据吗？—— 支持，在模板中替换数据源即可，可导出图表与文件。",
    open: false,
  },
  {
    q: "联系我们",
    a: "使用中遇到问题或有建议，欢迎从知识库主页底部的「用户反馈」提交，我们会逐条处理；如需进一步协助，可联系平台管理员。",
    open: false,
  },
]);

function toggleHelp(i) {
  const wasOpen = helpItems.value[i].open;
  helpItems.value.forEach((it) => (it.open = false));
  helpItems.value[i].open = !wasOpen;
}

function enterApp(key) {
  if (key === "kb") {
    if (auth.isLoggedIn) {
      router.push("/kb");
    } else {
      router.push({ path: "/login", query: { next: "/kb" } });
    }
  } else if (key === "chart") {
    openDrawingPlatform();
  } else if (key === "lb") {
    openDrawingPlatform("/line-balance-studio");
  }
}

async function openDrawingPlatform(redirect = "/templates") {
  if (!auth.isLoggedIn) {
    router.push({ path: "/login", query: { next: "/" } });
    return;
  }
  const ok = await ssoOpenDrawingPlatform(redirect);
  if (!ok) router.push({ path: "/login", query: { next: "/" } });
}

async function handleLogout() {
  await auth.logout();
  router.push("/");
}

/* ===== 账号管理 ===== */
const accountVisible = ref(false);
const pwSubmitting = ref(false);
const pwForm = ref({ old_password: "", new_password: "", confirm_password: "" });

function openAccountDialog() {
  pwForm.value = { old_password: "", new_password: "", confirm_password: "" };
  accountVisible.value = true;
}

async function submitChangePassword() {
  const f = pwForm.value;
  if (!f.old_password) {
    ElMessage.warning("请输入旧密码");
    return;
  }
  if (!f.new_password || f.new_password.length < 8) {
    ElMessage.warning("新密码至少 8 位");
    return;
  }
  if (f.new_password !== f.confirm_password) {
    ElMessage.warning("两次输入的新密码不一致");
    return;
  }
  pwSubmitting.value = true;
  try {
    await authApi.changePassword({
      old_password: f.old_password,
      new_password: f.new_password,
    });
    ElMessage.success("密码已更新，请重新登录");
    accountVisible.value = false;
    await auth.logout();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  } finally {
    pwSubmitting.value = false;
  }
}

function contactUs() {
  const idx = helpItems.value.findIndex((it) => it.q === "联系我们");
  if (idx >= 0) toggleHelp(idx);
  document.getElementById("help")?.scrollIntoView({ behavior: "smooth" });
}

/* ===== Banner 交互粒子网络 ===== */
let ctx = null;
let bannerNode = null;
let canvasNode = null;
let parts = [];
let W = 0;
let H = 0;
let rafId = null;
let running = false;
let reduced = false;
let moveH = null;
let leaveH = null;
let touchH = null;
let visH = null;
let resizeH = null;
const mouse = { x: -9999, y: -9999 };
const LINK = 110;
const MOUSE_LINK = 170;
const REPEL = 130;

function netResize() {
  if (!ctx || !canvasNode || !bannerNode) return;
  const DPR = Math.min(window.devicePixelRatio || 1, 2);
  W = bannerNode.clientWidth;
  H = bannerNode.clientHeight;
  canvasNode.width = W * DPR;
  canvasNode.height = H * DPR;
  canvasNode.style.width = W + "px";
  canvasNode.style.height = H + "px";
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  const n = Math.min(80, Math.max(28, Math.round((W * H) / 16000)));
  parts = [];
  for (let i = 0; i < n; i++) {
    parts.push({
      x: Math.random() * W,
      y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.35,
      vy: (Math.random() - 0.5) * 0.35,
      r: Math.random() * 1.5 + 1,
    });
  }
}

function netStep() {
  let i, j, p, q, dx, dy, d2, d, a;
  ctx.clearRect(0, 0, W, H);
  for (i = 0; i < parts.length; i++) {
    p = parts[i];
    dx = p.x - mouse.x;
    dy = p.y - mouse.y;
    d2 = dx * dx + dy * dy;
    if (d2 < REPEL * REPEL && d2 > 0.5) {
      d = Math.sqrt(d2);
      p.vx += (dx / d) * 0.05;
      p.vy += (dy / d) * 0.05;
    }
    p.x += p.vx;
    p.y += p.vy;
    if (p.x < -20) p.x = W + 20;
    else if (p.x > W + 20) p.x = -20;
    if (p.y < -20) p.y = H + 20;
    else if (p.y > H + 20) p.y = -20;
    if (d2 < MOUSE_LINK * MOUSE_LINK) {
      a = 1 - Math.sqrt(d2) / MOUSE_LINK;
      ctx.strokeStyle = "rgba(255,255,255," + (a * 0.3).toFixed(3) + ")";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(mouse.x, mouse.y);
      ctx.stroke();
    }
  }
  for (i = 0; i < parts.length; i++) {
    for (j = i + 1; j < parts.length; j++) {
      p = parts[i];
      q = parts[j];
      dx = p.x - q.x;
      dy = p.y - q.y;
      d2 = dx * dx + dy * dy;
      if (d2 < LINK * LINK) {
        a = 1 - Math.sqrt(d2) / LINK;
        ctx.strokeStyle = "rgba(160,190,255," + (a * 0.22).toFixed(3) + ")";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(q.x, q.y);
        ctx.stroke();
      }
    }
  }
  for (i = 0; i < parts.length; i++) {
    p = parts[i];
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r, 0, 6.2832);
    ctx.fill();
  }
  if (running) rafId = requestAnimationFrame(netStep);
}

function netStart() {
  if (reduced) {
    netStep();
    return;
  }
  if (running) return;
  running = true;
  netStep();
}

function netStop() {
  running = false;
  if (rafId) cancelAnimationFrame(rafId);
  rafId = null;
}

function setupNet() {
  canvasNode = netCanvasEl.value;
  bannerNode = bannerEl.value;
  if (!canvasNode || !bannerNode || !canvasNode.getContext) return;
  ctx = canvasNode.getContext("2d");
  reduced =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  moveH = (e) => {
    const r = canvasNode.getBoundingClientRect();
    mouse.x = e.clientX - r.left;
    mouse.y = e.clientY - r.top;
  };
  leaveH = () => {
    mouse.x = -9999;
    mouse.y = -9999;
  };
  touchH = (e) => {
    if (e.touches.length) {
      const r = canvasNode.getBoundingClientRect();
      mouse.x = e.touches[0].clientX - r.left;
      mouse.y = e.touches[0].clientY - r.top;
    }
  };
  visH = () => {
    if (document.hidden) netStop();
    else netStart();
  };
  resizeH = netResize;

  bannerNode.addEventListener("mousemove", moveH);
  bannerNode.addEventListener("mouseleave", leaveH);
  bannerNode.addEventListener("touchmove", touchH, { passive: true });
  document.addEventListener("visibilitychange", visH);
  window.addEventListener("resize", resizeH);

  netResize();
  netStart();
}

function teardownNet() {
  netStop();
  if (bannerNode) {
    if (moveH) bannerNode.removeEventListener("mousemove", moveH);
    if (leaveH) bannerNode.removeEventListener("mouseleave", leaveH);
    if (touchH) bannerNode.removeEventListener("touchmove", touchH);
  }
  if (visH) document.removeEventListener("visibilitychange", visH);
  if (resizeH) window.removeEventListener("resize", resizeH);
  bannerNode = null;
  canvasNode = null;
  ctx = null;
}

/* ===== 应用图标行：横向溢出提示 ===== */
function syncAppRowOverflow() {
  const row = appRowEl.value;
  if (!row) return;
  if (row.scrollWidth > row.clientWidth + 4) row.classList.add("has-overflow");
  else row.classList.remove("has-overflow");
}

function onWindowResize() {
  syncAppRowOverflow();
}

onMounted(() => {
  document.documentElement.style.scrollBehavior = "smooth";
  setupNet();
  syncAppRowOverflow();
  window.addEventListener("resize", onWindowResize);
});

onBeforeUnmount(() => {
  teardownNet();
  window.removeEventListener("resize", onWindowResize);
  document.documentElement.style.scrollBehavior = "";
});
</script>

<template>
  <div class="portal">
    <!-- 顶部导航 -->
    <header class="topbar">
      <div class="topbar-inner">
        <a class="brand" href="#top" aria-label="回到顶部">
          <span class="brand-mark">
            <svg width="28" height="28" viewBox="0 0 32 32" fill="none" aria-hidden="true">
              <defs>
                <linearGradient id="bmarkGrad" x1="16" y1="2" x2="16" y2="30" gradientUnits="userSpaceOnUse">
                  <stop offset="0" stop-color="#FB923C"/>
                  <stop offset="1" stop-color="#EA580C"/>
                </linearGradient>
              </defs>
              <path d="M16 2L4 8V24L16 30L28 24V8L16 2Z" fill="url(#bmarkGrad)"/>
              <path d="M16 12V22" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>
              <path d="M11 15L16 12L21 15" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>
              <circle cx="16" cy="12" r="2.2" fill="#fff"/>
              <circle cx="11" cy="15" r="2.2" fill="#fff"/>
              <circle cx="21" cy="15" r="2.2" fill="#fff"/>
              <circle cx="16" cy="22" r="2.2" fill="#fff"/>
            </svg>
          </span>
          <span>
            <span class="brand-name">知绘<span class="ie">IE</span></span>
            <div class="brand-sub">INDUSTRIAL ENGINEERING PLATFORM</div>
          </span>
        </a>

        <nav class="nav-links" aria-label="主导航">
          <a href="#apps">平台应用</a>
          <a href="#about">关于平台</a>
          <a href="#help">帮助中心</a>
        </nav>

        <div class="nav-actions">
          <template v-if="auth.isLoggedIn">
            <span class="user-chip">{{ auth.userInfo?.username || auth.userInfo?.email }}</span>
            <button type="button" class="btn ghost" @click="openAccountDialog">账号管理</button>
            <button type="button" class="btn ghost" @click="handleLogout">退出登录</button>
          </template>
          <template v-else>
            <button type="button" class="btn ghost" @click="router.push('/login')">登录</button>
            <button type="button" class="btn primary" @click="router.push('/register')">注册</button>
          </template>
        </div>
      </div>
    </header>

    <!-- 华丽 Banner -->
    <section class="banner" id="top" ref="bannerEl" aria-label="平台介绍">
      <canvas class="banner-net" ref="netCanvasEl" aria-hidden="true"></canvas>
      <div class="gear g1" aria-hidden="true"></div>
      <div class="gear g2" aria-hidden="true"></div>
      <div class="banner-glow b1" aria-hidden="true"></div>
      <div class="banner-glow b2" aria-hidden="true"></div>
      <div class="banner-inner">
        <div class="banner-badge">知绘IE · 智能体平台 · 统一门户</div>
        <h1>让工业工程 <span class="hl">更智能</span></h1>
        <p class="sub">统一门户，接入面向工业工程的一系列工具——知识沉淀、智能问答、数据分析与图形化表达，一站直达。</p>
        <div class="banner-cta">
          <button type="button" class="btn primary" @click="enterApp('kb')">进入知识库</button>
          <button type="button" class="btn ghost" @click="enterApp('chart')">进入绘图工具</button>
        </div>
      </div>
    </section>

    <!-- 应用启动器 -->
    <main class="apps" id="apps">
      <div class="section-head">
        <span class="tag">APP CENTER</span>
        <h2>平台应用 · 点击图标进入</h2>
        <p>知绘IE 全部应用，图标即入口，横向滑动查看更多</p>
      </div>

      <div class="app-row" ref="appRowEl" role="list" aria-label="平台应用图标">
        <button type="button" class="app-icon" role="listitem" aria-label="进入知识库" @click="enterApp('kb')">
          <span class="i ac-orange">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>
              <path d="M9 8h6M9 12h6"/>
            </svg>
          </span>
          <span class="n">知识库</span>
        </button>

        <button type="button" class="app-icon" role="listitem" aria-label="进入绘图工具" @click="enterApp('chart')">
          <span class="i ac-blue">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M3 3v18h18"/><path d="M7 14l3-4 3 2 4-6"/>
              <circle cx="7" cy="14" r="1.3" fill="currentColor" stroke="none"/>
              <circle cx="10" cy="10" r="1.3" fill="currentColor" stroke="none"/>
              <circle cx="13" cy="12" r="1.3" fill="currentColor" stroke="none"/>
              <circle cx="17" cy="6" r="1.3" fill="currentColor" stroke="none"/>
            </svg>
          </span>
          <span class="n">绘图工具</span>
        </button>

        <button type="button" class="app-icon" role="listitem" aria-label="进入产线平衡" @click="enterApp('lb')">
          <span class="i ac-green">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 4v16"/><path d="M5 20h14"/><path d="M7 8h10"/><path d="M7 8l-3 6a3 3 0 0 0 6 0l-3-6z"/><path d="M17 8l-3 6a3 3 0 0 0 6 0l-3-6z"/>
            </svg>
          </span>
          <span class="n">产线平衡</span>
        </button>
      </div>

      <div class="app-desc" role="list" aria-label="应用介绍">
        <div class="d-item" role="listitem">
          <span class="dot" style="background:#f97316"></span>
          <span class="d-name">知识库</span>
          <span class="d-text">智能问答、知识图谱、深度综合与 Wiki 浏览，答案可溯源，知识持续生长。</span>
        </div>
        <div class="d-item" role="listitem">
          <span class="dot" style="background:#2563eb"></span>
          <span class="d-name">绘图工具</span>
          <span class="d-text">专业图表模板与 SOP 生成，让数据与流程可视化。</span>
        </div>
        <div class="d-item" role="listitem">
          <span class="dot" style="background:#10b981"></span>
          <span class="d-name">产线平衡</span>
          <span class="d-text">产线平衡工作台：款式、工序与工位建模，寻优方案与 KPI 评估。</span>
        </div>
      </div>
    </main>

    <!-- 关于平台 / 帮助中心 -->
    <section class="info" id="about">
      <div class="info-panel">
        <h3><span class="tag-line"></span>关于知绘IE</h3>
        <div class="lead">ABOUT US</div>
        <p class="txt">
          知绘IE 是面向工业工程领域的智能体平台，以统一门户接入一系列工业工程工具——知识库、绘图工具与产线平衡是率先上线的一批应用。
          通过前端统一网关，一次登录即可访问全部应用，请求按路径自动分流到对应后端，无需重复登录、无需切换站点；更多工具将持续接入同一门户。
        </p>
        <ul class="about-subs">
          <li>
            <span class="badge kb">知识库</span>
            <span>知识沉淀 · 智能问答 · 知识图谱</span>
            <span class="mods">问答 / 图谱 / 综合 / Wiki</span>
          </li>
          <li>
            <span class="badge chart">绘图工具</span>
            <span>图表绘制 · SOP 生成</span>
            <span class="mods">模板 / SOP</span>
          </li>
          <li>
            <span class="badge lb">产线平衡</span>
            <span>产线平衡工作台 · 工序工位建模</span>
            <span class="mods">平衡 / KPI</span>
          </li>
          <li class="more">
            <span class="badge more">更多工具</span>
            <span>面向工业工程的更多工具持续接入中</span>
            <span class="mods">敬请期待</span>
          </li>
        </ul>
      </div>

      <div class="info-panel" id="help">
        <h3><span class="tag-line"></span>帮助中心</h3>
        <div class="lead">HELP CENTER</div>
        <ul class="help-list">
          <li
            v-for="(item, i) in helpItems"
            :key="i"
            :class="{ open: item.open }"
          >
            <button
              type="button"
              class="h-q"
              :aria-expanded="item.open ? 'true' : 'false'"
              @click="toggleHelp(i)"
            >
              {{ item.q }}
              <svg class="arr" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
            </button>
            <div class="h-a"><p>{{ item.a }}</p></div>
          </li>
        </ul>
      </div>
    </section>

    <!-- 账号管理 -->
    <el-dialog v-model="accountVisible" title="账号管理" width="460" align-center>
      <div class="acct-info">
        <div class="acct-row"><span class="acct-label">用户名</span><span>{{ auth.userInfo?.username || "-" }}</span></div>
        <div class="acct-row"><span class="acct-label">邮箱</span><span>{{ auth.userInfo?.email || "-" }}</span></div>
        <div class="acct-row"><span class="acct-label">角色</span><span>{{ auth.isAdmin ? "管理员" : "普通用户" }}</span></div>
      </div>
      <el-divider>修改密码</el-divider>
      <el-form label-position="top" @submit.prevent="submitChangePassword">
        <el-form-item label="旧密码" required>
          <el-input
            v-model="pwForm.old_password"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="请输入当前密码"
          />
        </el-form-item>
        <el-form-item label="新密码" required>
          <el-input
            v-model="pwForm.new_password"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="至少 8 位"
          />
        </el-form-item>
        <el-form-item label="确认新密码" required>
          <el-input
            v-model="pwForm.confirm_password"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="再输入一次新密码"
            @keyup.enter="submitChangePassword"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="accountVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwSubmitting" @click="submitChangePassword">确认修改</el-button>
      </template>
    </el-dialog>

    <!-- 页脚 -->
    <footer class="footer">
      <div class="footer-inner">
        <nav class="f-links" aria-label="页脚链接">
          <a href="#about">关于知绘IE</a>
          <a href="#help">帮助中心</a>
          <a href="#help" @click.prevent="contactUs">联系我们</a>
        </nav>
        <p>© 2026 知绘IE · 工业工程智能体平台 · 闽 ICP 备 2026010974 号 -1</p>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.portal {
  --brand: #f97316;
  --brand-deep: #ea580c;
  --navy: #16306b;
  --navy-deep: #0f2452;
  --blue: #2563eb;
  --bg: #f4f6fa;
  --card: #ffffff;
  --line: #e5e9f0;
  --text-hi: #1e293b;
  --text-mid: #55677d;
  --text-low: #8aa0b8;
  --radius: 16px;
  --shadow: 0 6px 20px rgba(22, 48, 107, 0.08);
  --font: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, -apple-system, sans-serif;
  --ease: cubic-bezier(0.22, 0.61, 0.36, 1);

  font-family: var(--font);
  background: var(--bg);
  color: var(--text-hi);
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}
.portal a { color: inherit; text-decoration: none; }
.portal a:hover { color: inherit; }

/* ============ 顶部导航 ============ */
.topbar { position: relative; z-index: 10; background: var(--navy); color: #fff; }
.topbar-inner {
  max-width: 1200px; margin: 0 auto; padding: 0 24px;
  display: flex; align-items: center; justify-content: space-between; height: 68px;
}
.brand { display: inline-flex; align-items: center; gap: 12px; }
.brand-mark {
  width: 40px; height: 40px; border-radius: 11px; background: #fff;
  display: grid; place-items: center; color: var(--brand);
  box-shadow: 0 2px 10px rgba(0,0,0,0.25);
}
.brand-name { font-size: 22px; font-weight: 800; letter-spacing: 0.03em; }
.brand-name .ie { color: #ffb066; font-weight: 700; }
.brand-sub { font-size: 11px; color: rgba(255,255,255,0.6); letter-spacing: 0.14em; margin-top: -2px; }

.nav-links { display: flex; align-items: center; gap: 26px; }
.nav-links a {
  font-size: 14.5px; color: rgba(255,255,255,0.82);
  padding: 6px 2px; border-bottom: 2px solid transparent;
  transition: color 160ms var(--ease), border-color 160ms var(--ease);
}
.nav-links a:hover { color: #fff; border-bottom-color: var(--brand); }

.nav-actions { display: flex; align-items: center; gap: 10px; }
.user-chip {
  font-size: 13px; color: rgba(255,255,255,0.92);
  padding: 6px 14px; border: 1px solid rgba(255,255,255,0.3); border-radius: 999px;
}
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  min-height: 40px; padding: 0 18px; border-radius: 9px;
  font-size: 14px; font-weight: 600; border: 1px solid transparent; cursor: pointer;
  font-family: inherit;
  transition: background 170ms var(--ease), color 170ms var(--ease), transform 170ms var(--ease), box-shadow 170ms var(--ease);
}
.btn:active { transform: translateY(1px); }
.btn:focus-visible { outline: 2px solid var(--brand); outline-offset: 3px; }
.btn.ghost { border-color: rgba(255,255,255,0.35); color: #fff; background: transparent; }
.btn.ghost:hover { background: rgba(255,255,255,0.12); }
.btn.primary { background: var(--brand); color: #fff; }
.btn.primary:hover { background: var(--brand-deep); box-shadow: 0 6px 18px rgba(249,115,22,0.4); }

/* ============ 华丽 Banner ============ */
.banner {
  position: relative;
  background: linear-gradient(135deg, #1c3d85 0%, var(--navy) 55%, var(--navy-deep) 100%);
  color: #fff; overflow: hidden;
}
.banner::before {
  content: ""; position: absolute; inset: 0;
  background-image:
    linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px);
  background-size: 52px 52px;
  mask-image: radial-gradient(ellipse 80% 90% at 50% 0%, #000 20%, transparent 75%);
  -webkit-mask-image: radial-gradient(ellipse 80% 90% at 50% 0%, #000 20%, transparent 75%);
}
.banner-net { position: absolute; inset: 0; z-index: 1; width: 100%; height: 100%; pointer-events: none; }
.banner-glow { position: absolute; width: 520px; height: 520px; border-radius: 50%; filter: blur(80px); opacity: 0.55; }
.banner-glow.b1 { top: -180px; right: -60px; background: radial-gradient(circle, rgba(249,115,22,0.5), transparent 65%); }
.banner-glow.b2 { bottom: -220px; left: -80px; background: radial-gradient(circle, rgba(37,99,235,0.5), transparent 65%); }
.gear { position: absolute; border: 2px solid rgba(255,255,255,0.10); border-radius: 50%; }
.gear.g1 { width: 340px; height: 340px; right: -70px; top: -90px; animation: portal-spin 40s linear infinite; }
.gear.g1::after { content:""; position:absolute; inset: 46px; border: 2px dashed rgba(255,255,255,0.08); border-radius: 50%; }
.gear.g2 { width: 180px; height: 180px; right: 180px; bottom: -70px; animation: portal-spin 30s linear infinite reverse; }
.gear.g2::after { content:""; position:absolute; inset: 30px; border: 2px dashed rgba(255,255,255,0.09); border-radius: 50%; }
@keyframes portal-spin { to { transform: rotate(360deg); } }

.banner-inner { position: relative; z-index: 2; max-width: 1200px; margin: 0 auto; padding: 64px 24px 60px; text-align: center; }
.banner-badge {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 6px 16px; border-radius: 999px;
  background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.2);
  font-size: 13px; letter-spacing: 0.08em; color: rgba(255,255,255,0.9); margin-bottom: 22px;
}
.banner h1 { font-size: clamp(34px, 6vw, 54px) !important; font-weight: 800; letter-spacing: 0.02em; line-height: 1.2; }
.banner h1 .hl { color: #ffb066; }
.banner .sub { max-width: 660px; margin: 18px auto 0; font-size: 15.5px; color: rgba(255,255,255,0.8); line-height: 1.85; }
.banner-cta { display: flex; justify-content: center; gap: 14px; margin-top: 32px; flex-wrap: nowrap; }
.banner-cta .btn { min-height: 46px; padding: 0 26px; font-size: 15px; border-radius: 11px; flex: none; }

/* ============ 应用启动器 ============ */
.apps { max-width: 1200px; margin: 0 auto; padding: 44px 24px 10px; }
.section-head { text-align: center; margin-bottom: 36px; }
.section-head .tag {
  display: inline-block; font-size: 12px; font-weight: 700; letter-spacing: 0.14em;
  color: var(--brand-deep); margin-bottom: 10px;
}
.section-head h2 { font-size: 26px !important; font-weight: 800; letter-spacing: 0.01em; }
.section-head p { margin-top: 10px; font-size: 14px; color: var(--text-mid); }

.app-row {
  position: relative;
  display: flex;
  flex-wrap: nowrap;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
  gap: 40px;
  padding: 30px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  justify-content: center;
}
.app-row::-webkit-scrollbar { display: none; }
.app-row.has-overflow { justify-content: flex-start; }

.app-icon {
  flex: none;
  display: flex; flex-direction: column; align-items: center; gap: 14px;
  background: none; border: none; cursor: pointer; font-family: inherit;
  padding: 10px 14px; border-radius: 16px;
  scroll-snap-align: center;
  transition: transform 190ms var(--ease);
}
.app-icon:hover { transform: translateY(-6px); }
.app-icon:active { transform: translateY(-1px); }
.app-icon:focus-visible { outline: 2px solid var(--brand); outline-offset: 3px; }
.app-icon .i {
  width: 84px; height: 84px; border-radius: 24px;
  display: grid; place-items: center;
  transition: box-shadow 190ms var(--ease), filter 190ms var(--ease);
}
.app-icon:hover .i { filter: brightness(1.03); box-shadow: 0 14px 30px rgba(22,48,107,0.18); }
.app-icon .n { font-size: 16px; font-weight: 700; color: var(--text-hi); white-space: nowrap; }

.ac-orange { color: #f97316; background: #fff3ea; }
.ac-blue   { color: #2563eb; background: #eef3ff; }
.ac-green  { color: #10b981; background: #ecfdf5; }

.app-desc {
  margin-top: 20px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 22px 30px;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px 44px;
}
.d-item { display: flex; align-items: baseline; gap: 10px; font-size: 14px; color: var(--text-mid); }
.d-item .dot { flex: none; width: 9px; height: 9px; border-radius: 50%; margin-top: -2px; }
.d-item .d-name { flex: none; font-weight: 700; color: var(--text-hi); min-width: 84px; font-size: 14.5px; }
.d-item .d-text { line-height: 1.7; }

/* ============ 关于平台 / 帮助中心 ============ */
.info {
  max-width: 1200px; margin: 0 auto; padding: 26px 24px 0;
  display: grid; grid-template-columns: 1.2fr 1fr; gap: 22px;
}
.info-panel {
  background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 26px 28px;
}
.info-panel h3 {
  font-size: 17px !important; font-weight: 800; margin-bottom: 6px;
  display: flex; align-items: center; gap: 8px;
}
.info-panel h3 .tag-line { flex: none; width: 5px; height: 18px; border-radius: 3px; background: var(--brand); }
.info-panel .lead { font-size: 13px; color: var(--text-low); letter-spacing: 0.06em; margin-bottom: 16px; }
.info-panel p.txt { font-size: 14px; color: var(--text-mid); line-height: 1.85; }
.about-subs {
  list-style: none; margin-top: 16px; padding: 0;
  display: flex; flex-direction: column; gap: 10px;
}
.about-subs li {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 16px; border: 1px solid var(--line); border-radius: 12px;
  background: #fafbfe; font-size: 13.5px; color: var(--text-hi);
}
.about-subs .badge {
  flex: none; font-size: 11.5px; font-weight: 700;
  padding: 3px 10px; border-radius: 999px;
}
.about-subs .badge.kb { color: var(--brand-deep); background: #fff3ea; }
.about-subs .badge.chart { color: var(--blue, #2563eb); background: #eef3ff; }
.about-subs .badge.lb { color: #059669; background: #ecfdf5; }
.about-subs .badge.more { color: var(--text-low); background: #f1f2f5; }
.about-subs li.more { border-style: dashed; background: transparent; color: var(--text-low); }
.about-subs .mods { color: var(--text-low); font-size: 12.5px; margin-left: auto; }

.help-list { list-style: none; margin-top: 6px; padding: 0; display: flex; flex-direction: column; }
.help-list li { border-bottom: 1px dashed var(--line); }
.help-list li:last-child { border-bottom: none; }
.help-list .h-q {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 14px 2px; font-size: 14px; font-weight: 600; color: var(--text-mid);
  font-family: inherit; background: none; border: none; cursor: pointer; text-align: left;
  transition: color 160ms var(--ease);
}
.help-list .h-q:hover { color: var(--brand-deep); }
.help-list li.open .h-q { color: var(--brand-deep); }
.help-list .arr { margin-left: auto; color: var(--text-low); flex: none; transition: transform 220ms var(--ease); }
.help-list li.open .arr { transform: rotate(180deg); color: var(--brand-deep); }
.help-list .h-a { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 260ms var(--ease); }
.help-list .h-a > p { overflow: hidden; min-height: 0; margin: 0; font-size: 13.5px; line-height: 1.8; color: var(--text-mid); padding: 0 2px; white-space: pre-line; }
.help-list li.open .h-a { grid-template-rows: 1fr; }
.help-list li.open .h-a > p { padding-bottom: 14px; }

/* ============ 账号管理 ============ */
.acct-info {
  display: flex; flex-direction: column; gap: 10px; margin-bottom: 6px;
}
.acct-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 9px 12px; background: #fafbfe; border: 1px solid var(--line); border-radius: 10px;
  font-size: 13.5px; color: var(--text-hi);
}
.acct-label { color: var(--text-mid); font-size: 13px; }

/* ============ 页脚 ============ */
.footer { margin-top: 50px; background: var(--navy-deep); color: rgba(255,255,255,0.75); }
.footer-inner { max-width: 1200px; margin: 0 auto; padding: 30px 24px 34px; text-align: center; }
.footer-inner p { font-size: 12.5px; line-height: 1.9; }
.footer-inner .f-links { margin-bottom: 12px; display: flex; justify-content: center; gap: 26px; }
.footer-inner .f-links a { font-size: 13px; color: rgba(255,255,255,0.7); transition: color 160ms var(--ease); }
.footer-inner .f-links a:hover { color: #fff; }

/* ============ 响应式 ============ */
@media (max-width: 960px) {
  .info { grid-template-columns: 1fr; }
  .app-desc { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .nav-links { display: none; }
  .banner-inner { padding: 46px 18px 44px; }
  .banner-cta { gap: 10px; }
  .banner-cta .btn { padding: 0 16px; font-size: 14px; }
  .apps { padding: 34px 16px 8px; }
  .app-row { padding: 24px 14px; gap: 24px; }
  .app-icon .i { width: 72px; height: 72px; border-radius: 20px; }
  .app-desc { padding: 18px 18px; }
  .info { padding: 20px 16px 0; }
  .info-panel { padding: 20px 18px; }
  .footer-inner { padding: 26px 16px 30px; }
}

@media (prefers-reduced-motion: reduce) {
  .portal *, .portal *::before, .portal *::after { animation: none !important; transition: none !important; }
}
</style>
