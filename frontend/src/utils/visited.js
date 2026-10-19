// 浏览足迹:记录用户打开过的 wiki 页 slug(仅存本机 localStorage,供学习路线图显示进度)
const KEY = "kg_visited_wiki";
const CAP = 3000;

export function getVisitedSet() {
  try {
    const arr = JSON.parse(localStorage.getItem(KEY) || "[]");
    return new Set(Array.isArray(arr) ? arr : []);
  } catch (_) {
    return new Set();
  }
}

export function recordVisit(slug) {
  if (!slug) return;
  try {
    const set = getVisitedSet();
    if (set.has(slug)) return;
    set.add(slug);
    let arr = [...set];
    if (arr.length > CAP) arr = arr.slice(arr.length - CAP);
    localStorage.setItem(KEY, JSON.stringify(arr));
  } catch (_) {
    /* localStorage 满/禁用时静默放弃 */
  }
}
