"""wiki/fs_io.py：原子读写、slug 命名、安全路径检查。

`safe_join`:防止 ../ 越界。
`slugify`:中文保留 + 去特殊字符,生成稳定文件名。
`atomic_write`:tmp + rename 原子写,避免半截文件。
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from slugify import slugify as _ascii_slugify

# 文件名禁用字符(Windows + POSIX 并集) + 控制字符
_INVALID_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WHITESPACE = re.compile(r"\s+")
_REPEATED_DASH = re.compile(r"-{2,}")

# Unicode 破折号/连字符变体 → 统一折叠成 ASCII '-'。
# NFC 归一不会折叠这些字符,导致正文 [[柯布‑道格拉斯…]](U+2011 非断行连字符)
# 与文件名/slug 里的 ASCII '-'(U+002D)比对不相等 → 被判虚空 → void-fill 又
# 建一份 U+2011 占位页 → 同一概念裂成两个节点(文件名==slug 检查照样过)。
# 在 slug 归一与正文 link 解析两侧都折叠,使两侧收敛到 ASCII '-'。
# 注意:不含 U+30FC(日文长音符,非破折号)。
_DASH_VARIANTS = "‐‑‒–—―−⁃﹘﹣－"
_DASH_FOLD = {ord(c): "-" for c in _DASH_VARIANTS}


def fold_dashes(s: str) -> str:
    """把各种 Unicode 破折号/连字符变体折叠成 ASCII '-'(U+002D)。"""
    return str(s).translate(_DASH_FOLD)


def safe_join(root: Path, *parts: str) -> Path:
    """拼路径并校验不越出 root。

    任何 part 是绝对路径、或拼接结果 .resolve() 后不在 root 下,都抛 ValueError。
    """
    root_resolved = Path(root).resolve()
    for p in parts:
        if Path(p).is_absolute():
            raise ValueError(f"absolute path not allowed in safe_join: {p!r}")

    target = root_resolved.joinpath(*parts).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as e:
        raise ValueError(f"path escapes root: {target} not under {root_resolved}") from e
    return target


def slugify(title: str, *, max_len: int = 80) -> str:
    """生成 wiki 文件名安全的 slug。

    规则:
    - NFC 归一化
    - 去掉 \\x00-\\x1f 与 < > : " / \\ | ? *
    - 空白折叠为单个 `-`
    - 中文保留,英文/数字保留,其他符号尽量保留(除上述禁用字符)
    - 全 ASCII 标题降级到 python-slugify(对纯 ASCII 题目更友好)
    - 最大长度截断到 max_len(以字符计)
    """
    if not title or not title.strip():
        raise ValueError("slugify: title is empty")

    s = unicodedata.normalize("NFC", title).strip()
    s = fold_dashes(s)
    s = _INVALID_CHARS.sub("", s)
    s = _WHITESPACE.sub("-", s)
    s = _REPEATED_DASH.sub("-", s).strip("-")

    if not s:
        raise ValueError(f"slugify: title produced empty slug: {title!r}")

    # 纯 ASCII 走 python-slugify,得到更规范的 dash 风格
    if s.isascii():
        ascii_slug = _ascii_slugify(s, max_length=max_len, lowercase=False)
        if ascii_slug:
            return ascii_slug

    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s


def safe_slug(slug: str, *, max_len: int = 120) -> str:
    """把"已经是 slug"的字符串清成文件系统安全 stem(Windows + POSIX)。

    与 `slugify` 的区别:slugify 面向"标题→slug"(会折叠空白成 -、纯 ASCII 走
    python-slugify、空标题抛错);本函数面向"slug→安全 slug",做最小变换以尽量
    保留可读中文 slug,使正文 [[X]] 经同一变换后仍与文件名一致。

    变换:NFC 归一 → 删除 \\x00-\\x1f 与 < > : " / \\ | ? * → 去结尾的点/空格
    (Windows 禁止)→ 截断到 max_len。永不抛错,空结果回退 "unnamed"。
    """
    s = unicodedata.normalize("NFC", str(slug)).strip()
    s = fold_dashes(s)
    s = _INVALID_CHARS.sub("", s)
    s = s.rstrip(". ")
    if len(s) > max_len:
        s = s[:max_len].rstrip(". -")
    return s or "unnamed"


async def atomic_write(path: Path, content: str) -> None:
    """原子写:写到 tmp 同目录下再 rename 到目标。

    rename 在同一文件系统上是原子操作。父目录不存在时自动创建。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


async def read_text(path: Path) -> str:
    """读 UTF-8 文本;文件不存在抛 FileNotFoundError。"""
    return Path(path).read_text(encoding="utf-8")
