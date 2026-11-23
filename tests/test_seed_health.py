"""Veredictos de tracker: lo que qBittorrent recibe en cada announce."""

from catalogo.seed_health import classify


def test_unregistered_torrent():
    verdict, message = classify(["Unregistered torrent"])
    assert verdict == "unregistered"
    assert message == "Unregistered torrent"


def test_client_not_allowed():
    verdict, _ = classify(["Client is not allowed: qBittorrent 5.1.4"])
    assert verdict == "client_not_allowed"


def test_not_authorized():
    verdict, _ = classify(["Invalid passkey"])
    assert verdict == "not_authorized"


def test_healthy_tracker_has_no_verdict():
    assert classify(["Working", ""]) == (None, None)
    assert classify([]) == (None, None)


def test_dht_entries_are_ignored_upstream():
    # El filtro de ** (DHT/PEX/LSD) vive en seeding; classify solo ve texto.
    assert classify(["DHT not connected"]) == (None, None)
