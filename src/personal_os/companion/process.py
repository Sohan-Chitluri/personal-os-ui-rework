"""User-scoped process inspection (M2-A).

Security invariants:
- Reports only processes owned by the current user (os.getuid()).
- Never returns root or other users' processes.
- Sanitizes command arguments to prevent leaking credentials or secrets.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from typing import Sequence

from personal_os.companion.protocol import (
    ProcessInfo,
    ProcessListParams,
    ProcessListResult,
)

log = logging.getLogger("personal_os.companion.process")

SECRET_ARG_PATTERN = re.compile(
    r"(token|secret|password|key|auth|bearer)=([^\s]+)", re.IGNORECASE
)


def sanitize_process_name(name: str) -> str:
    """Sanitize process name to exclude potential embedded secrets."""
    return SECRET_ARG_PATTERN.sub(r"\1=[REDACTED]", name).strip()


class ProcessInspector:
    """Safely inspects processes owned by the active user."""

    def __init__(self) -> None:
        self.current_uid = os.getuid() if hasattr(os, "getuid") else None

    def list_processes(self, params: ProcessListParams | None = None) -> ProcessListResult:
        """Return processes owned strictly by the current user."""
        limit = params.limit if params else 100
        name_filter = params.name_filter.lower() if params and params.name_filter else None

        processes: list[ProcessInfo] = []

        if sys.platform.startswith("linux") and os.path.exists("/proc"):
            processes = self._list_from_proc(limit=limit, name_filter=name_filter)
        else:
            processes = self._list_from_ps(limit=limit, name_filter=name_filter)

        return ProcessListResult(
            processes=processes[:limit],
            total=len(processes),
        )

    def _list_from_proc(self, limit: int, name_filter: str | None) -> list[ProcessInfo]:
        """Read /proc for user-owned processes on Linux."""
        results: list[ProcessInfo] = []

        try:
            entries = os.listdir("/proc")
        except OSError as exc:
            log.warning("Could not list /proc: %s", exc)
            return results

        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            proc_path = f"/proc/{pid}"

            try:
                st = os.stat(proc_path)
                if self.current_uid is not None and st.st_uid != self.current_uid:
                    # Belongs to another user (or root); skip
                    continue
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue

            # Read process name from /proc/[pid]/comm or status
            name = ""
            comm_path = f"{proc_path}/comm"
            try:
                with open(comm_path, "r", encoding="utf-8", errors="ignore") as f:
                    name = f.read().strip()
            except (FileNotFoundError, PermissionError, OSError):
                # Fallback to cmdline
                cmdline_path = f"{proc_path}/cmdline"
                try:
                    with open(cmdline_path, "r", encoding="utf-8", errors="ignore") as f:
                        cmd = f.read().replace("\x00", " ").strip()
                        name = cmd.split()[0] if cmd else f"proc_{pid}"
                except (FileNotFoundError, PermissionError, OSError):
                    name = f"proc_{pid}"

            name = sanitize_process_name(name)
            if name_filter and name_filter not in name.lower():
                continue

            # Read status and memory from /proc/[pid]/statm or status
            rss_bytes = 0
            statm_path = f"{proc_path}/statm"
            try:
                with open(statm_path, "r", encoding="utf-8") as f:
                    parts = f.read().split()
                    if len(parts) >= 2:
                        pages = int(parts[1])
                        page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
                        rss_bytes = pages * page_size
            except (FileNotFoundError, PermissionError, OSError, ValueError):
                pass

            results.append(
                ProcessInfo(
                    pid=pid,
                    name=name,
                    status="running",
                    memory_rss_bytes=rss_bytes,
                    username=os.environ.get("USER", "user"),
                )
            )

            if len(results) >= limit * 2:
                break

        return results

    def _list_from_ps(self, limit: int, name_filter: str | None) -> list[ProcessInfo]:
        """Fallback to 'ps' command for non-Linux or containerized environments."""
        results: list[ProcessInfo] = []
        user = os.environ.get("USER", "")

        cmd = ["ps", "-u", user, "-o", "pid,comm"] if user else ["ps", "-o", "pid,comm"]
        try:
            output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            log.warning("Failed to execute ps: %s", exc)
            return results

        lines = output.strip().splitlines()
        if len(lines) <= 1:
            return results

        for line in lines[1:]:
            parts = line.strip().split(None, 1)
            if len(parts) < 2 or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            name = sanitize_process_name(parts[1])
            if name_filter and name_filter not in name.lower():
                continue

            results.append(
                ProcessInfo(
                    pid=pid,
                    name=name,
                    status="running",
                    username=user,
                )
            )
            if len(results) >= limit:
                break

        return results
