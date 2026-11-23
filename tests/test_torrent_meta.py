import hashlib
from pathlib import Path

from catalogo.qbittorrent import info_hash_from_name
from catalogo.torrent_meta import info_dict_bytes


def test_info_dict_bytes_and_hash(tmp_path):
    info = b"d4:name4:test6:lengthi1000ee"
    raw = b"d4:info" + info + b"e"
    assert info_dict_bytes(raw) == info
    expected = hashlib.sha1(info).hexdigest()

    torrent = tmp_path / "test.torrent"
    torrent.write_bytes(raw)
    assert info_hash_from_name(torrent) == expected


def test_info_dict_missing():
    assert info_dict_bytes(b"d4:name4:teste") is None
    assert info_dict_bytes(b"no-es-bencode") is None


def test_announce_urls_reads_announce_and_list():
    from catalogo.torrent_meta import announce_urls

    def b(value):
        return str(len(value)).encode() + b":" + value.encode()

    main = "http://x.example/announce"
    other = "udp://y.example:6969/announce"
    raw = (
        b"d8:announce" + b(main) + b"13:announce-list"
        + b"ll" + b(main) + b"el" + b(other) + b"eee"
    )
    assert announce_urls(raw) == [main, main, other]
    assert announce_urls(b"no-es-bencode") == []
