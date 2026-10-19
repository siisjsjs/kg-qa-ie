import { defineStore } from "pinia";

export const useUiStore = defineStore("ui", {
  state: () => ({
    // 桌面端记忆用户偏好;移动端始终默认隐藏(用 mobileDrawerOpen 控制弹出)
    sidebarCollapsed: localStorage.getItem("kg_sidebar_collapsed") === "1",
    isMobile: false,
    mobileDrawerOpen: false,
  }),
  actions: {
    toggleSidebar() {
      if (this.isMobile) {
        this.mobileDrawerOpen = !this.mobileDrawerOpen;
        return;
      }
      this.sidebarCollapsed = !this.sidebarCollapsed;
      localStorage.setItem(
        "kg_sidebar_collapsed",
        this.sidebarCollapsed ? "1" : "0"
      );
    },
    closeMobileDrawer() {
      this.mobileDrawerOpen = false;
    },
    setMobile(b) {
      const wasMobile = this.isMobile;
      this.isMobile = !!b;
      // 切换到移动端时关闭抽屉,切回桌面端时也复位
      if (wasMobile !== this.isMobile) {
        this.mobileDrawerOpen = false;
      }
    },
  },
});
