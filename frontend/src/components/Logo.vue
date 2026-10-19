<template>
  <div class="app-logo" :class="{ 'is-collapsed': collapsed }">
    <svg width="30" height="30" viewBox="0 0 32 32" fill="none">
      <path d="M16 2L4 8V24L16 30L28 24V8L16 2Z" stroke="#F97316" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M16 12V22" stroke="#F97316" stroke-width="2" stroke-linecap="round" />
      <path d="M11 15L16 12L21 15" stroke="#F97316" stroke-width="2" stroke-linecap="round" />
      <circle cx="16" cy="12" r="2" fill="#F97316" />
      <circle cx="11" cy="15" r="2" fill="#F97316" />
      <circle cx="21" cy="15" r="2" fill="#F97316" />
      <circle cx="16" cy="22" r="2" fill="#F97316" />
    </svg>
    <span v-if="!collapsed" class="logo-text">KG-Wiki</span>
  </div>
</template>

<script setup>
defineProps({
  collapsed: { type: Boolean, default: false },
});
</script>

<style scoped>
.app-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  font-size: 17px;
  color: #0f172a;
  user-select: none;
}
.logo-text {
  white-space: nowrap;
  letter-spacing: -0.01em;
}
.is-collapsed .logo-text {
  display: none;
}
</style>
