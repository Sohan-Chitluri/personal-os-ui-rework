"""Hermes runtime client adapter and session manager for Raphael.

Provides both LiveHermesClient (delegating to the installed Hermes CLI runtime)
and MockHermesClient (for testing, offline isolation, and simulated failures).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from personal_os.config import Settings

log = logging.getLogger(__name__)


class HermesError(Exception):
    """Base error for Hermes runtime interactions."""


class HermesTimeoutError(HermesError):
    """Hermes invocation exceeded the specified timeout limit."""


class HermesConnectionError(HermesError):
    """Hermes runtime is unreachable, returned an error, or crashed."""


@dataclass(frozen=True)
class HermesResponse:
    reply: str
    session_id: str | None = None


REASONING_TAG_REGEX = re.compile(
    r"<(?:think|thought|reasoning)>[\s\S]*?(?:</(?:think|thought|reasoning)>|$)",
    re.IGNORECASE,
)
REASONING_BOX_UNICODE = re.compile(
    r"[ \t]*┌[─\-]+[ \t]*Reasoning[ \t]*[─\-]*[┐\n][\s\S]*?(?:└[─\-]+[┘\n]?[ \t]*\n?|$)",
    re.IGNORECASE,
)
REASONING_BOX_ASCII = re.compile(
    r"[ \t]*\+[-=]+[ \t]*Reasoning[ \t]*[-=]*\+?[\s\S]*?(?:\+[-=]+\+[ \t]*\n?|$)",
    re.IGNORECASE,
)


def strip_hermes_reasoning(text: str) -> str:
    """Remove internal agent reasoning blocks (<think> tags and Unicode/ASCII reasoning boxes)."""
    if not text:
        return ""
    cleaned = REASONING_TAG_REGEX.sub("", text)
    cleaned = REASONING_BOX_UNICODE.sub("", cleaned)
    cleaned = REASONING_BOX_ASCII.sub("", cleaned)
    return cleaned.strip()


def parse_hermes_output(output: str, stderr: str = "") -> tuple[str, str | None]:
    """Parse Hermes CLI quiet output to extract the assistant reply and session_id."""
    lines = output.splitlines()
    reply_lines: list[str] = []
    session_id: str | None = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("session_id:"):
            session_id = stripped.split(":", 1)[1].strip()
            continue
        # Skip CLI header info lines
        is_resume_header = stripped.startswith("↻ Resumed session ")
        is_fresh_session = stripped.startswith("Session ") and (
            "Starting fresh" in stripped or "has no messages" in stripped
        )
        if is_resume_header or is_fresh_session:
            continue

        reply_lines.append(line)

    # In Hermes CLI quiet mode, session_id is written to stderr to keep stdout clean
    if not session_id and stderr:
        for line in stderr.splitlines():
            stripped = line.strip()
            if stripped.startswith("session_id:"):
                session_id = stripped.split(":", 1)[1].strip()
                break

    raw_reply = "\n".join(reply_lines).strip()
    reply = strip_hermes_reasoning(raw_reply)
    return reply, session_id


@runtime_checkable
class HermesClient(Protocol):
    """Protocol defining the Hermes runtime invocation interface."""

    async def send_message(
        self,
        prompt: str,
        session_id: str | None = None,
        session_name: str | None = None,
        skill: str | None = None,
        timeout_seconds: float = 90.0,
        model: str | None = None,
        provider: str | None = None,
        safe_mode: bool | None = None,
        yolo: bool | None = None,
    ) -> HermesResponse: ...


class LiveHermesClient:
    """Interacts with the real Hermes CLI agent runtime installed on the host."""

    def __init__(
        self,
        binary_path: str = "hermes",
        default_skill: str = "personal-os",
        default_model: str | None = None,
        default_provider: str | None = None,
        safe_mode: bool = False,
        yolo: bool = False,
    ) -> None:
        self.binary_path = binary_path
        self.default_skill = default_skill
        self.default_model = default_model
        self.default_provider = default_provider
        self.safe_mode = safe_mode
        self.yolo = yolo

    def is_available(self) -> bool:
        """Check whether the Hermes binary exists and is executable."""
        resolved = shutil.which(self.binary_path) or (
            self.binary_path
            if os.path.exists(self.binary_path) and os.access(self.binary_path, os.X_OK)
            else None
        )
        return resolved is not None

    def build_command(
        self,
        prompt: str,
        session_id: str | None = None,
        skill: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        safe_mode: bool | None = None,
        yolo: bool | None = None,
    ) -> list[str]:
        """Build the subprocess command line arguments adhering strictly to Hermes CLI flags."""
        resolved_binary = shutil.which(self.binary_path) or (
            self.binary_path
            if os.path.exists(self.binary_path) and os.access(self.binary_path, os.X_OK)
            else None
        )
        if not resolved_binary:
            raise HermesConnectionError(
                f"Hermes binary not found or not executable at '{self.binary_path}'"
            )

        cmd = [resolved_binary, "chat", "-q", prompt, "-Q"]

        if session_id:
            cmd.extend(["-r", session_id])

        chosen_skill = skill or self.default_skill
        if chosen_skill:
            cmd.extend(["-s", chosen_skill])

        chosen_model = model or self.default_model
        if chosen_model:
            cmd.extend(["-m", chosen_model])

        chosen_provider = provider or self.default_provider
        if chosen_provider:
            cmd.extend(["--provider", chosen_provider])

        is_safe = self.safe_mode if safe_mode is None else safe_mode
        if is_safe:
            cmd.append("--safe-mode")

        is_yolo = self.yolo if yolo is None else yolo
        if is_yolo:
            cmd.append("--yolo")

        return cmd

    async def send_message(
        self,
        prompt: str,
        session_id: str | None = None,
        session_name: str | None = None,
        skill: str | None = None,
        timeout_seconds: float = 90.0,
        model: str | None = None,
        provider: str | None = None,
        safe_mode: bool | None = None,
        yolo: bool | None = None,
    ) -> HermesResponse:
        cmd = self.build_command(
            prompt=prompt,
            session_id=session_id,
            skill=skill,
            model=model,
            provider=provider,
            safe_mode=safe_mode,
            yolo=yolo,
        )

        log.info("Launching Hermes CLI with args: %s", cmd)

        env = dict(os.environ)
        env.setdefault("HERMES_STATE_DB_GUARD_BYPASS", "1")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            raise HermesConnectionError(f"Failed to start Hermes process: {exc}") from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            try:
                kill_fn = getattr(proc, "kill", None)
                if callable(kill_fn):
                    res = kill_fn()
                    if hasattr(res, "__await__"):
                        await res
                await proc.wait()
            except Exception:
                pass
            raise HermesTimeoutError(f"Hermes timed out after {timeout_seconds} seconds") from exc

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            clean_error = (
                stderr.strip() or stdout.strip() or f"Process exited with code {proc.returncode}"
            )
            # Extract specific error line if preceded by usage text so CLI argument errors
            # are surfaced clearly.
            error_lines = [
                line.strip() for line in clean_error.splitlines() if "error:" in line.lower()
            ]
            error_msg = error_lines[-1] if error_lines else clean_error
            log.warning("Hermes CLI failed (code %d): %s", proc.returncode, error_msg)
            raise HermesConnectionError(f"Hermes runtime error: {error_msg}")

        reply, parsed_session_id = parse_hermes_output(stdout, stderr=stderr)
        effective_session_id = parsed_session_id or session_id

        if not reply:
            if stderr.strip():
                raise HermesConnectionError(
                    f"Hermes returned error without response: {stderr.strip()}"
                )
            raise HermesConnectionError("Hermes returned an empty response")

        return HermesResponse(reply=reply, session_id=effective_session_id)


class MockHermesClient:
    """Deterministic, isolated test adapter for Hermes runtime."""

    def __init__(self) -> None:
        self.recorded_calls: list[dict[str, Any]] = []
        self.simulated_error: Exception | None = None
        self.custom_responses: list[str] = []
        self._sessions: dict[str, list[dict[str, str]]] = {}

    def is_available(self) -> bool:
        return True

    async def send_message(
        self,
        prompt: str,
        session_id: str | None = None,
        session_name: str | None = None,
        skill: str | None = None,
        timeout_seconds: float = 90.0,
        model: str | None = None,
        provider: str | None = None,
        safe_mode: bool | None = None,
        yolo: bool | None = None,
    ) -> HermesResponse:
        self.recorded_calls.append(
            {
                "prompt": prompt,
                "session_id": session_id,
                "session_name": session_name,
                "skill": skill,
                "timeout_seconds": timeout_seconds,
                "model": model,
                "provider": provider,
                "safe_mode": safe_mode,
                "yolo": yolo,
            }
        )

        if self.simulated_error is not None:
            raise self.simulated_error

        sid = session_id or f"mock-session-{uuid4().hex[:12]}"
        history = self._sessions.setdefault(sid, [])
        history.append({"role": "user", "content": prompt})

        if self.custom_responses:
            reply = self.custom_responses.pop(0)
        else:
            reply = self._generate_rule_based_response(prompt, sid)

        history.append({"role": "assistant", "content": reply})
        return HermesResponse(reply=reply, session_id=sid)

    def _extract_clean_title(self, text: str) -> str:
        res = text.strip()
        for p in (
            "i need to ",
            "i should ",
            "i have to ",
            "i want to ",
            "i must ",
            "i ought to ",
        ):
            if res.lower().startswith(p):
                res = res[len(p) :].strip()
                break
        for s in (" soon", " sometime", " later", " today", " tomorrow"):
            if res.lower().endswith(s):
                res = res[: -len(s)].strip()
                break
        return res[:1].upper() + res[1:] if res else "New Task"

    def _parse_task_command(self, text: str) -> tuple[str, str | None, str | None, str | None]:
        res = text.strip()
        low = res.lower()

        # 1. Project extraction: "in project <name>", "for project <name>", "project: <name>"
        project: str | None = None
        proj_match = re.search(
            r"(?:in|for)\s+project\s+([A-Za-z0-9_\-\s]+?)(?=\s+by|\s+due|\s+tomorrow|\s+today|$)",
            res,
            re.IGNORECASE,
        )
        if proj_match:
            project = proj_match.group(1).strip()
            res = res[: proj_match.start()] + res[proj_match.end() :]
        elif "wwtp" in low:
            project = "WWTP"
        elif "cordic" in low:
            project = "CORDIC"
        elif "vlsi" in low:
            project = "VLSI"

        # 2. Due date extraction: "tomorrow", "today", "tonight", "friday", "next week", ISO date
        due_date: str | None = None
        if "tomorrow" in low:
            due_date = "tomorrow"
            res = re.sub(r"(?i)\b(?:by\s+)?tomorrow\b", "", res).strip()
        elif "tonight" in low:
            due_date = "tonight"
            res = re.sub(r"(?i)\b(?:by\s+)?tonight\b", "", res).strip()
        elif "today" in low:
            due_date = "today"
            res = re.sub(r"(?i)\b(?:by\s+)?today\b", "", res).strip()
        elif "friday" in low:
            due_date = "friday"
            res = re.sub(r"(?i)\b(?:by|before\s+)?friday\b", "", res).strip()
        elif "next week" in low:
            due_date = "next week"
            res = re.sub(r"(?i)\b(?:by\s+)?next\s+week\b", "", res).strip()
        else:
            date_match = re.search(r"\b(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z?)?)\b", res)
            if date_match:
                due_date = date_match.group(1)
                res = res[: date_match.start()] + res[date_match.end() :]

        # 3. Description & Context extraction (e.g. Somya Sir, WWTP BOM, soft deadline)
        desc_parts: list[str] = []
        if "somya sir" in low:
            desc_parts.append("Recipient: Somya Sir")
        if "wwtp" in low and project != "WWTP":
            desc_parts.append("Context: WWTP")
        if "soft deadline" in low:
            desc_parts.append("Soft deadline")
        elif "hard deadline" in low:
            desc_parts.append("Hard deadline")

        description: str | None = ". ".join(desc_parts) + "." if desc_parts else None

        # Clean "soft deadline" or "hard deadline" from res
        res = re.sub(r"(?i)\s*[\(,–-]?\s*(?:soft|hard)\s+deadline\s*[\)]?", "", res).strip()

        # 4. Clean command prefixes
        for cmd in (
            "add a task to ",
            "add a task for ",
            "add a task: ",
            "add a task ",
            "create a task to ",
            "create a task for ",
            "create a task: ",
            "create a task ",
            "add this to my tasks: ",
            "add this to my tasks ",
            "add to my tasks: ",
            "add to my tasks ",
            "add to blockday: ",
            "add to blockday ",
            "add task to ",
            "add task: ",
            "add task ",
            "create task to ",
            "create task: ",
            "create task ",
            "new task: ",
            "new task ",
            "remind me to ",
            "make a task to ",
            "make a task ",
            "i have to ",
            "i need to ",
            "need to ",
            "we should ",
            "i must ",
            "i ought to ",
        ):
            if res.lower().startswith(cmd):
                res = res[len(cmd) :].strip()
                break

        res = re.sub(r"\s+(?:by|in|for)\s*$", "", res).strip().rstrip(".! ")
        for s in (" soon", " sometime", " later"):
            if res.lower().endswith(s):
                res = res[: -len(s)].strip()
                break
        if "submit the bom for the wwtp to somya sir" in low or (
            "bom" in low and "wwtp" in low and "somya" in low
        ):
            title = "Submit WWTP BOM to Somya Sir"
        else:
            title = (res[:1].upper() + res[1:]) if res else ""
        return title, due_date, project, description

    def _parse_project_command(self, text: str) -> tuple[str, str | None]:
        res = text.strip()

        # Check: "create/add a project for <desc> called <name>"
        m = re.search(
            r"(?i)\b(?:create|add|new)\s+(?:a\s+)?project\s+for\s+(.*?)\s+called\s+['\"]?([^'\"\n]+)['\"]?",
            res,
        )
        if m:
            desc = m.group(1).strip()
            name = m.group(2).strip().rstrip(".! ")
            return name, desc or None

        # Check: "create/add a project called <name> with description <desc>"
        m = re.search(
            r"(?i)\b(?:create|add|new)\s+(?:a\s+)?project\s+called\s+['\"]?([^'\"\n]+)['\"]?\s+with\s+description\s+(.*)",
            res,
        )
        if m:
            name = m.group(1).strip().rstrip(".! ")
            desc = m.group(2).strip().rstrip(".! ")
            return name, desc or None

        # Check: "create/add a project called <name> for <desc>"
        m = re.search(
            r"(?i)\b(?:create|add|new)\s+(?:a\s+)?project\s+called\s+['\"]?([^'\"\n]+)['\"]?\s+for\s+(.*)",
            res,
        )
        if m:
            name = m.group(1).strip().rstrip(".! ")
            desc = m.group(2).strip().rstrip(".! ")
            return name, desc or None

        # Check: "create/add a project called <name>[: <desc>]"
        m = re.search(
            r"(?i)\b(?:create|add|new)\s+(?:a\s+)?project\s+called\s+['\"]?([^'\"\n:]+)['\"]?(?::\s*(.*))?",
            res,
        )
        if m:
            name = m.group(1).strip().rstrip(".! ")
            desc = m.group(2).strip().rstrip(".! ") if m.group(2) else None
            return name, desc or None

        # Check generic: "create/add a project <name>[: <desc>]"
        m = re.search(
            r"(?i)\b(?:create|add|new)\s+(?:a\s+)?project\s*[:\s]\s*['\"]?([^'\"\n:]+)['\"]?(?::\s*(.*))?",
            res,
        )
        if m:
            name = m.group(1).strip().rstrip(".! ")
            desc = m.group(2).strip().rstrip(".! ") if m.group(2) else None
            return name, desc or None

        # Fallback: remove command prefix
        for cmd in (
            "add a project called ",
            "add a project: ",
            "add a project ",
            "create a project called ",
            "create a project: ",
            "create a project ",
            "add project: ",
            "add project ",
            "create project: ",
            "create project ",
            "new project: ",
            "new project ",
        ):
            if res.lower().startswith(cmd):
                res = res[len(cmd) :].strip()
                break

        name = res.strip().strip("'\"").rstrip(".! ")
        return name, None

    def _generate_rule_based_response(self, prompt: str, session_id: str) -> str:
        stripped = prompt.strip()
        low = stripped.lower()

        history = self._sessions.get(session_id, [])
        prior_assistant_msgs = [m["content"] for m in history[:-1] if m.get("role") == "assistant"]
        prior_assistant = prior_assistant_msgs[-1] if prior_assistant_msgs else ""
        prior_user_msgs = [m["content"] for m in history[:-1] if m.get("role") == "user"]
        prior_user = prior_user_msgs[-1] if prior_user_msgs else ""

        # Case 1: Pending intent question resolution (choice between add task, work now, or both)
        is_intent_choice_pending = (
            "should i add" in prior_assistant.lower()
            or "would you like me to create a task" in prior_assistant.lower()
        ) and (
            "blockday" in prior_assistant.lower()
            or "both" in prior_assistant.lower()
            or "task" in prior_assistant.lower()
        )
        if is_intent_choice_pending:
            if any(w in low for w in ("both", "do both", "option 3", "3")):
                title, due_date, project, desc = self._parse_task_command(prior_user)
                action: dict[str, Any] = {
                    "action": "create_task",
                    "title": title or "New Task",
                    "due_date": due_date,
                    "project": project,
                }
                if desc:
                    action["description"] = desc
                action_block = f"```action\n{json.dumps(action)}\n```"
                work_topic = "BOM" if "bom" in prior_user.lower() else "work"
                return (
                    f"{action_block}\nI'll add the task to Blockday and let's get started on the {work_topic}. "
                    "To begin, what items or specifications should we include?"
                )
            elif any(
                w in low
                for w in (
                    "help me",
                    "work on it",
                    "prepare it",
                    "work now",
                    "help with",
                    "option 2",
                    "2",
                )
            ):
                work_topic = "BOM for the WWTP" if "bom" in prior_user.lower() else "work"
                return f"Let's prepare the {work_topic}. What components, sub-assemblies, or specifications should we include first?"
            elif any(
                w in low
                for w in (
                    "add task",
                    "add to blockday",
                    "track it",
                    "just add",
                    "add it",
                    "yes",
                    "confirm",
                    "create it",
                    "option 1",
                    "1",
                    "please do",
                    "sure",
                    "approve",
                    "go ahead",
                )
            ):
                title, due_date, project, desc = self._parse_task_command(prior_user)
                action = {
                    "action": "create_task",
                    "title": title or "New Task",
                    "due_date": due_date,
                    "project": project,
                }
                if desc:
                    action["description"] = desc
                return f"```action\n{json.dumps(action)}\n```"

        # Case 2: Explicit user approval of a prior project proposal
        approval_words = (
            "yes",
            "go ahead",
            "confirm",
            "sure",
            "please do",
            "approve",
            "do it",
            "create it",
            "yes, create it",
            "yes please",
        )
        is_approval = (
            any(
                low == w or low.startswith(w + ",") or low.startswith(w + " ")
                for w in approval_words
            )
            or "create it" in low
        )
        if is_approval and (
            "would you like me to create a project" in prior_assistant.lower()
            or "create a project" in prior_assistant.lower()
        ):
            name = "New Project"
            if "'" in prior_assistant:
                name = prior_assistant.split("'", 2)[1]
            elif "for " in prior_assistant.lower():
                part = prior_assistant.lower().split("for ", 1)[1]
                name = part.split("?")[0].strip().title()
            elif prior_user:
                name = self._extract_clean_title(prior_user)

            project_action: dict[str, Any] = {
                "action": "create_project",
                "name": name,
            }
            return f"```action\n{json.dumps(project_action)}\n```"

        # Case 3: Category A — Explicit command to create/add a task
        is_explicit_task_cmd = any(
            cmd in low
            for cmd in (
                "add a task",
                "create a task",
                "add task",
                "create task",
                "add this to my tasks",
                "add to my tasks",
                "add to blockday",
                "track this task",
                "remind me to",
                "make a task",
            )
        ) or low.startswith("new task")

        if is_explicit_task_cmd:
            title, due_date, project, desc = self._parse_task_command(stripped)
            if not title:
                return "What title or description would you like to give to the task?"
            task_action: dict[str, Any] = {
                "action": "create_task",
                "title": title,
                "due_date": due_date,
                "project": project,
            }
            if desc:
                task_action["description"] = desc
            return f"```action\n{json.dumps(task_action)}\n```"

        # Case 4: Category C — Explicit request to complete work
        is_explicit_work_cmd = any(
            low.startswith(p)
            for p in (
                "help me prepare",
                "help me write",
                "help me with",
                "can you write",
                "can you draft",
                "can you help me prepare",
                "can you help prepare",
                "write the",
                "draft the",
            )
        )
        if is_explicit_work_cmd:
            if "bom" in low:
                return (
                    "I'd be glad to help you prepare the BOM for the WWTP. What components, materials, "
                    "or specifications should we begin with? Let me know if you would also like me to track this as a task in Blockday."
                )
            return (
                "I can certainly help you with that. How would you like to get started? "
                "Let me know if you also want to track this in Blockday."
            )

        # Case 5: Category D — Explicit request to plan or discuss an idea
        is_plan_discuss = any(
            low.startswith(p)
            for p in (
                "let's plan",
                "let's discuss",
                "can we discuss",
                "can we brainstorm",
                "brainstorm",
                "discuss the",
            )
        )
        if is_plan_discuss:
            return "Let's discuss and plan that out. What are your main goals and constraints for this?"

        # Case 6: Category E — Casual statement / reflection
        if low in (
            "hello",
            "hi",
            "hey",
            "hello!",
            "hi!",
            "good morning",
            "good evening",
            "thanks",
            "thank you",
            "today was a long day",
        ):
            return "Hello! I am Raphael, your Personal OS assistant. How can I help you today?"

        # Case 7: Explicit command to create/add a project
        is_explicit_project_cmd = any(
            cmd in low
            for cmd in (
                "add a project",
                "create a project",
                "add project",
                "create project",
            )
        ) or low.startswith("new project")
        if is_explicit_project_cmd:
            name, description = self._parse_project_command(stripped)
            if not name:
                return "What name would you like to give to the project?"
            proj_act: dict[str, Any] = {
                "action": "create_project",
                "name": name,
            }
            if description:
                proj_act["description"] = description
            return f"```action\n{json.dumps(proj_act)}\n```"

        # Case 8: Category B — Ambiguous intent / Implicit task
        ambiguous_indicators = (
            "i have to",
            "i need to",
            "need to",
            "we should",
            "i should",
            "i must",
            "must",
            "i ought to",
            "i want to",
            "want to",
            "still need to be",
            "has to be",
        )
        is_ambiguous_phrase = any(
            low.startswith(p) or f" {p} " in f" {low} " for p in ambiguous_indicators
        )

        if is_ambiguous_phrase:
            if "bom" in low and "wwtp" in low:
                return "Got it — should I add the WWTP BOM submission to Blockday, help you prepare it now, or both?"
            elif "cordic" in low and "regression" in low:
                return "Got it — should I add fixing the CORDIC regression tests to Blockday, work on it now, or both?"
            elif "presentation" in low:
                return "Got it — should I add the presentation to Blockday, help you work on it now, or both?"
            elif "email" in low and "professor" in low:
                return "Got it — should I add emailing the professor to Blockday, help you draft it now, or both?"
            elif "timing failure" in low:
                return "Got it — should I add investigating the timing failure to Blockday, work on it now, or both?"
            elif "project" in low:
                task_hint = self._extract_clean_title(stripped)
                return (
                    "It sounds like you have a new initiative in mind. "
                    f"Would you like me to create a project for '{task_hint}'?"
                )
            else:
                task_hint = self._extract_clean_title(stripped)
                return (
                    "It sounds like you have an upcoming task. "
                    f"Would you like me to create a task in Blockday for '{task_hint}', help you work on it now, or both?"
                )

        # Fallback default
        return f"Raphael (mock): Received your message: {prompt}"


def build_hermes_client(settings: Settings) -> HermesClient:
    """Build the configured Hermes client based on application settings."""
    if settings.hermes_adapter == "mock":
        return MockHermesClient()
    return LiveHermesClient(
        binary_path=settings.hermes_binary_path,
        default_skill=settings.hermes_skill_name,
    )
