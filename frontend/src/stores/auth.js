import { defineStore } from "pinia";
import { authApi } from "@/api";
import { tokenStore } from "@/api/http";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    accessToken: tokenStore.getAccess(),
    refreshToken: tokenStore.getRefresh(),
    userInfo: null,
    permissions: [],
    bootstrapped: false,
  }),
  getters: {
    isLoggedIn: (s) => !!s.accessToken && !!s.userInfo,
    isAdmin: (s) => s.userInfo?.role === "admin",
  },
  actions: {
    setTokens(access, refresh) {
      this.accessToken = access || "";
      this.refreshToken = refresh || this.refreshToken || "";
      tokenStore.set(this.accessToken, this.refreshToken);
    },
    async login(payload) {
      const data = await authApi.login(payload);
      const body = data?.data || data;
      this.setTokens(body.access_token, body.refresh_token);
      await this.fetchProfile();
      return { success: true };
    },
    async register(payload) {
      const data = await authApi.register(payload);
      return data?.data || data;
    },
    async refresh() {
      if (!this.refreshToken) throw new Error("no refresh token");
      const data = await authApi.refresh(this.refreshToken);
      const body = data?.data || data;
      this.setTokens(body.access_token, body.refresh_token);
      return body.access_token;
    },
    async fetchProfile() {
      try {
        const data = await authApi.me();
        const body = data?.data || data;
        this.userInfo = body?.user || body;
        this.permissions = body?.permissions || [];
      } catch (e) {
        this.userInfo = null;
        this.permissions = [];
        throw e;
      }
    },
    async bootstrap() {
      this.bootstrapped = true;
      if (this.accessToken) {
        try {
          await this.fetchProfile();
        } catch (_) {
          this.logout();
        }
      }
    },
    async logout() {
      try { await authApi.logout(); } catch (_) {}
      this.accessToken = "";
      this.refreshToken = "";
      this.userInfo = null;
      this.permissions = [];
      tokenStore.clear();
      sessionStorage.removeItem("kg-show-announcement");
    },
  },
});
