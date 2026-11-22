"""Cliente mínimo de TMDB en solo lectura.

Solo metadatos: nunca descarga torrents ni toca Jellyfin. El título original
es la clave para buscar en trackers que usan el nombre internacional.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.themoviedb.org/3"


class TmdbError(RuntimeError):
    pass


def year_of_date(value: str | None) -> int | None:
    text = (value or "").strip()
    if len(text) >= 4 and text[:4].isdigit():
        year = int(text[:4])
        return year if 1900 <= year <= 2100 else None
    return None


def _countries(result: dict, *, tv: bool = False) -> str | None:
    raw = result.get("origin_country") if tv else result.get("production_countries")
    codes: list[str] = []
    for entry in raw or []:
        if isinstance(entry, dict):
            code = entry.get("iso_3166_1")
        else:
            code = entry
        code = str(code or "").strip().upper()
        if code and code not in codes:
            codes.append(code)
    return ",".join(codes) or None


def movie_item(result: dict) -> dict | None:
    """Normaliza un resultado de TMDB a una fila de catálogo."""
    try:
        tmdb = int(result.get("id") or 0)
    except (TypeError, ValueError):
        tmdb = 0
    title = (result.get("title") or result.get("original_title") or "").strip()
    if not tmdb or not title:
        return None
    return {
        "kind": "movie",
        "tmdb": tmdb,
        "title": title,
        "original_title": (
            result.get("original_title") or title
        ).strip(),
        "year": year_of_date(result.get("release_date")),
        "original_language": (result.get("original_language") or "").strip()
        or None,
        "original_country": _countries(result),
        "popularity": float(result.get("popularity") or 0),
        "release_date": (result.get("release_date") or "").strip() or None,
        "overview": (result.get("overview") or "").strip() or None,
        "poster_path": (result.get("poster_path") or "").strip() or None,
        "backdrop_path": (result.get("backdrop_path") or "").strip() or None,
        "vote_average": float(result.get("vote_average") or 0) or None,
    }


def tv_item(result: dict) -> dict | None:
    try:
        tmdb = int(result.get("id") or 0)
    except (TypeError, ValueError):
        tmdb = 0
    title = (result.get("name") or result.get("original_name") or "").strip()
    if not tmdb or not title:
        return None
    return {
        "kind": "series",
        "tmdb": tmdb,
        "title": title,
        "original_title": (
            result.get("original_name") or title
        ).strip(),
        "year": year_of_date(result.get("first_air_date")),
        "original_language": (result.get("original_language") or "").strip()
        or None,
        "original_country": _countries(result, tv=True),
        "popularity": float(result.get("popularity") or 0),
        "release_date": (result.get("first_air_date") or "").strip() or None,
        "overview": (result.get("overview") or "").strip() or None,
        "poster_path": (result.get("poster_path") or "").strip() or None,
        "backdrop_path": (result.get("backdrop_path") or "").strip() or None,
        "vote_average": float(result.get("vote_average") or 0) or None,
    }


def episode_items(season_number: int, payload: dict) -> list[dict]:
    episodes: list[dict] = []
    for episode in payload.get("episodes") or []:
        try:
            number = int(episode.get("episode_number") or 0)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        episodes.append(
            {
                "season": int(season_number),
                "episode": number,
                "name": (episode.get("name") or "").strip() or None,
                "air_date": (episode.get("air_date") or "").strip() or None,
                "overview": (episode.get("overview") or "").strip() or None,
                "still_path": (episode.get("still_path") or "").strip() or None,
            }
        )
    return episodes


class TmdbClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        language: str = "es-ES",
    ):
        if not api_key:
            raise TmdbError("sin API key de TMDB")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.language = language

    @classmethod
    def from_config(cls, config) -> "TmdbClient":
        if not getattr(config, "tmdb_api_key", None):
            raise TmdbError(
                "sin TMDB_API_KEY en el entorno ni en .env"
            )
        return cls(config.tmdb_api_key)

    def _get(self, path: str, **params) -> dict:
        params.setdefault("language", self.language)
        params["api_key"] = self.api_key
        url = f"{self.base_url}/{path.lstrip('/')}?" + urllib.parse.urlencode(
            params
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "catalogo-tmdb/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise TmdbError(f"TMDB HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TmdbError(f"TMDB no responde: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise TmdbError("TMDB devolvió JSON inválido") from exc
        if not isinstance(payload, dict):
            raise TmdbError("TMDB devolvió una respuesta inesperada")
        return payload

    def discover_movies(
        self,
        *,
        year: int | None = None,
        min_votes: int = 0,
        sort_by: str = "popularity.desc",
        page: int = 1,
    ) -> dict:
        params: dict[str, object] = {
            "sort_by": sort_by,
            "page": page,
            "include_adult": "false",
        }
        if year:
            params["primary_release_date.gte"] = f"{year}-01-01"
            params["primary_release_date.lte"] = f"{year}-12-31"
        if min_votes:
            params["vote_count.gte"] = min_votes
        return self._get("discover/movie", **params)

    def popular_movies(self, *, page: int = 1) -> dict:
        return self._get("movie/popular", page=page)

    def popular_tv(self, *, page: int = 1) -> dict:
        return self._get("tv/popular", page=page)

    def movie_details(self, tmdb_id: int) -> dict:
        return self._get(f"movie/{int(tmdb_id)}")

    def collection_details(self, collection_id: int) -> dict:
        return self._get(f"collection/{int(collection_id)}")

    def list_details(self, list_id: int, *, page: int = 1) -> dict:
        return self._get(f"list/{int(list_id)}", page=page)

    def search_movie(
        self, query: str, *, year: int | None = None, page: int = 1
    ) -> dict:
        params: dict[str, object] = {"query": query, "page": page}
        if year:
            params["year"] = year
        return self._get("search/movie", **params)

    def search_tv(
        self, query: str, *, year: int | None = None, page: int = 1
    ) -> dict:
        params: dict[str, object] = {"query": query, "page": page}
        if year:
            params["first_air_date_year"] = year
        return self._get("search/tv", **params)

    def tv_details(self, tmdb_id: int) -> dict:
        return self._get(f"tv/{int(tmdb_id)}")

    def season_details(self, tmdb_id: int, season: int) -> dict:
        return self._get(f"tv/{int(tmdb_id)}/season/{int(season)}")

    def alternative_titles(self, kind: str, tmdb_id: int) -> list[str]:
        path = (
            f"movie/{int(tmdb_id)}/alternative_titles"
            if kind == "movie"
            else f"tv/{int(tmdb_id)}/alternative_titles"
        )
        payload = self._get(path)
        rows = payload.get("titles") or payload.get("results") or []
        titles: list[str] = []
        for row in rows:
            value = (row.get("title") or row.get("name") or "").strip()
            if value and value not in titles:
                titles.append(value)
        return titles
