from pathlib import Path

from catalogo import strm_inventory
from catalogo.strm import strm_path

ROOT = Path("/mnt/catalogo")


def _touch(root: Path, item: dict, key: str) -> Path:
    path = strm_path(root, item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"http://example/play?key={key}", encoding="utf-8")
    return path


def test_inventory_counts_only_main_files(tmp_path):
    movie = {"kind": "movie", "title": "Peli", "year": 2024}
    hyphenated = {"kind": "movie", "title": "Peli - Edición especial", "year": 2024}
    episode = {
        "kind": "series",
        "title": "Serie",
        "year": 2020,
        "season": 1,
        "episode": 2,
    }
    movie_path = _touch(tmp_path, movie, "movie:tmdb:1")
    hyphenated_path = _touch(tmp_path, hyphenated, "movie:tmdb:3")
    _touch(tmp_path, episode, "series:tmdb:2:1:2")
    version = movie_path.with_name(f"{movie_path.stem} - 1080p.strm")
    version.write_text(
        "http://example/play?key=movie%3Atmdb%3A1&release=123",
        encoding="utf-8",
    )

    strm_inventory.invalidate(tmp_path)
    inv = strm_inventory.inventory(tmp_path)
    assert inv["movies"] == 2
    assert inv["series"] == 1
    assert inv["episodes"] == 1
    assert inv["versions"] == 1
    assert str(movie_path) in inv["paths"]
    assert str(hyphenated_path) in inv["paths"]
    assert "movie:tmdb:1" in inv["keys"]
    assert "movie:tmdb:3" in inv["keys"]
    assert "series:tmdb:2:1:2" in inv["keys"]
    assert str(version) not in inv["paths"]
    assert strm_inventory.is_published(tmp_path, movie, inv["paths"])
    assert strm_inventory.is_published(tmp_path, hyphenated, inv["paths"])
    assert not strm_inventory.is_published(
        tmp_path, {**movie, "title": "Otra"}, inv["paths"]
    )


def test_inventory_missing_root_is_empty(tmp_path):
    missing = tmp_path / "nope"
    strm_inventory.invalidate(missing)
    inv = strm_inventory.inventory(missing)
    assert inv["movies"] == 0
    assert inv["series"] == 0
    assert inv["episodes"] == 0
    assert inv["paths"] == set()
