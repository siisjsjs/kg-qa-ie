<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import {
  ArrowRight,
  ChatLineRound,
  Notebook,
  Share,
  Connection,
  Aim,
  StarFilled,
  Bell,
  Calendar,
  ChatDotSquare,
  Promotion,
} from "@element-plus/icons-vue";
import { announcementApi, feedbackApi } from "@/api";
import { useAuthStore } from "@/stores/auth";
import MarkdownViewer from "@/components/MarkdownViewer.vue";

const router = useRouter();
const auth = useAuthStore();

// ===== 公告 =====
const announcements = ref([]);
const annLoading = ref(false);
const detail = ref(null);
const detailVisible = ref(false);

async function loadAnnouncements() {
  annLoading.value = true;
  try {
    const data = await announcementApi.list();
    announcements.value = data?.items || data?.data?.items || [];
    maybeAutoOpen();
  } catch (_) { /* 静默 */ }
  finally { annLoading.value = false; }
}
onMounted(loadAnnouncements);

function maybeAutoOpen() {
  if (sessionStorage.getItem("kg-show-announcement") !== "1") return;
  sessionStorage.removeItem("kg-show-announcement");
  // 优先选最近一条置顶,否则最新一条
  const target = announcements.value.find((a) => a.pinned) || announcements.value[0];
  if (target) openDetail(target);
}

function openDetail(a) {
  detail.value = a;
  detailVisible.value = true;
}

// ===== 反馈 =====
const fbVisible = ref(false);
const fbSubmitting = ref(false);
const fbForm = reactive({
  category: "other",
  content: "",
  contact: "",
});

function openFeedback() {
  fbForm.category = "other";
  fbForm.content = "";
  fbForm.contact = auth.userInfo?.email || "";
  fbVisible.value = true;
}

async function submitFeedback() {
  if (!fbForm.content.trim()) {
    ElMessage.warning("请填写反馈内容");
    return;
  }
  fbSubmitting.value = true;
  try {
    await feedbackApi.submit({
      category: fbForm.category,
      content: fbForm.content.trim(),
      contact: fbForm.contact.trim() || null,
    });
    ElMessage.success("反馈已提交，谢谢！");
    fbVisible.value = false;
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message);
  } finally {
    fbSubmitting.value = false;
  }
}

// ===== utils =====
function fmtDate(s) {
  if (!s) return "";
  try {
    return new Date(s).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  } catch (_) { return String(s).slice(5, 10); }
}

const greeting = computed(() => {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 12) return "早上好";
  if (h < 18) return "下午好";
  return "晚上好";
});

const features = [
  {
    icon: Connection,
    title: "知识图谱驱动",
    desc: "实体、关系、别名自动抽取。在 3D 图谱中直观浏览节点关联，让知识结构可见。",
  },
  {
    icon: Aim,
    title: "证据可追溯",
    desc: "每个回答都给出 wiki 引用与来源页。结论看得见出处，避免大模型瞎编。",
  },
  {
    icon: StarFilled,
    title: "持续生长",
    desc: "问答与综合不断催生新页面，知识库自我增长，越用越完整。",
  },
];

const ctas = [
  { label: "开始问答", icon: ChatLineRound, path: "/kb/query", primary: true },
  { label: "浏览 Wiki", icon: Notebook, path: "/kb/wiki" },
  { label: "知识图谱", icon: Share, path: "/kb/wiki" },
];

const guides = [
  {
    n: "01",
    title: "用「智能问答」开始",
    body: "提问任何工业工程相关问题，系统从 wiki 检索证据并流式输出答案，附带引用。",
  },
  {
    n: "02",
    title: "用「Wiki 浏览」深读",
    body: "结构化知识页按 entity / topic / source 分类，每页含 frontmatter、正文、关联节点。",
  },
  {
    n: "03",
    title: "用「知识图谱」找联系",
    body: "3D 力导向图展示实体之间的边，可点击节点跳转 wiki，发现意料之外的关联。",
  },
  {
    n: "04",
    title: "看公告与提反馈",
    body: "重要公告会在主页置顶，使用中遇到任何问题或建议，从下方「用户反馈」告诉我们。",
  },
];
</script>
<!-- TPL -->

<template>
  <div class="home">
    <!-- ============ Hero ============ -->
    <section class="hero">
      <div class="hero-inner">
        <div class="kicker">
          <span class="kicker-dot"></span>
          <span>持续生长的工业工程知识库 · 测试阶段</span>
        </div>
        <h1 class="hero-title">
          {{ greeting }}<span v-if="auth.userInfo?.username" class="hero-name">，{{ auth.userInfo.username }}</span>
        </h1>
        <p class="hero-sub">
          基于 llm-wiki 方法论 · 知识图谱、证据引用、持续生长
        </p>
        <div class="hero-ctas">
          <button
            v-for="c in ctas"
            :key="c.path"
            class="btn"
            :class="{ 'btn-primary': c.primary, 'btn-ghost': !c.primary }"
            @click="router.push(c.path)"
          >
            <el-icon><component :is="c.icon" /></el-icon>
            <span>{{ c.label }}</span>
            <el-icon v-if="c.primary" class="btn-arrow"><ArrowRight /></el-icon>
          </button>
        </div>
      </div>
    </section>

    <!-- ============ 公告 ============ -->
    <section class="block" v-if="announcements.length || annLoading">
      <header class="block-head">
        <div class="block-title-wrap">
          <el-icon class="block-icon"><Bell /></el-icon>
          <h2 class="block-title">最新公告</h2>
        </div>
        <span class="block-meta" v-if="announcements.length">
          共 {{ announcements.length }} 条
        </span>
      </header>
      <div class="ann-grid" v-loading="annLoading">
        <article
          v-for="a in announcements"
          :key="a.id"
          class="ann-card"
          :class="{ pinned: a.pinned }"
          @click="openDetail(a)"
        >
          <div class="ann-cover-wrap">
            <img v-if="a.cover_image_url" :src="a.cover_image_url" class="ann-cover" alt="" />
            <div v-else class="ann-cover-fallback">
              <el-icon><Bell /></el-icon>
            </div>
            <span v-if="a.pinned" class="pin-badge">
              <el-icon><StarFilled /></el-icon>
              置顶
            </span>
          </div>
          <div class="ann-body">
            <h3 class="ann-title">{{ a.title }}</h3>
            <p class="ann-excerpt">{{ (a.body_md || "").replace(/[#*_>]/g, "").trim().slice(0, 90) }}</p>
            <div class="ann-foot">
              <el-icon><Calendar /></el-icon>
              <span>{{ fmtDate(a.created_at) }}</span>
              <span class="read-more">阅读详情 →</span>
            </div>
          </div>
        </article>
      </div>
    </section>

    <!-- ============ Features ============ -->
    <section class="block">
      <header class="block-head">
        <div class="block-title-wrap">
          <h2 class="block-title">为什么选择 KG-Wiki</h2>
        </div>
      </header>
      <div class="features">
        <div v-for="f in features" :key="f.title" class="feature">
          <div class="feature-icon">
            <el-icon><component :is="f.icon" /></el-icon>
          </div>
          <h3 class="feature-title">{{ f.title }}</h3>
          <p class="feature-desc">{{ f.desc }}</p>
        </div>
      </div>
    </section>

    <!-- ============ Guide ============ -->
    <section class="block">
      <header class="block-head">
        <div class="block-title-wrap">
          <h2 class="block-title">快速上手</h2>
        </div>
        <span class="block-meta">4 步开始使用</span>
      </header>
      <div class="guide">
        <div v-for="(g, i) in guides" :key="g.n" class="guide-step" :style="{ '--i': i }">
          <div class="guide-num">{{ g.n }}</div>
          <div class="guide-content">
            <h3 class="guide-title">{{ g.title }}</h3>
            <p class="guide-desc">{{ g.body }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ Feedback strip ============ -->
    <section class="feedback-strip" @click="openFeedback">
      <div class="fb-icon">
        <el-icon><ChatDotSquare /></el-icon>
      </div>
      <div class="fb-text">
        <div class="fb-title">遇到问题或有建议？</div>
        <div class="fb-sub">把它告诉我们，让 KG-Wiki 变得更好</div>
      </div>
      <button class="btn btn-primary fb-btn" @click.stop="openFeedback">
        提交反馈
        <el-icon class="btn-arrow"><ArrowRight /></el-icon>
      </button>
    </section>

    <!-- ============ 公告详情 ============ -->
    <el-dialog
      v-model="detailVisible"
      :title="detail?.title || ''"
      width="720"
      align-center
      class="ann-dialog"
    >
      <img v-if="detail?.cover_image_url" :src="detail.cover_image_url" class="dialog-cover" alt="" />
      <MarkdownViewer v-if="detail" :source="detail.body_md || ''" />
      <div class="dialog-meta" v-if="detail">
        <el-icon><Calendar /></el-icon>
        <span>{{ fmtDate(detail.created_at) }}</span>
      </div>
    </el-dialog>

    <!-- ============ 反馈 ============ -->
    <el-dialog v-model="fbVisible" title="用户反馈" width="520" align-center>
      <el-form label-position="top">
        <el-form-item label="反馈类型">
          <el-radio-group v-model="fbForm.category">
            <el-radio-button value="bug">缺陷</el-radio-button>
            <el-radio-button value="feature">新功能</el-radio-button>
            <el-radio-button value="content">内容相关</el-radio-button>
            <el-radio-button value="other">其他</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="反馈内容" required>
          <el-input
            v-model="fbForm.content"
            type="textarea"
            :rows="6"
            maxlength="2000"
            show-word-limit
            placeholder="请详细描述你遇到的问题或建议"
          />
        </el-form-item>
        <el-form-item label="联系方式（可选）">
          <el-input v-model="fbForm.contact" placeholder="邮箱、微信等，方便我们回复你" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="fbVisible = false">取消</el-button>
        <el-button type="primary" :loading="fbSubmitting" @click="submitFeedback">
          提交反馈
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>
<!-- STY -->

<style scoped>
/* ============ tokens ============ */
.home {
  --ink-900: #0f172a;
  --ink-700: #334155;
  --ink-500: #64748b;
  --ink-300: #cbd5e1;
  --line: #e2e8f0;
  --line-soft: #eef2f6;
  --surface: #ffffff;
  --bg-soft: #fafaf9;
  --warm: #f97316;
  --warm-deep: #ea580c;
  --warm-tint: #fff7ed;
  --shadow-1: 0 1px 2px rgb(15 23 42 / 0.04), 0 1px 1px rgb(15 23 42 / 0.03);
  --shadow-2: 0 12px 30px -12px rgb(15 23 42 / 0.12), 0 4px 8px -4px rgb(15 23 42 / 0.06);

  display: flex;
  flex-direction: column;
  gap: 56px;
  max-width: 1200px;
  margin: 0 auto;
  padding: 8px 4px 40px;
}

/* ============ Hero ============ */
.hero {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--line-soft);
  border-radius: 20px;
  padding: 64px 56px 72px;
  overflow: hidden;
  box-shadow: var(--shadow-1);
}
.hero::before {
  content: "";
  position: absolute;
  top: -120px; right: -120px;
  width: 360px; height: 360px;
  background: radial-gradient(circle at center, #fed7aa 0%, transparent 65%);
  opacity: 0.55;
  pointer-events: none;
}
.hero::after {
  content: "";
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--line), transparent);
}
.hero-inner { position: relative; max-width: 680px; }

.kicker {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: var(--warm-tint);
  color: var(--warm-deep);
  border-radius: 999px;
  font-size: 12.5px;
  font-weight: 500;
  letter-spacing: 0.02em;
}
.kicker-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--warm);
  box-shadow: 0 0 0 4px rgb(249 115 22 / 0.18);
  animation: pulse 2.4s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 4px rgb(249 115 22 / 0.18); }
  50% { box-shadow: 0 0 0 7px rgb(249 115 22 / 0.05); }
}

.hero-title {
  font-size: clamp(32px, 4.4vw, 46px);
  font-weight: 700;
  letter-spacing: -0.025em;
  color: var(--ink-900);
  margin: 18px 0 10px;
  line-height: 1.1;
}
.hero-name { color: var(--warm-deep); }
.hero-sub {
  font-size: 16px;
  color: var(--ink-500);
  margin: 0 0 28px;
  line-height: 1.6;
}
.hero-ctas { display: flex; flex-wrap: wrap; gap: 10px; }

/* ============ Buttons ============ */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 11px 20px;
  font-size: 14px;
  font-weight: 500;
  border-radius: 10px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: transform 0.18s ease-out, box-shadow 0.18s, background 0.18s, border-color 0.18s, color 0.18s;
  font-family: inherit;
}
.btn:active { transform: scale(0.98); }
.btn-primary {
  background: var(--ink-900);
  color: #fff;
}
.btn-primary:hover {
  background: var(--warm-deep);
  box-shadow: 0 6px 14px -4px rgb(234 88 12 / 0.4);
}
.btn-ghost {
  background: var(--surface);
  color: var(--ink-700);
  border-color: var(--line);
}
.btn-ghost:hover {
  border-color: var(--ink-300);
  color: var(--ink-900);
}
.btn-arrow { transition: transform 0.18s; }
.btn:hover .btn-arrow { transform: translateX(2px); }

/* ============ Block (section wrapper) ============ */
.block { display: flex; flex-direction: column; gap: 20px; }
.block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.block-title-wrap { display: flex; align-items: center; gap: 8px; }
.block-icon { font-size: 18px; color: var(--warm); }
.block-title {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--ink-900);
  margin: 0;
}
.block-meta { font-size: 12.5px; color: var(--ink-500); }

/* ============ Announcements ============ */
.ann-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}
.ann-card {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 14px;
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.22s ease-out, box-shadow 0.22s, border-color 0.22s;
}
.ann-card:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-2);
  border-color: #fdba74;
}
.ann-cover-wrap {
  position: relative;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  background: linear-gradient(135deg, #fff7ed, #ffedd5);
}
.ann-cover {
  width: 100%; height: 100%;
  object-fit: cover;
  transition: transform 0.4s ease-out;
}
.ann-card:hover .ann-cover { transform: scale(1.04); }
.ann-cover-fallback {
  width: 100%; height: 100%;
  display: flex; align-items: center; justify-content: center;
  font-size: 38px;
  color: var(--warm);
  opacity: 0.45;
}
.pin-badge {
  position: absolute;
  top: 10px; left: 10px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  background: rgba(15, 23, 42, 0.85);
  color: #fed7aa;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  backdrop-filter: blur(4px);
}
.pin-badge .el-icon { font-size: 11px; }
.ann-body {
  padding: 16px 18px 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
}
.ann-title {
  font-size: 15.5px;
  font-weight: 600;
  margin: 0;
  color: var(--ink-900);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.ann-excerpt {
  font-size: 13px;
  color: var(--ink-500);
  margin: 0;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.ann-foot {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  color: var(--ink-500);
  margin-top: auto;
  padding-top: 8px;
}
.ann-foot .read-more {
  margin-left: auto;
  color: var(--warm-deep);
  font-weight: 500;
  opacity: 0;
  transform: translateX(-4px);
  transition: all 0.22s;
}
.ann-card:hover .read-more { opacity: 1; transform: translateX(0); }

/* ============ Features ============ */
.features {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}
.feature {
  padding: 28px 24px;
  background: var(--surface);
  border: 1px solid var(--line-soft);
  border-radius: 14px;
  transition: transform 0.22s, box-shadow 0.22s, border-color 0.22s;
}
.feature:hover {
  transform: translateY(-2px);
  border-color: var(--line);
  box-shadow: var(--shadow-1);
}
.feature-icon {
  width: 42px; height: 42px;
  display: flex; align-items: center; justify-content: center;
  background: var(--warm-tint);
  color: var(--warm-deep);
  border-radius: 10px;
  font-size: 20px;
  margin-bottom: 16px;
}
.feature-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--ink-900);
  margin: 0 0 8px;
}
.feature-desc {
  font-size: 13.5px;
  color: var(--ink-500);
  margin: 0;
  line-height: 1.7;
}

/* ============ Guide ============ */
.guide {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
}
.guide-step {
  display: flex;
  gap: 16px;
  padding: 22px 22px 22px 18px;
  background: var(--surface);
  border: 1px solid var(--line-soft);
  border-radius: 12px;
  transition: border-color 0.22s, transform 0.22s;
}
.guide-step:hover {
  border-color: #fdba74;
  transform: translateX(2px);
}
.guide-num {
  font-size: 22px;
  font-weight: 700;
  font-feature-settings: "tnum";
  color: var(--warm);
  font-style: italic;
  flex-shrink: 0;
  width: 36px;
  letter-spacing: -0.02em;
}
.guide-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink-900);
  margin: 2px 0 6px;
}
.guide-desc {
  font-size: 13px;
  color: var(--ink-500);
  margin: 0;
  line-height: 1.65;
}

/* ============ Feedback strip ============ */
.feedback-strip {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 22px 28px;
  background: linear-gradient(135deg, #1f2937 0%, #0f172a 100%);
  border-radius: 16px;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: box-shadow 0.22s;
}
.feedback-strip::before {
  content: "";
  position: absolute;
  top: -40px; right: -40px;
  width: 200px; height: 200px;
  background: radial-gradient(circle, rgb(249 115 22 / 0.5) 0%, transparent 70%);
  pointer-events: none;
}
.feedback-strip:hover {
  box-shadow: 0 14px 40px -8px rgb(15 23 42 / 0.4);
}
.fb-icon {
  width: 48px; height: 48px;
  display: flex; align-items: center; justify-content: center;
  background: rgb(249 115 22 / 0.18);
  color: #fed7aa;
  border-radius: 12px;
  font-size: 22px;
  flex-shrink: 0;
}
.fb-text { flex: 1; min-width: 0; position: relative; }
.fb-title {
  font-size: 16px;
  font-weight: 600;
  color: #fff;
  letter-spacing: -0.01em;
}
.fb-sub {
  font-size: 13px;
  color: #94a3b8;
  margin-top: 3px;
}
.fb-btn { position: relative; }

/* ============ Dialogs ============ */
.dialog-cover {
  width: 100%;
  max-height: 320px;
  object-fit: cover;
  border-radius: 10px;
  margin-bottom: 18px;
}
.dialog-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
  font-size: 12px;
  color: var(--ink-500);
  justify-content: flex-end;
}

/* ============ Responsive ============ */
@media (max-width: 768px) {
  .home { gap: 40px; padding: 4px 2px 32px; }
  .hero { padding: 40px 24px 48px; border-radius: 14px; }
  .hero-title { font-size: 28px; }
  .hero-sub { font-size: 14px; }
  .features { grid-template-columns: 1fr; }
  .guide { grid-template-columns: 1fr; }
  .ann-grid { grid-template-columns: 1fr; }
  .feedback-strip {
    flex-direction: column;
    align-items: flex-start;
    padding: 22px;
    gap: 14px;
  }
  .feedback-strip .btn { width: 100%; justify-content: center; }
  .block-title { font-size: 19px; }
}
</style>



