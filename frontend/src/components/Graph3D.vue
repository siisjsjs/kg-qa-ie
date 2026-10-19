<script setup>
import { onBeforeUnmount, onMounted, ref, watch, computed } from "vue";
import ForceGraph3D from "3d-force-graph";
import * as THREE from "three";
import SpriteText from "three-spritetext";
import { useGraphStore } from "@/stores/graph";
import { kindColor, kindLabel, KIND_RING, SURFACE, HIGHLIGHT } from "@/constants/graphTheme";

const emit = defineEmits(["node-click", "fps"]);
const container = ref(null);
const store = useGraphStore();

let graph = null;
let ro = null;
let starField = null;
let frameTimer = null;
let frameCount = 0;
let lastFpsTs = 0;
let autoOrbit = true;
let orbitAngle = 0;
let lastInteractTs = 0;
let prefersReducedMotion = false;

// 用于复用 halo 纹理
let haloTextureCache = new Map();

const surface = computed(() => SURFACE[store.theme]);
const highlight = computed(() => HIGHLIGHT[store.theme]);
const isDark = computed(() => store.theme === "dark");

function buildGraphData() {
  const { nodes, edges } = store.filteredGraph;
  return {
    nodes: nodes.map((n) => ({ ...n })),
    links: edges.map((e) => ({ ...e })),
  };
}

function nodeRadius(node) {
  const d = node.degree || 0;
  return 1.5 + Math.log2(d + 1) * 1.2;
}

// 三层球壳半径:素材排成小球壳,主题/实体逐层外扩。
// 外层半径由该层节点数反推(球面积 ≥ 节点数 × 单节点占位),节点多则壳更大。
function ringRadii() {
  const nodes = store.filteredGraph.nodes;
  const counts = [0, 0, 0];
  for (const n of nodes) counts[KIND_RING[n.kind] ?? 2]++;
  const cell = 28;           // 每节点在球面占用的"边长"
  const gap = 110;           // 相邻壳层最小间距
  // 4πr² ≥ c*cell²  →  r ≥ cell*sqrt(c/(4π))
  const byCount = (c) => cell * Math.sqrt(c / (4 * Math.PI));
  const r0 = Math.max(40, byCount(counts[0]));
  const r1 = Math.max(r0 + gap, byCount(counts[1]));
  const r2 = Math.max(r1 + gap, byCount(counts[2]));
  return [r0, r1, r2];
}

function getHaloTexture(colorHex) {
  if (haloTextureCache.has(colorHex)) return haloTextureCache.get(colorHex);
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size; canvas.height = size;
  const ctx = canvas.getContext("2d");
  const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  gradient.addColorStop(0, hexToRgba(colorHex, 0.85));
  gradient.addColorStop(0.4, hexToRgba(colorHex, 0.35));
  gradient.addColorStop(1, hexToRgba(colorHex, 0));
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.needsUpdate = true;
  haloTextureCache.set(colorHex, tex);
  return tex;
}

function hexToRgba(hex, a) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || "#888");
  if (!m) return `rgba(136,136,136,${a})`;
  return `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${a})`;
}

function buildNodeObject(node) {
  const r = nodeRadius(node);
  const c = kindColor(node.kind || "entity", store.theme);
  const inPath = store.pathNodes.includes(node.id);
  const isSel = store.selectedNodeId === node.id;

  const group = new THREE.Group();

  // 核心球
  const sphereMat = new THREE.MeshBasicMaterial({
    color: new THREE.Color(c),
    transparent: true,
    opacity: 0.92,
  });
  const sphere = new THREE.Mesh(new THREE.SphereGeometry(r, 16, 16), sphereMat);
  group.add(sphere);

  // halo sprite
  const haloMat = new THREE.SpriteMaterial({
    map: getHaloTexture(c),
    color: 0xffffff,
    transparent: true,
    opacity: 0.85,
    depthWrite: false,
    blending: isDark.value ? THREE.AdditiveBlending : THREE.NormalBlending,
  });
  const halo = new THREE.Sprite(haloMat);
  const haloScale = r * (inPath ? 6.5 : isSel ? 5.5 : 4);
  halo.scale.set(haloScale, haloScale, 1);
  group.add(halo);

  // 高度数节点 → 加内核小亮点
  if ((node.degree || 0) >= 5) {
    const coreMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(isDark.value ? "#ffffff" : c),
      transparent: true,
      opacity: 0.6,
    });
    const core = new THREE.Mesh(new THREE.SphereGeometry(r * 0.4, 8, 8), coreMat);
    group.add(core);
  }

  // 路径环
  if (inPath || isSel) {
    const ringGeom = new THREE.RingGeometry(r * 1.6, r * 1.8, 32);
    const ringMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(inPath ? highlight.value.node : surface.value.label),
      transparent: true,
      opacity: 0.85,
      side: THREE.DoubleSide,
    });
    const ring = new THREE.Mesh(ringGeom, ringMat);
    ring.lookAt(new THREE.Vector3(0, 0, 1));
    group.add(ring);
  }

  // 标签
  if (store.showLabels && (node.degree || 0) >= 1) {
    const label = new SpriteText(`[${kindLabel(node.kind || "entity")}] ${node.title || node.id}`);
    label.color = surface.value.label;
    label.backgroundColor = false;
    label.textHeight = Math.max(2.5, r * 0.6);
    label.material.depthWrite = false;
    label.position.set(0, -(r + 2), 0);
    group.add(label);
  }

  return group;
}

function buildStarField() {
  if (!isDark.value) return null;
  const starCount = 1500;
  const positions = new Float32Array(starCount * 3);
  for (let i = 0; i < starCount; i++) {
    // 球壳分布,半径 800-1500
    const r = 800 + Math.random() * 700;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = r * Math.cos(phi);
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const mat = new THREE.PointsMaterial({
    color: 0xffffff,
    size: 1.2,
    transparent: true,
    opacity: 0.6,
    sizeAttenuation: true,
  });
  return new THREE.Points(geom, mat);
}

function build() {
  if (!container.value || graph) return;
  prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const el = container.value;
  graph = ForceGraph3D()(el)
    .backgroundColor(surface.value.bg)
    .graphData(buildGraphData())
    .nodeId("id")
    .nodeRelSize(1)
    .nodeThreeObject(buildNodeObject)
    .nodeThreeObjectExtend(false)
    .linkSource("source")
    .linkTarget("target")
    .linkColor((l) => linkColorFor(l))
    .linkOpacity(isDark.value ? 0.55 : 0.45)
    .linkWidth((l) => (isPathLink(l) ? 1.4 : 0.5))
    .linkDirectionalParticles((l) => (isPathLink(l) ? 4 : 0))
    .linkDirectionalParticleSpeed(0.006)
    .linkDirectionalParticleWidth(2)
    .linkDirectionalParticleColor(() => highlight.value.link)
    .onNodeClick((node) => {
      lastInteractTs = performance.now();
      emit("node-click", node);
    })
    .onNodeDragEnd((node) => {
      node.fx = node.x; node.fy = node.y; node.fz = node.z;
    })
    .cooldownTicks(150);

  // 中心放射(3D 球壳):素材小球壳居中,主题中层,实体外层
  import("d3-force-3d").then(({ forceRadial }) => {
    if (!graph) return;
    const radii = ringRadii();
    graph.d3Force(
      "radial",
      forceRadial((n) => radii[KIND_RING[n.kind] ?? 2], 0, 0, 0).strength(0.9)
    );
    const center = graph.d3Force("center");
    if (center) center.strength(0);
    // charge 够强才能把同层节点沿球面推开,否则只贴半径方向挤成一团
    const charge = graph.d3Force("charge");
    if (charge) charge.strength(-480);
    const link = graph.d3Force("link");
    if (link) link.strength(0.012);
    graph.d3ReheatSimulation();
  });

  // 加星空
  const scene = graph.scene();
  if (isDark.value) {
    starField = buildStarField();
    if (starField) scene.add(starField);
  }

  // 用户交互停止自动公转
  const renderer = graph.renderer();
  renderer.domElement.addEventListener("mousedown", () => (lastInteractTs = performance.now()));
  renderer.domElement.addEventListener("wheel", () => (lastInteractTs = performance.now()));
  renderer.domElement.addEventListener("touchstart", () => (lastInteractTs = performance.now()), { passive: true });

  resize();
  window.addEventListener("resize", resize);
  ro = new ResizeObserver(() => resize());
  ro.observe(el);
  startTickLoop();
}

function isPathLink(link) {
  const path = store.pathNodes;
  if (path.length < 2) return false;
  const sId = typeof link.source === "object" ? link.source.id : link.source;
  const tId = typeof link.target === "object" ? link.target.id : link.target;
  return path.includes(sId) && path.includes(tId) &&
    Math.abs(path.indexOf(sId) - path.indexOf(tId)) === 1;
}
function linkColorFor(link) {
  if (isPathLink(link)) return highlight.value.link;
  return surface.value.link;
}

function startTickLoop() {
  let prev = performance.now();
  const tick = (now) => {
    frameCount++;
    if (now - lastFpsTs > 1000) {
      emit("fps", frameCount);
      frameCount = 0;
      lastFpsTs = now;
    }
    // 自动公转(用户 3 秒未交互)
    if (autoOrbit && !prefersReducedMotion && now - lastInteractTs > 3000 && graph) {
      orbitAngle += 0.0008;
      const distance = 380;
      try {
        graph.cameraPosition({
          x: Math.sin(orbitAngle) * distance,
          z: Math.cos(orbitAngle) * distance,
        });
      } catch (_) { /* ignore */ }
    }
    prev = now;
    frameTimer = requestAnimationFrame(tick);
  };
  lastFpsTs = performance.now();
  lastInteractTs = performance.now();
  frameTimer = requestAnimationFrame(tick);
}

function resize() {
  if (!graph || !container.value) return;
  const w = container.value.clientWidth;
  const h = container.value.clientHeight;
  if (!w || !h) return;
  graph.width(w);
  graph.height(h);
}

function rebuildAll() {
  if (!graph) return;
  // 清掉 halo 纹理缓存(主题切换会变色)
  haloTextureCache.forEach((t) => t.dispose());
  haloTextureCache.clear();
  // 触发节点重建
  graph.backgroundColor(surface.value.bg);
  graph.linkOpacity(isDark.value ? 0.55 : 0.45);
  graph.linkColor((l) => linkColorFor(l));
  graph.nodeThreeObject(buildNodeObject);
  // 星空
  const scene = graph.scene();
  if (starField) {
    scene.remove(starField);
    starField.geometry.dispose();
    starField.material.dispose();
    starField = null;
  }
  if (isDark.value) {
    starField = buildStarField();
    if (starField) scene.add(starField);
  }
}

watch(
  () => store.filteredGraph,
  () => {
    if (!graph) return;
    graph.graphData(buildGraphData());
  },
  { deep: true }
);

watch(
  () => [store.theme, store.showLabels, store.pathNodes, store.selectedNodeId],
  () => rebuildAll()
);

defineExpose({
  focus(id) {
    if (!graph) return;
    const node = graph.graphData().nodes.find((n) => n.id === id);
    if (!node) return;
    lastInteractTs = performance.now();
    const distance = 80;
    const dist = Math.hypot(node.x || 1, node.y || 1, node.z || 1) || 1;
    const distRatio = 1 + distance / dist;
    graph.cameraPosition(
      { x: (node.x || 0) * distRatio, y: (node.y || 0) * distRatio, z: (node.z || 0) * distRatio },
      node,
      1500
    );
  },
});

onMounted(build);
onBeforeUnmount(() => {
  if (frameTimer) cancelAnimationFrame(frameTimer);
  window.removeEventListener("resize", resize);
  if (ro) { ro.disconnect(); ro = null; }
  haloTextureCache.forEach((t) => t.dispose());
  haloTextureCache.clear();
  if (starField) {
    starField.geometry.dispose();
    starField.material.dispose();
  }
  if (graph) graph._destructor?.();
  graph = null;
});
</script>

<template>
  <div ref="container" class="graph-3d-container" />
</template>

<style scoped>
.graph-3d-container {
  width: 100%;
  height: 100%;
}
</style>
