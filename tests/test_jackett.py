import pytest

from catalogo.jackett import (
    JackettError,
    parse_results,
    sanitize_link,
    with_api_key,
)

SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:torznab="http://torznab.com/schemas/2015/feed">
<channel>
<item>
<title>Encanto SPANISH BDRip x264</title>
<link>http://127.0.0.1:9117/dl/publico1/?jackett_apikey=SECRET&amp;path=abc&amp;file=Encanto</link>
<guid>https://publico1.foo/encanto</guid>
<size>2147483648</size>
<torznab:attr name="seeders" value="1"/>
<torznab:attr name="peers" value="2"/>
<torznab:attr name="infohash" value="ABC123"/>
</item>
</channel>
</rss>'''


def test_parse_results_and_sanitize():
    results = parse_results(SAMPLE, "publico1")
    assert len(results) == 1
    item = results[0]
    assert item["title"].startswith("Encanto")
    assert item["seeders"] == 1
    assert item["peers"] == 2
    assert item["infohash"] == "ABC123"
    clean = sanitize_link(item["link"])
    assert "SECRET" not in clean
    assert "path=abc" in clean
    assert "jackett_apikey=SECRET" in with_api_key(clean, "SECRET")


def test_error_xml_raises():
    with pytest.raises(JackettError):
        parse_results(
            b'<rss><error code="900" description="Cloudflare"/></rss>',
            "publico2",
        )
