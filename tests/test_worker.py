from catalogo.worker import run_forever, run_once


def test_run_once_order_and_error_isolation():
    calls = []

    def make(name, fail=False):
        def handler():
            calls.append(name)
            if fail:
                raise RuntimeError(f"{name} failed")
            return {"name": name}
        return handler

    result = run_once(
        cleanup=make("cleanup"),
        sync=make("sync"),
        abandon=make("abandon"),
        moves=make("moves", fail=True),
        seeding=make("seeding"),
        debrid=make("debrid"),
    )
    assert calls == ["cleanup", "sync", "abandon", "moves", "seeding", "debrid"]
    assert not result.ok
    assert result.stages["moves"]["error"] == "moves failed"
    assert result.stages["sync"] == {"name": "sync"}


def test_run_forever_once():
    emitted = []
    calls = []

    def handler():
        calls.append("sync")
        return {"ok": True}

    run_forever(
        sync=handler,
        once=True,
        emit=emitted.append,
        sleep=lambda _: None,
    )
    assert calls == ["sync"]
    assert len(emitted) == 1
    assert emitted[0]["stages"]["sync"] == {"ok": True}
