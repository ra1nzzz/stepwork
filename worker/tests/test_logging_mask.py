

def test_json_shaped_secrets_are_masked() -> None:
    """结构化日志里的密钥是 JSON 形态：``"apiKey": "sk-..."``。

    此前正则要求关键字后**紧跟** ``:``，中间的引号会让匹配失败 ——
    于是所有 JSON 形式的密钥都掩不掉，而结构化日志恰恰全是 JSON。
    """
    from worker.runtime.logging_config import mask_secrets

    for raw, leaked in [
        ('{"apiKey": "sk-live-SECRET-1234"}', "sk-live-SECRET-1234"),
        ('{"token":"t-9-secret"}', "t-9-secret"),
        ("{'password': 'p@ss'}", "p@ss"),
        ('{"session_id": "sess-abc"}', "sess-abc"),
    ]:
        assert leaked not in mask_secrets(raw), raw


def test_masking_does_not_hit_ordinary_words() -> None:
    """关键字必须后跟分隔符才触发，否则正常叙述会被打成马赛克。"""
    from worker.runtime.logging_config import mask_secrets

    for benign in ("monkey pattern 正常文本", "the token bucket algorithm", "keyboard"):
        assert mask_secrets(benign) == benign
