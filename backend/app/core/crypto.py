"""API key 等敏感配置的对称加密（Fernet）。

策略：
  1. 如果 APP_CONFIG_ENCRYPTION_KEY 设了有效 fernet key → 用它
  2. 否则尝试读 ${CACHE_DIR}/.fernet.key
  3. 都没有 → 自动生成一份并写到 ${CACHE_DIR}/.fernet.key（dev 友好）

这样即使没配 .env，加密-解密往返也能正常工作。
生产部署应显式设 APP_CONFIG_ENCRYPTION_KEY 并妥善保管。
"""

from __future__ import annotations

import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_lock = threading.RLock()
_fernet: Fernet | None = None


def _key_path() -> Path:
    cache_dir = Path(settings.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / ".fernet.key"


def _load_or_create_key() -> bytes:
    # 优先用环境变量
    env_key = settings.app_config_encryption_key.strip()
    if env_key:
        try:
            Fernet(env_key.encode("utf-8"))  # 验证
            return env_key.encode("utf-8")
        except (ValueError, InvalidToken):
            pass  # 落到文件方案

    # 文件方案
    path = _key_path()
    if path.exists():
        data = path.read_bytes().strip()
        try:
            Fernet(data)
            return data
        except (ValueError, InvalidToken):
            pass

    # 都没有 → 生成
    key = Fernet.generate_key()
    path.write_bytes(key)
    return key


def _get_fernet() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is None:
            _fernet = Fernet(_load_or_create_key())
        return _fernet


def reset_for_tests() -> None:
    """测试用：丢弃单例，下次重新初始化。"""
    global _fernet
    with _lock:
        _fernet = None


def encrypt_value(plain: str) -> str:
    """加密敏感字符串（如 API key）。空串原样返回。"""
    if not plain:
        return plain
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_value(encrypted: str) -> str:
    """解密。空串原样返回；解密失败抛 InvalidToken。"""
    if not encrypted:
        return encrypted
    try:
        return _get_fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # 兼容场景：值原本就是明文（旧记录或手动写入）
        return encrypted


def mask_secret(value: str, *, head: int = 4) -> str:
    """前端展示时屏蔽敏感字段：保留前 head 位 + ****。"""
    if not value:
        return ""
    if len(value) <= head:
        return "*" * len(value)
    return value[:head] + "*" * (len(value) - head)
