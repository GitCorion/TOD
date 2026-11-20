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
