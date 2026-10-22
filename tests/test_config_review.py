"""Regression tests for:
- Finding #9: Model configuration in config.toml is ignored (llm.py get_provider).
- Finding #14: Weekly review project_hours remains empty (weekly_review.py duration parsing & aggregation).
- Finding #15: Direct .write_text() in weekly_review.py bypasses VaultManager (save_review & generate).
- Finding #17: Weekly review filenames use %V (ISO 8601 Monday-first week numbering).
- Finding #18: Uppercase Markdown checkboxes (- [x] and - [X]) supported in weekly_review and CLI.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from personal_os.cli.main import app
from personal_os.core.config import Config, ModelConfig, VaultConfig
from personal_os.core.llm import get_provider
from personal_os.core.llm_openrouter import OpenRouterProvider
from personal_os.core.vault import VaultManager
from personal_os.core.weekly_review import (
    WeeklyReviewGenerator,
    _calculate_metrics,
    build_review_data,
    generate_review,
    save_review,
)


# =========================================================================== #
# Finding #9: Model Configuration in config.toml
# =========================================================================== #

class TestFinding9ModelConfig:
    def test_get_provider_with_config_object(self):
        """get_provider(config) extracts provider, model_name, and api_key from config.model."""
        config = Config()
        config.model = ModelConfig(
            provider="openrouter",
            model_name="anthropic/claude-3-haiku",
            api_key="sk-or-v1-custom-key",
            base_url="https://custom.openrouter.ai/api/v1",
        )

        provider = get_provider(config)
        assert isinstance(provider, OpenRouterProvider)
        assert provider._model == "anthropic/claude-3-haiku"
        assert provider._api_key == "sk-or-v1-custom-key"
        assert provider._base_url == "https://custom.openrouter.ai/api/v1"

    def test_get_provider_with_model_config_directly(self):
        """get_provider(config) handles ModelConfig passed directly as config."""
        model_cfg = ModelConfig(
            provider="openrouter",
            model_name="google/gemini-2.0-flash",
            api_key="key-directly-passed",
            base_url="https://openrouter.ai/api/v1",
        )

        provider = get_provider(model_cfg)
        assert isinstance(provider, OpenRouterProvider)
        assert provider._model == "google/gemini-2.0-flash"
        assert provider._api_key == "key-directly-passed"

    def test_get_provider_backward_compatibility_top_level_attrs(self):
        """Legacy objects with top-level .provider, .model_name, .api_key still work."""
        legacy_cfg = SimpleNamespace(
            provider="openrouter",
            model_name="meta-llama/llama-3-70b-instruct",
            api_key="legacy-key-123",
            base_url="https://openrouter.ai/api/v1",
        )

        provider = get_provider(legacy_cfg)
        assert isinstance(provider, OpenRouterProvider)
        assert provider._model == "meta-llama/llama-3-70b-instruct"
        assert provider._api_key == "legacy-key-123"

    def test_get_provider_top_level_model_string(self):
        """Object with top-level .model string and .provider still works."""
        legacy_cfg = SimpleNamespace(
            provider="openrouter",
            model="mistralai/mixtral-8x7b-instruct",
            api_key="key-abc",
        )

        provider = get_provider(legacy_cfg)
        assert isinstance(provider, OpenRouterProvider)
        assert provider._model == "mistralai/mixtral-8x7b-instruct"
        assert provider._api_key == "key-abc"

    def test_get_provider_dict_support(self):
        """Dictionary with nested or top-level model configuration works."""
        dict_cfg = {
            "model": {
                "provider": "openrouter",
                "model_name": "qwen/qwen-2.5-72b-instruct",
                "api_key": "dict-api-key",
            }
        }
        provider = get_provider(dict_cfg)
        assert isinstance(provider, OpenRouterProvider)
        assert provider._model == "qwen/qwen-2.5-72b-instruct"
        assert provider._api_key == "dict-api-key"

    def test_get_provider_unknown_provider_raises(self):
        """Unknown provider raises ValueError."""
        config = Config()
        config.model = ModelConfig(provider="unsupported_vendor")

        with pytest.raises(ValueError, match="Unknown LLM provider: 'unsupported_vendor'"):
            get_provider(config)


# =========================================================================== #
# Finding #14: Weekly review project_hours
# =========================================================================== #

class TestFinding14ProjectHours:
    def test_extract_hours_formats(self):
        """_extract_hours handles @2h, 2h, (1.5h), timestamps, minutes, etc."""
        extract = WeeklyReviewGenerator._extract_hours

        assert extract("Worked on router @2h today") == 2.0
        assert extract("Work on router @2.5h") == 2.5
        assert extract("Bug fix in CORDIC (1.5h)") == 1.5
        assert extract("Spent 3 hours on design") == 3.0
        assert extract("2h spent on refactoring") == 2.0
        assert extract("Meeting 10:00 - 12:30 with team") == 2.5
        assert extract("Review session 14:00 to 15:15") == 1.25
        assert extract("Quick fix @30m") == 0.5
        assert extract("Investigated 45 mins") == 0.75
        assert extract("No duration mentioned in this sentence") == 0.0

    def test_project_hours_parsed_from_project_progress(self, isolated_vault):
        """Progress.md entries with duration markers are attributed to their project."""
        vm = VaultManager(VaultConfig(root=isolated_vault))

        proj_dir = isolated_vault / "02-Projects" / "Maestro"
        proj_dir.mkdir(parents=True, exist_ok=True)
        progress_file = proj_dir / "Progress.md"
        vm.write_file(
            progress_file,
            "# Maestro Progress\n\n## Progress Log\n"
            "- **2026-07-07**: Implemented router core @3.5h\n"
            "- **2026-07-08**: Fixed SSE streaming bug (1.5h)\n"
            "- **2026-07-09**: No duration logged for this update\n",
        )

        gen = WeeklyReviewGenerator(vault=vm)
        review_date = datetime(2026, 7, 8)
        data = gen.build_review_data(date=review_date)

        assert "Maestro" in data.metrics["project_hours"]
        assert data.metrics["project_hours"]["Maestro"] == 5.0
        assert data.metrics["total_hours"] == 5.0

    def test_project_hours_parsed_from_journal_entries(self, isolated_vault):
        """Journal entries mentioning a project and duration markers attribute hours."""
        vm = VaultManager(VaultConfig(root=isolated_vault))

        proj_dir = isolated_vault / "02-Projects" / "CORDIC"
        proj_dir.mkdir(parents=True, exist_ok=True)

        journal_dir = isolated_vault / "04-Personal" / "Journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        vm.write_file(
            journal_dir / "2026-07-08.md",
            "# 2026-07-08\n\n"
            "## 10:00 - [Source: Text]\n"
            "Worked on CORDIC pipeline optimizations @2h\n\n"
            "## 15:00 - [Source: Text]\n"
            "CORDIC timing analysis 14:00 - 15:30\n",
        )

        gen = WeeklyReviewGenerator(vault=vm)
        data = gen.build_review_data(date=datetime(2026, 7, 8))

        assert "CORDIC" in data.metrics["project_hours"]
        assert data.metrics["project_hours"]["CORDIC"] == 3.5
        assert data.metrics["total_hours"] == 3.5

    def test_no_fabrication_when_no_duration_markers(self, isolated_vault):
        """When no duration markers exist, project_hours is not fabricated."""
        vm = VaultManager(VaultConfig(root=isolated_vault))

        proj_dir = isolated_vault / "02-Projects" / "SilentProject"
        proj_dir.mkdir(parents=True, exist_ok=True)
        vm.write_file(
            proj_dir / "Progress.md",
            "# SilentProject Progress\n\n## Progress Log\n"
            "- **2026-07-08**: Updated README without hours\n",
        )

        journal_dir = isolated_vault / "04-Personal" / "Journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        vm.write_file(
            journal_dir / "2026-07-08.md",
            "# 2026-07-08\n\n## 09:00 - [Source: Text]\nReviewed PR for SilentProject\n",
        )

        gen = WeeklyReviewGenerator(vault=vm)
        data = gen.build_review_data(date=datetime(2026, 7, 8))

        assert data.metrics["project_hours"] == {}
        assert data.metrics["total_hours"] == 0.0

    def test_calculate_metrics_function(self, isolated_vault):
        """_calculate_metrics calculates hours and metrics accurately."""
        vm = VaultManager(VaultConfig(root=isolated_vault))
        gen = WeeklyReviewGenerator(vault=vm)

        entries = [
            {
                "date": "2026-07-08",
                "content": "## 10:00\nWorked on [[AppX]] for 2h\nwe decided to use Rust",
            }
        ]
        metrics = gen._calculate_metrics(entries)

        assert "AppX" in metrics["project_hours"]
        assert metrics["project_hours"]["AppX"] == 2.0
        assert metrics["decision_count"] == 1


# =========================================================================== #
# Finding #15: Direct .write_text() bypasses VaultManager
# =========================================================================== #

class TestFinding15VaultManagerWrite:
    def test_save_review_uses_vault_write_file(self, isolated_vault):
        """save_review calls self.vault.write_file with commit message."""
        vm = VaultManager(VaultConfig(root=isolated_vault))
        vm.write_file = MagicMock(wraps=vm.write_file)

        gen = WeeklyReviewGenerator(vault=vm)
        review_file = gen.reviews_dir / "2026-W28.md"
        content = "# Weekly Review Content\n"

        result_path = gen.save_review(review_file, content)

        assert result_path == review_file
        vm.write_file.assert_called_once_with(
            review_file,
            content,
            message=f"Weekly review: {review_file.stem}",
        )

    def test_generate_calls_vault_write_file(self, isolated_vault):
        """generate() orchestrates review creation via VaultManager.write_file."""
        vm = VaultManager(VaultConfig(root=isolated_vault))
        vm.write_file = MagicMock(wraps=vm.write_file)

        gen = WeeklyReviewGenerator(vault=vm)
        out_file = gen.generate(date=datetime(2026, 7, 8))

        assert out_file.exists()
        vm.write_file.assert_called_once()
        call_args, call_kwargs = vm.write_file.call_args
        assert call_args[0] == out_file
        assert "Weekly review:" in call_kwargs.get("message", "")


# =========================================================================== #
# Finding #17: Weekly review filenames use %V (ISO 8601 Monday-first)
# =========================================================================== #

class TestFinding17ISOWeekNumbering:
    def test_week_number_uses_percent_v_not_percent_u(self, isolated_vault):
        """On weeks where %U and %V differ (e.g. 2026-01-05 Monday), %V is used."""
        vm = VaultManager(VaultConfig(root=isolated_vault))
        gen = WeeklyReviewGenerator(vault=vm)

        # 2026-01-05 is a Monday:
        # %U produces '01' (first Sunday of 2026 was Jan 4)
        # %V produces '02' (first week had >= 4 days in 2026)
        test_monday = datetime(2026, 1, 5)
        assert test_monday.strftime("%Y-W%U") == "2026-W01"
        assert test_monday.strftime("%Y-W%V") == "2026-W02"

        out_path = gen.generate(date=test_monday)
        assert out_path.name == "2026-W02.md"
        assert out_path.name != "2026-W01.md"

    def test_midyear_iso_week_filename(self, isolated_vault):
        """July 6, 2026 is Monday of ISO Week 28."""
        vm = VaultManager(VaultConfig(root=isolated_vault))
        gen = WeeklyReviewGenerator(vault=vm)

        test_monday = datetime(2026, 7, 6)
        out_path = gen.generate(date=test_monday)
        assert out_path.name == "2026-W28.md"


# =========================================================================== #
# Finding #18: Uppercase Markdown checkboxes (- [x] and - [X])
# =========================================================================== #

class TestFinding18CheckboxCaseInsensitivity:
    def test_weekly_review_detects_both_lowercase_and_uppercase_checkboxes(
        self, isolated_vault
    ):
        """collect_completed_tasks collects both - [x] and - [X]."""
        vm = VaultManager(VaultConfig(root=isolated_vault))

        proj_dir = isolated_vault / "02-Projects" / "TestProject"
        proj_dir.mkdir(parents=True, exist_ok=True)
        tasks_file = proj_dir / "Tasks.md"
        vm.write_file(
            tasks_file,
            "# Tasks\n\n"
            "## Active Tasks\n"
            "- [x] Completed task lowercase\n"
            "- [X] Completed task uppercase\n"
            "- [ ] Open task pending\n",
        )

        gen = WeeklyReviewGenerator(vault=vm)
        monday, sunday = gen.get_week_range(datetime(2026, 7, 8))
        completed = gen.collect_completed_tasks(monday, sunday)

        tasks_text = [t["task"] for t in completed]
        assert "- [x] Completed task lowercase" in tasks_text
        assert "- [X] Completed task uppercase" in tasks_text
        assert not any("Open task pending" in t for t in tasks_text)

    def test_cli_tasks_command_recognizes_uppercase_checkbox(self, isolated_vault):
        """CLI tasks command marks both - [x] and - [X] as done."""
        vm = VaultManager(VaultConfig(root=isolated_vault))

        proj_dir = isolated_vault / "02-Projects" / "Alpha"
        proj_dir.mkdir(parents=True, exist_ok=True)
        vm.write_file(
            proj_dir / "Tasks.md",
            "# Tasks\n\n"
            "- [x] First lowercase done\n"
            "- [X] Second uppercase done\n"
            "- [ ] Third open task\n",
        )

        runner = CliRunner()
        # Filter status=done
        res_done = runner.invoke(app, ["tasks", "--status", "done"])
        assert res_done.exit_code == 0
        assert "First lowercase done" in res_done.stdout
        assert "Second uppercase done" in res_done.stdout
        assert "Third open task" not in res_done.stdout

        # Filter status=open
        res_open = runner.invoke(app, ["tasks", "--status", "open"])
        assert res_open.exit_code == 0
        assert "Third open task" in res_open.stdout
        assert "First lowercase done" not in res_open.stdout
        assert "Second uppercase done" not in res_open.stdout
