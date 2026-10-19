<script setup>
import { ref, onMounted, onBeforeUnmount } from "vue";

const props = defineProps({
  density: { type: Number, default: 0.00009 }, // 节点数 = density * area
  linkDistance: { type: Number, default: 150 },
  nodeColor: { type: String, default: "#0f172a" },
  edgeColor: { type: String, default: "15, 23, 42" }, // rgb
  speed: { type: Number, default: 0.18 },
});

const canvas = ref(null);
let ctx = null;
let raf = 0;
let nodes = [];
let dpr = 1;
let width = 0;
let height = 0;
let visible = true;
let reduced = false;

function seedNodes() {
  const count = Math.max(28, Math.round(width * height * props.density));
  nodes = Array.from({ length: count }, () => ({
    x: Math.random() * width,
    y: Math.random() * height,
    vx: (Math.random() - 0.5) * props.speed,
    vy: (Math.random() - 0.5) * props.speed,
    r: 1.6 + Math.random() * 2.2,
  }));
}

function resize() {
  const el = canvas.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  dpr = Math.min(window.devicePixelRatio || 1, 2);
  width = rect.width;
  height = rect.height;
  el.width = Math.round(width * dpr);
  el.height = Math.round(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  seedNodes();
}

function step() {
  raf = 0;
  if (!ctx || !visible) return;
  ctx.clearRect(0, 0, width, height);

  for (const n of nodes) {
    n.x += n.vx;
    n.y += n.vy;
    if (n.x < -10) n.x = width + 10;
    else if (n.x > width + 10) n.x = -10;
    if (n.y < -10) n.y = height + 10;
    else if (n.y > height + 10) n.y = -10;
  }

  const D = props.linkDistance;
  ctx.lineWidth = 0.6;
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < D) {
        const alpha = (1 - dist / D) * 0.35;
        ctx.strokeStyle = `rgba(${props.edgeColor}, ${alpha.toFixed(3)})`;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
  }

  for (const n of nodes) {
    ctx.fillStyle = `rgba(${props.edgeColor}, 0.12)`;
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.r * 2.6, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = props.nodeColor;
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
    ctx.fill();
  }

  if (!reduced) raf = requestAnimationFrame(step);
}

function onVisibility() {
  visible = !document.hidden;
  if (visible && !reduced && !raf) step();
}

onMounted(() => {
  ctx = canvas.value.getContext("2d");
  reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  resize();
  step();
  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", onVisibility);
});

onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf);
  window.removeEventListener("resize", resize);
  document.removeEventListener("visibilitychange", onVisibility);
});
</script>

<template>
  <canvas ref="canvas" class="graph-bg" aria-hidden="true" />
</template>

<style scoped>
.graph-bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  pointer-events: none;
}
</style>
