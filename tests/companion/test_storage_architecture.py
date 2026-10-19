"""Keep local companion storage separate from cloud synchronization state."""

from importlib.util import find_spec


def test_companion_has_no_local_operational_queue_adapter() -> None:
    """The companion must not ship a local SQLite or other queue adapter."""
    assert find_spec("personal_os.companion.offline_queue") is None
