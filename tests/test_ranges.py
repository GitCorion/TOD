from catalogo.ranges import parse_range


def test_no_range_is_full():
    result = parse_range(None, 1000)
    assert result.status == 200
    assert (result.start, result.end, result.length) == (0, 999, 1000)
    assert result.content_range is None


def test_normal_range():
    result = parse_range("bytes=0-99", 1000)
    assert result.status == 206
    assert (result.start, result.end, result.length) == (0, 99, 100)
    assert result.content_range == "bytes 0-99/1000"


def test_open_ended_range():
    result = parse_range("bytes=900-", 1000)
    assert result.status == 206
    assert (result.start, result.end, result.length) == (900, 999, 100)


def test_suffix_range_returns_the_end():
    result = parse_range("bytes=-100", 1000)
    assert result.status == 206
    assert (result.start, result.end, result.length) == (900, 999, 100)
    assert result.content_range == "bytes 900-999/1000"


def test_end_beyond_size_is_clamped():
    result = parse_range("bytes=990-5000", 1000)
    assert result.status == 206
    assert (result.start, result.end, result.length) == (990, 999, 10)


def test_start_at_size_is_416():
    result = parse_range("bytes=1000-", 1000)
    assert result.status == 416
    assert result.content_range == "bytes */1000"


def test_zero_suffix_is_416():
    assert parse_range("bytes=-0", 1000).status == 416


def test_invalid_header_is_ignored():
    for header in ("bytes=abc", "bytes=0-1,5-6", "items=0-1", "bytes=-"):
        result = parse_range(header, 1000)
        assert result.status == 200
        assert result.content_range is None


def test_reversed_range_is_ignored():
    result = parse_range("bytes=500-100", 1000)
    assert result.status == 200
    assert result.length == 1000


def test_empty_file():
    result = parse_range(None, 0)
    assert result.status == 200
    assert result.length == 0
    assert parse_range("bytes=0-", 0).status == 416
