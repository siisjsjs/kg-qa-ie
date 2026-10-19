import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import path from "node:path";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5180,
    proxy: {
      "/api": {
        // 本地预览可用 KGQA_PROXY_TARGET 指到生产后端(如 https://kb.example.com)
        // 显式用 127.0.0.1 而非 localhost:避免 Node 解析到 ::1 后连上其他占用 8000 的进程
        target: process.env.KGQA_PROXY_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
        // SSE 必须关闭压缩 / 关闭代理 buffering,确保 token 即时下发
        selfHandleResponse: false,
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            // 关掉 nginx/上游可能的 buffer 提示;并强制不压缩
            proxyRes.headers["x-accel-buffering"] = "no";
            delete proxyRes.headers["content-encoding"];
          });
        },
      },
    },
  },
});
