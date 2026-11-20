from catalogo.media_probe import has_spanish


def test_has_spanish_variants():
    assert has_spanish(["spa"])
    assert has_spanish(["es-ES"])
    assert has_spanish(["eng", "spa"])
    assert not has_spanish(["eng", "fre"])
    assert not has_spanish(None)


def test_audio_verdict_does_not_guess_with_undeclared_tracks():
    from catalogo.media_probe import AudioProbe, audio_verdict

    assert audio_verdict(AudioProbe(("spa",), True)) is True
    assert audio_verdict(AudioProbe(("eng", "fre"), False)) is False
    assert audio_verdict(AudioProbe(("eng",), True)) is None
    assert audio_verdict(None) is None
