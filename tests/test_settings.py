from pathlib import Path

import pytest

from catalogo.db import init_db
from catalogo.settings import (
    ONDEMAND_MAX_ACTIVE,
    get_int,
    ondemand_max_active,
    set_ondemand_max_active,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_ondemand_limit_defaults_and_persists(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    assert get_int(db, ONDEMAND_MAX_ACTIVE, 4) == 4
    assert ondemand_max_active(db, 4) == 4
    assert set_ondemand_max_active(db, 7) == 7
    assert ondemand_max_active(db, 4) == 7
    assert get_int(db, ONDEMAND_MAX_ACTIVE, 4) == 7


def test_ondemand_limit_rejects_out_of_range(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    with pytest.raises(ValueError):
        set_ondemand_max_active(db, 0)
    with pytest.raises(ValueError):
        set_ondemand_max_active(db, 11)
