"""Runtime entrypoint for Personal OS LiveKit Voice Agent Worker."""

from __future__ import annotations

import logging

from personal_os.voice.agent import create_voice_agent_worker

log = logging.getLogger("personal_os.deploy_voice_worker")


def main() -> None:
    try:
        create_voice_agent_worker()
    except Exception:
        log.exception("Voice worker runtime failure")
        raise


if __name__ == "__main__":
    main()
