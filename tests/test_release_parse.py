import pytest

from catalogo.release_parse import (
    episode_in_release,
    pack_matches_season,
    parse_episode,
    parse_release,
    season_of,
)


@pytest.mark.parametrize(
    ("title", "season", "episodes"),
    [
        ("Serie.S01E02.1080p.mkv", 1, (2,)),
        ("Serie S01E02E03 1080p", 1, (2, 3)),
        ("Serie S01E02-E04 1080p", 1, (2, 3, 4)),
        ("Serie 1x02 1080p", 1, (2,)),
        ("Serie Temporada 2 Episodio 5", 2, (5,)),
        ("Serie T1E2 1080p", 1, (2,)),
        ("Más de la cuenta.2025S01E01.1080P.mkv", 1, (1,)),
    ],
)
def test_parse_episodes(title, season, episodes):
    parsed = parse_release(title)
    assert parsed.season == season
    assert parsed.episodes == episodes
    assert parse_episode(title) == (season, episodes[0])


@pytest.mark.parametrize(
    ("title", "season"),
    [
        ("Serie S01 [PACK]", 1),
        ("Serie Temporada 2 [PACK]", 2),
        ("Serie 3ª Temporada [PACK]", 3),
        ("Serie (4ºTemporada) [PACK]", 4),
        ("Serie [QUINTA TEMPORADA] [PACK]", 5),
        ("Serie [SextaTemporada] [PACK]", 6),
        ("Serie T7 WEB-DL 1080p", 7),
    ],
)
def test_season_formats(title, season):
    assert season_of(title) == season
    assert pack_matches_season(title, season)
    assert not pack_matches_season(title, season + 1)


def test_complete_series_and_unknown_packs_match_any_season():
    assert pack_matches_season("Serie Complete Series 1080p", 4)
    assert pack_matches_season("Serie (2020) [PACK] 1080p", 4)
    assert pack_matches_season("Condena (2021) (Miniserie) WEB-DL", 1)


def test_episode_must_match_exactly_but_multiepisode_counts():
    assert episode_in_release("Serie S01E02 1080p", 1, 2)
    assert not episode_in_release("Serie S01E02 1080p", 1, 3)
    assert not episode_in_release("Serie S02E02 1080p", 1, 2)
    assert episode_in_release("Serie S01E02E03 1080p", 1, 3)
