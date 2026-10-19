<script setup>
const props = defineProps({
  payload: { type: Object, default: () => ({ nodes: [], edges: [] }) },
});
const emit = defineEmits(["focus-node"]);
</script>

<template>
  <div class="path-highlight" v-if="payload?.nodes?.length">
    <div class="ph-title">本次问答涉及节点</div>
    <div class="ph-tags">
      <el-tag
        v-for="n in payload.nodes"
        :key="n"
        type="warning"
        effect="plain"
        size="small"
        class="ph-tag"
        @click="emit('focus-node', n)"
      >
        {{ n }}
      </el-tag>
    </div>
  </div>
</template>

<style scoped>
.path-highlight {
  padding: 10px 12px;
  background: #fff7ed;
  border-radius: 6px;
  border: 1px solid #fed7aa;
}
.ph-title {
  font-size: 12px;
  color: var(--text-light);
  margin-bottom: 6px;
}
.ph-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.ph-tag { cursor: pointer; }
</style>
