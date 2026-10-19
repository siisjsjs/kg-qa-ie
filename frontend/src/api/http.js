import axios from "axios";
import { ElMessage } from "element-plus";

const ACCESS_KEY = "kg_access_token";
const REFRESH_KEY = "kg_refresh_token";

export const tokenStore = {
  getAccess: () => localStorage.getItem(ACCESS_KEY) || "",
  getRefresh: () => localStorage.getItem(REFRESH_KEY) || "",
  set(access, refresh) {
    if (access) localStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

const http = axios.create({
  baseURL: "/api/v1",
  timeout: 120000,
});

http.interceptors.request.use((config) => {
  const token = tokenStore.getAccess();
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let refreshing = null;

async function performRefresh() {
  const refreshToken = tokenStore.getRefresh();
  if (!refreshToken) throw new Error("missing refresh token");
  const resp = await axios.post(
    "/api/v1/auth/refresh",
    { refresh_token: refreshToken },
    { timeout: 30000 }
  );
  const data = resp.data?.data || resp.data;
  const access = data?.access_token;
  const newRefresh = data?.refresh_token;
  if (!access) throw new Error("refresh response missing access_token");
  tokenStore.set(access, newRefresh || refreshToken);
  return access;
}

function onAuthFailed() {
  tokenStore.clear();
  const onAuthPages = ["/", "/login", "/register"];
  if (!onAuthPages.includes(location.pathname)) {
    location.href = "/";
  }
}

http.interceptors.response.use(
  (resp) => resp,
  async (err) => {
    const { config, response } = err;
    if (!response) {
      if (!config?.skipErrorNotify) {
        ElMessage.error("网络错误：" + (err.message || "请求失败"));
      }
      return Promise.reject(err);
    }
    if (response.status === 401 && !config._retry) {
      if (config.url?.includes("/auth/refresh") || config.url?.includes("/auth/login")) {
        onAuthFailed();
        return Promise.reject(err);
      }
      config._retry = true;
      try {
        refreshing ||= performRefresh().finally(() => {
          refreshing = null;
        });
        const access = await refreshing;
        config.headers = config.headers || {};
        config.headers.Authorization = `Bearer ${access}`;
        return http(config);
      } catch (e) {
        onAuthFailed();
        return Promise.reject(err);
      }
    }
    if (!config?.skipErrorNotify) {
      const msg =
        response.data?.detail ||
        response.data?.message ||
        response.data?.error ||
        `请求失败 (${response.status})`;
      ElMessage.error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return Promise.reject(err);
  }
);

export default http;
