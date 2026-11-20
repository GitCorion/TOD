import io
from pathlib import Path

import pytest

from catalogo.stream import (
    RangeNotSatisfiable,
    StreamTimeout,
    file_offset,
    find_file,
    stream_torrent_file,
    wait_pieces,
)

MB = 1024 * 1024


class FakeHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


class FakeQbit:
    def __init__(self, files, states, piece_size=MB):
        self._files = files
        self._states = states
        self._piece_size = piece_size
        self.calls = 0

    def files(self, info_hash):
        return list(self._files)

    def properties(self, info_hash):
        return {"piece_size": self._piece_size}

    def piece_states(self, info_hash):
        self.calls += 1
        return list(self._states)


def make_files(tmp_path):
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.write_bytes(b"A" * MB)
    second.write_bytes(b"B" * (2 * MB))
    files = [
        {"index": 0, "name": "first.mkv", "size": MB},
        {"index": 1, "name": "second.mkv", "size": 2 * MB},
    ]
    return files, first, second


def test_file_offset_and_find(tmp_path):
    files, _first, second = make_files(tmp_path)
    assert file_offset(files, 0) == 0
    assert file_offset(files, 1) == MB
    assert find_file("second.mkv", [str(tmp_path)]) == second


def test_stream_second_file_uses_global_offset_and_range(tmp_path):
    files, _first, second = make_files(tmp_path)
    qbit = FakeQbit(files, [2, 2, 2, 2])
    handler = FakeHandler()
    stream_torrent_file(
        handler, qbit, "hash", 1, 0, MB - 1,
        roots=[str(tmp_path)], timeout=1,
    )
    assert handler.status == 206
    assert handler.headers["Content-Range"] == f"bytes 0-{MB-1}/{2*MB}"
    assert handler.headers["Content-Length"] == str(MB)
    assert handler.wfile.getvalue() == b"B" * MB


def test_stream_head_has_no_body(tmp_path):
    files, _first, _second = make_files(tmp_path)
    qbit = FakeQbit(files, [2, 2, 2, 2])
    handler = FakeHandler()
    stream_torrent_file(
        handler, qbit, "hash", 0, 0, 99,
        roots=[str(tmp_path)], timeout=1, head=True,
    )
    assert handler.status == 206
    assert handler.wfile.getvalue() == b""


def test_far_seek_times_out_before_headers(tmp_path):
    files, _first, _second = make_files(tmp_path)
    qbit = FakeQbit(files, [2, 0, 0, 0])
    handler = FakeHandler()
    with pytest.raises(StreamTimeout):
        stream_torrent_file(
            handler, qbit, "hash", 1, 0, MB - 1,
            roots=[str(tmp_path)], timeout=0.01,
        )
    assert handler.status is None


def test_range_out_of_bounds():
    files = [{"index": 0, "name": "x.mkv", "size": 10}]
    qbit = FakeQbit(files, [2])
    handler = FakeHandler()
    with pytest.raises(RangeNotSatisfiable):
        stream_torrent_file(
            handler, qbit, "hash", 0, 10, 20, roots=["/tmp"], timeout=0.01
        )


def test_wait_pieces_checks_global_range():
    class Q:
        def __init__(self):
            self.calls = 0

        def piece_states(self, info_hash):
            self.calls += 1
            return [2, 2, 2, 2, 2]

    q = Q()
    assert wait_pieces(q, "hash", MB, MB, MB, timeout=0.1)
    assert q.calls == 1
    assert wait_pieces(q, "hash", 0, 6 * MB, MB, timeout=0.01) is False
