from __future__ import annotations

from tcg.sources.yahoo import parse_hits


def test_yahoo_parse_hits_filters_and_adds_postage(single, config):
    payload = {
        "hits": [
            {"name": "テストex SAR 100/100 美品", "price": 12000, "url": "u1", "seller": {"name": "A"}, "shipping": {"code": 1}},
            {"name": "テストex SAR プロキシ", "price": 500, "url": "u2", "seller": {"name": "B"}, "shipping": {"code": 1}},
            {"name": "テストex SAR 100/100", "price": 11800, "url": "u3", "seller": {"name": "C"}, "shipping": {"code": 3}},
            {"name": "テストex SAR", "price": "x", "url": "u4", "seller": {"name": "D"}},
        ]
    }
    found = parse_hits(payload, single, config["sources"]["yahoo_shopping"])
    assert [o.price for o in found] == [12000, 12100]
    assert found[0].source == "yahoo_shopping" and found[0].side == "buy"
