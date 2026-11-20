from catalogo.qbit_limits import (
    DEFAULT_RADARR_KBPS,
    DEFAULT_SONARR_KBPS,
    apply_limits,
    limits_from_kbps,
)


class FakeLimitsClient:
    def __init__(self, torrents):
        self._torrents = torrents
        self.calls = []

    def torrents(self, category=None):
        return list(self._torrents)

    def set_download_limit_many(self, info_hashes, limit):
        self.calls.append((tuple(info_hashes), limit))


def test_defaults_total_5000_kibps():
    assert DEFAULT_RADARR_KBPS + DEFAULT_SONARR_KBPS == 5000


def test_limits_from_kbps_converts_to_bytes():
    limits = limits_from_kbps(5120, 2560)
    assert limits["radarr"] == 5120 * 1024
    assert limits["tv-sonarr"] == 2560 * 1024
    assert limits["ondemand"] == 0


def test_dry_run_does_not_write():
    client = FakeLimitsClient([
        {"hash": "a", "category": "radarr", "dl_limit": 0},
    ])
    report = apply_limits(
        client, limits_from_kbps(5120, 5120), dry_run=True
    )
    assert report["checked"] == 1
    assert report["planned"][0]["torrents"] == 1
    assert client.calls == []


def test_groups_hashes_and_leaves_other_categories():
    client = FakeLimitsClient([
        {"hash": "r1", "category": "radarr", "dl_limit": 0},
        {"hash": "r2", "category": "radarr", "dl_limit": 0},
        {"hash": "s1", "category": "tv-sonarr", "dl_limit": 0},
        {"hash": "o1", "category": "ondemand", "dl_limit": 5242880},
        {"hash": "c1", "category": "completo", "dl_limit": 0},
    ])
    report = apply_limits(client, limits_from_kbps(5120, 5120))
    assert report["updated"] == 4
    assert report["by_category"]["radarr"] == 2
    assert report["by_category"]["tv-sonarr"] == 1
    assert report["by_category"]["ondemand"] == 1
    assert any(
        set(call[0]) == {"r1", "r2", "s1"} for call in client.calls
    )
    assert any(call[0] == ("o1",) for call in client.calls)
    assert all("c1" not in call[0] for call in client.calls)


def test_already_correct_is_not_rewritten():
    client = FakeLimitsClient([
        {"hash": "r1", "category": "radarr", "dl_limit": 5242880},
        {"hash": "o1", "category": "ondemand", "dl_limit": 0},
    ])
    report = apply_limits(client, limits_from_kbps(5120, 5120))
    assert report["already"] == 2
    assert report["updated"] == 0
    assert client.calls == []
