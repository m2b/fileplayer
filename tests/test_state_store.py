import json
import os

import pytest

from publisher.state_store import StateStore


def test_set_persists_after_min_save_interval_elapses(tmp_path):
    path = str(tmp_path / "state.json")
    store = StateStore(path, min_save_interval_s=0.0)
    store.set("device-a", {"last_index": 3, "last_row_ts": 12345})

    with open(path) as fin:
        data = json.load(fin)
    assert data == {"device-a": {"last_index": 3, "last_row_ts": 12345}}


def test_flush_writes_out_pending_changes_even_within_the_debounce_window(tmp_path):
    path = str(tmp_path / "state.json")
    store = StateStore(path, min_save_interval_s=999.0)
    store.set("device-a", {"last_index": 1, "last_row_ts": 100})
    # A second set() within the debounce window must not skip flush()'s write.
    store._dirty = True

    store.flush()
    with open(path) as fin:
        data = json.load(fin)
    assert data == {"device-a": {"last_index": 1, "last_row_ts": 100}}


def test_reloading_picks_up_previously_saved_state(tmp_path):
    path = str(tmp_path / "state.json")
    store = StateStore(path, min_save_interval_s=0.0)
    store.set("device-a", {"last_index": 5, "last_row_ts": 500})

    reloaded = StateStore(path)
    assert reloaded.get("device-a") == {"last_index": 5, "last_row_ts": 500}
    assert reloaded.get("unknown-device") is None


def test_missing_file_starts_empty(tmp_path):
    store = StateStore(str(tmp_path / "does_not_exist.json"))
    assert store.get("anything") is None


def test_flush_retries_past_a_transient_permission_error(tmp_path, monkeypatch):
    # Simulates the Windows-only failure mode where something else (AV, an
    # editor's file watcher) has the destination briefly open the instant
    # it changes, so os.replace raises PermissionError for a moment.
    path = str(tmp_path / "state.json")
    store = StateStore(path, min_save_interval_s=0.0)
    store._state["device-a"] = {"last_index": 1, "last_row_ts": 100}
    store._dirty = True

    real_replace = os.replace
    calls = {"count": 0}

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError("simulated transient lock")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    store.flush()

    assert calls["count"] == 3
    with open(path) as fin:
        assert json.load(fin) == {"device-a": {"last_index": 1, "last_row_ts": 100}}


def test_flush_gives_up_after_repeated_permission_errors(tmp_path, monkeypatch):
    path = str(tmp_path / "state.json")
    store = StateStore(path, min_save_interval_s=0.0)
    store._state["device-a"] = {"last_index": 1, "last_row_ts": 100}
    store._dirty = True

    def always_fails(src, dst):
        raise PermissionError("simulated persistent lock")

    monkeypatch.setattr(os, "replace", always_fails)

    with pytest.raises(PermissionError):
        store.flush()
