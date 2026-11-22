from pathlib import Path

from catalogo.arr import RadarrClient, SonarrClient
from catalogo.arr_data import ArrData

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def test_radarr_queue_reads_all_pages():
    client = RadarrClient("http://radarr", "key")
    calls = []

    def fake(path, *, method="GET", params=None, payload=None):
        calls.append(dict(params or {}))
        page = int((params or {}).get("page") or 1)
        return {
            "records": [{"id": page}],
            "totalRecords": 3,
            "page": page,
            "pageSize": 1,
        }

    client._request = fake
    data = client.queue(page_size=1)
    assert data["totalRecords"] == 3
    assert len(data["records"]) == 3
    assert [call["page"] for call in calls] == [1, 2, 3]
    assert all(call.get("includeUnknownMovieItems") == "true" for call in calls)


def test_sonarr_queue_uses_unknown_series_param():
    client = SonarrClient("http://sonarr", "key")
    seen = {}

    def fake(path, *, method="GET", params=None, payload=None):
        seen.update(params or {})
        return {"records": [], "totalRecords": 0}

    client._request = fake
    client.queue()
    assert seen["includeUnknownSeriesItems"] == "true"


class FakeRadarr:
    def movies(self):
        return [
            {
                "tmdbId": 1,
                "title": "A",
                "year": 2024,
                "hasFile": True,
                "sizeOnDisk": 100,
                "movieFile": {"dateAdded": "2026-01-02T00:00:00Z"},
            },
            {
                "tmdbId": 2,
                "title": "B",
                "year": 2023,
                "hasFile": False,
                "sizeOnDisk": 0,
            },
        ]

    def queue(self):
        return {"records": [], "totalRecords": 531}

    def diskspace(self):
        return [{"path": "/media", "freeSpace": 500, "totalSpace": 1000}]

    def status(self):
        return {"version": "7"}

    def history(self, *, page_size=8):
        return []


class FakeSonarr:
    def series(self):
        return [
            {
                "tmdbId": 10,
                "title": "S",
                "sizeOnDisk": 200,
                "statistics": {
                    "episodeFileCount": 3,
                    "totalEpisodeCount": 10,
                },
            }
        ]

    def queue(self):
        return {"records": [], "totalRecords": 30}

    def diskspace(self):
        return []

    def status(self):
        return {"version": "5"}

    def history(self, *, page_size=8):
        return []


def test_arr_overview_counts_library(monkeypatch):
    arr = ArrData(object())
    monkeypatch.setattr(arr, "_radarr", lambda: FakeRadarr())
    monkeypatch.setattr(arr, "_sonarr", lambda: FakeSonarr())
    data = arr.overview()
    assert data["ok"] is True
    assert data["radarr"]["movies_total"] == 2
    assert data["radarr"]["movies_available"] == 1
    assert data["radarr"]["queue"] == 531
    assert data["sonarr"]["series_available"] == 1
    assert data["sonarr"]["episodes_available"] == 3
    assert data["sonarr"]["episodes_total"] == 10
    assert data["disk"]["path"] == "/media"
    assert data["recent"][0]["title"] == "A"
    assert arr.in_library("movie", 1)["has_file"] is True
    assert arr.in_library("movie", 2)["has_file"] is False
    assert arr.in_library("series", 10)["in_library"] is True
