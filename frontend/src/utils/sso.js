// 绘图平台 SSO 跳转(从 Portal.vue 抽出共享;Query.vue 模板面板也要用)。
// 用隐藏表单 POST 携带 token,避免 token 出现在 URL / 日志 / 历史记录里。
import http, { tokenStore } from "@/api/http";

export const DEFAULT_ZHIHUITU_BASE = "https://draw.example.com";

/**
 * 打开绘图平台(新标签)。
 * @param {string} redirect 平台内目标路径(如 /templates、/mode-select/{id})
 * @param {string} baseUrl 平台地址(chart_templates 事件携带,不硬编码)
 * @returns {Promise<boolean>} false = 未登录,调用方自行跳登录页
 */
export async function openDrawingPlatform(redirect = "/templates", baseUrl = "") {
  try {
    await http.get("/auth/me"); // 触发一次静默续期,确保 token 新鲜
  } catch (_) {
    /* 401 已由拦截器自动续期或跳转处理 */
  }
  const token = tokenStore.getAccess();
  if (!token) return false;
  const base = (baseUrl || DEFAULT_ZHIHUITU_BASE).replace(/\/+$/, "");
  const form = document.createElement("form");
  form.method = "POST";
  form.action = `${base}/api/v1/auth/sso`;
  form.target = "_blank";
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = "token";
  input.value = token;
  form.appendChild(input);
  const redirectInput = document.createElement("input");
  redirectInput.type = "hidden";
  redirectInput.name = "redirect";
  redirectInput.value = redirect || "/templates";
  form.appendChild(redirectInput);
  document.body.appendChild(form);
  form.submit();
  form.remove();
  return true;
}
