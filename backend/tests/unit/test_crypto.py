"""crypto 加解密往返 + mask。"""

from app.core import crypto


def test_encrypt_decrypt_roundtrip():
    plain = "sk-1234567890abcdef"
    enc = crypto.encrypt_value(plain)
    assert enc != plain
    assert crypto.decrypt_value(enc) == plain


def test_encrypt_empty_string_passthrough():
    assert crypto.encrypt_value("") == ""
    assert crypto.decrypt_value("") == ""


def test_mask_secret():
    assert crypto.mask_secret("sk-abcd1234") == "sk-a*******"
    assert crypto.mask_secret("abc") == "***"
    assert crypto.mask_secret("") == ""
