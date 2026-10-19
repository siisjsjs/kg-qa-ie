"""privacy_check.py 单元测试。"""

from app.services.ingestion.privacy_check import scan


def test_scan_clean_text():
    out = scan("这是一段正常的工业工程笔记。")
    assert out["ok"] is True
    assert out["hits"] == []
    assert out["block_count"] == 0
    assert out["warn_count"] == 0


def test_scan_phone_number_warns():
    out = scan("作者联系方式 13812345678 欢迎讨论。")
    assert any(h["category"] == "phone" for h in out["hits"])
    assert out["warn_count"] >= 1
    # 手机号是 warn,不阻塞
    assert out["block_count"] == 0
    assert out["ok"] is True


def test_scan_id_card_blocks():
    """合法 18 位身份证号 → block。"""
    # 110101199001011234 → 110101 + 1990 + 01 + 01 + 123 + 4
    out = scan("身份证 110101199001011234 已脱敏")
    assert any(h["category"] == "id_card" for h in out["hits"])
    assert out["block_count"] >= 1
    assert out["ok"] is False


def test_scan_api_key_blocks():
    out = scan("我的 key 是 sk-test-placeholder")
    assert any(h["category"] == "api_key" for h in out["hits"])
    assert out["block_count"] >= 1
    assert out["ok"] is False


def test_scan_email_warns():
    out = scan("联系邮箱:foo@example.com")
    assert any(h["category"] == "email" for h in out["hits"])
    assert out["warn_count"] >= 1
    assert out["ok"] is True


def test_scan_url_token_blocks():
    out = scan("文档链接:https://api.example.com/v1/x?access_token=abcd1234efgh")
    assert any(h["category"] == "url_token" for h in out["hits"])
    assert out["block_count"] >= 1


def test_scan_snippet_is_masked():
    """命中片段应做掩码,不回显原值。"""
    out = scan("13812345678")
    snippet = out["hits"][0]["snippet"]
    assert "*" in snippet
    assert "13812345678" not in snippet


def test_scan_returns_line_number():
    text = "无敏感\n手机号 13812345678 在第 2 行\n第 3 行"
    out = scan(text)
    phone_hit = next(h for h in out["hits"] if h["category"] == "phone")
    assert phone_hit["line"] == 2


def test_scan_empty_input():
    out = scan("")
    assert out["ok"] is True
    assert out["hits"] == []
