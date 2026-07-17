"""Personal OS - CLI entry point with TUI support."""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from personal_os.core.config import get_config
from personal_os.core.vault import get_vault
from personal_os.core.processor import process_entry
from personal_os.core import links as link_service
from personal_os.core import weekly_review as review_service

console = Console()

app = typer.Typer(
    name="pos",
    help="Personal OS - Terminal-native personal operating system",
    add_completion=False,
    no_args_is_help=False,  # We'll handle no args to launch TUI
)


def launch_tui():
    """Launch the TUI application."""
    from personal_os.tui.app import PersonalOSTUI
    
    try:
        tui_app = PersonalOSTUI()
        tui_app.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Goodbye![/yellow]")
    except Exception as e:
        console.print(f"[red]TUI error: {e}[/red]")
        console.print("[dim]Run with --help for CLI commands[/dim]")
        sys.exit(1)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    tui: bool = typer.Option(False, "--tui/--no-tui", help="Launch TUI (default when no command)"),
    version: bool = typer.Option(False, "--version", "-v", help="Show version"),
):
    """Personal OS - Your terminal-native personal operating system.
    
    Run without arguments to launch the TUI.
    Use subcommands for quick actions.
    """
    if version:
        from personal_os import __version__
        console.print(f"Personal OS v{__version__}")
        raise typer.Exit()
    
    # No subcommand invoked - launch TUI by default
    if ctx.invoked_subcommand is None:
        launch_tui()


# ========== Quick Capture Commands ==========

@app.command()
def note(
    text: str = typer.Argument(..., help="Text to capture"),
    source: str = typer.Option("Text", "--source", "-s", help="Source type"),
):
    """Quick capture a note (background, no TUI)."""
    from personal_os.core.capture import capture_and_process

    async def _capture():
        # Capture contract: persist raw FIRST, then process.
        receipt, result = await capture_and_process(text, source=source)
        if not receipt.persisted:
            console.print(f"[red]❌ Capture failed: {receipt.error}[/red]")
            raise typer.Exit(1)
        console.print(f"✅ Logged to [cyan]{receipt.journal_path.name}[/cyan]")

        if result is None:
            console.print("[yellow]⚠️ Processing failed (raw capture is safe)[/yellow]")
        elif result.changes_made:
            console.print("🔄 Auto-processed:")
            for change in result.changes_made:
                console.print(f"   {change}")
        else:
            console.print("📝 No actionable items extracted")

    asyncio.run(_capture())


@app.command()
def voice(
    duration: int = typer.Option(10, "--duration", "-d", help="Recording duration in seconds"),
    model: str = typer.Option("base", "--model", "-m", help="Whisper model"),
    continuous: bool = typer.Option(False, "--continuous", "-c", help="Continuous capture"),
    language: str = typer.Option("en", "--language", "-l", help="Language code"),
):
    """Capture voice input."""
    from personal_os.skills.voice_capture import capture_voice
    asyncio.run(capture_voice(duration=duration, model=model, continuous=continuous, language=language))


# ========== View Commands ==========

@app.command()
def today():
    """Show today's journal."""
    vault = get_vault()
    from datetime import datetime
    journal_path = vault.get_journal_path(datetime.now())
    
    if journal_path.exists():
        console.print(journal_path.read_text())
    else:
        console.print("📭 No entries for today yet.")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Filter by project"),
    file_type: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by file type"),
):
    """Search the vault."""
    from personal_os.core.search import search as do_search

    # Translate --project/--type flags into core.search query syntax
    augmented = query
    if project:
        augmented = f"project:{project} {augmented}"
    if file_type:
        augmented = f"type:{file_type} {augmented}"

    results = asyncio.run(do_search(augmented, limit=limit))
    
    if not results:
        console.print(f"🔍 Search: [cyan]{query}[/cyan]\n📭 No matches found.")
        return
    
    console.print(f"🔍 Search: [cyan]{query}[/cyan]")
    console.print(f"📄 {len(results)} matches\n")
    
    from collections import defaultdict
    by_file = defaultdict(list)
    for r in results:
        by_file[r.file].append(r)
    
    for file_path, matches in by_file.items():
        console.print(f"📄 [bold]{file_path}[/bold]")
        for m in matches[:3]:
            console.print(f"   L{m.line}: {m.content[:100]}")
        if len(matches) > 3:
            console.print(f"   [dim]... and {len(matches) - 3} more matches[/dim]")
        console.print()


@app.command()
def projects(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status"),
):
    """List projects."""
    vault = get_vault()
    projects = vault.list_projects()
    
    if status:
        projects = [p for p in projects if p.get('status') == status]
    
    if not projects:
        console.print("📭 No projects found.")
        return
    
    from rich.table import Table
    table = Table(title="Projects")
    table.add_column("Name", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Health", style="yellow")
    table.add_column("Open Tasks", justify="right")
    table.add_column("Hours", justify="right")
    table.add_column("Last Activity")
    
    for p in projects:
        health_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(p.get('health', 'green'), "⚪")
        table.add_row(
            p['name'],
            p.get('status', 'active'),
            health_icon,
            str(p.get('open_tasks', 0)),
            f"{p.get('total_hours', 0):.1f}",
            p.get('last_activity', 'N/A') or 'N/A',
        )
    
    console.print(table)


@app.command()
def tasks(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Filter by project"),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status (open/done)"),
):
    """List tasks across projects."""
    vault = get_vault()
    
    if project:
        project_dir = vault.config.projects_dir / project
        tasks_file = project_dir / "Tasks.md"
        if not tasks_file.exists():
            console.print(f"📭 No tasks for {project}")
            return
        files = [tasks_file]
    else:
        # Find all Tasks.md files
        files = list(vault.config.projects_dir.glob("*/Tasks.md"))
    
    all_tasks = []
    for f in files:
        content = vault.read_file(f)
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if line.strip().startswith("- [") and "]" in line:
                task_text = line.strip()[5:]  # Remove "- [x] " or "- [ ] "
                done = line.strip().startswith("- [x]")
                all_tasks.append({
                    'project': f.parent.name,
                    'text': task_text,
                    'done': done,
                    'line': i + 1,
                })
    
    if status == "open":
        all_tasks = [t for t in all_tasks if not t['done']]
    elif status == "done":
        all_tasks = [t for t in all_tasks if t['done']]
    
    if not all_tasks:
        console.print("📭 No tasks found.")
        return
    
    from rich.table import Table
    table = Table(title="Tasks")
    table.add_column("Project", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Task")
    
    for t in all_tasks:
        table.add_row(t['project'], "✅ Done" if t['done'] else "☐ Open", t['text'])
    
    console.print(table)


# ========== Project Management ==========

@app.command()
def new_project(
    name: str = typer.Argument(..., help="Project name"),
    description: str = typer.Option("", "--description", "-d", help="Project description"),
    phase: str = typer.Option("planning", "--phase", help="Initial phase"),
):
    """Create a new project."""
    vault = get_vault()
    try:
        project_dir = vault.create_project(name, description, phase)
        console.print(f"✅ Created project [cyan]{name}[/cyan] at [dim]{project_dir}[/dim]")
    except FileExistsError:
        console.print(f"[red]Project '{name}' already exists[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed to create project: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def open_project(name: str = typer.Argument(..., help="Project name")):
    """Open project in TUI (focuses Projects panel)."""
    console.print(f"Opening project '{name}' in TUI...")
    # This would need TUI integration - for now just show info
    vault = get_vault()
    files = vault.get_project_files(name)
    
    console.print(f"\n📁 Project: [cyan]{name}[/cyan]")
    for fname, fpath in files.items():
        status = "✅" if fpath.exists() else "⭕"
        console.print(f"  {status} {fname}: [dim]{fpath}[/dim]")


# ========== Knowledge Graph ==========

@app.command()
def links(
    action: str = typer.Argument("suggest", help="suggest | related | orphans | hub"),
    query: str = typer.Argument("", help="Topic/query (for suggest/related/hub)"),
    limit: int = typer.Option(10, "--limit", "-l", help="Max results"),
):
    """Knowledge-graph operations on the vault (suggest/related/orphans/hub)."""
    import json

    try:
        if action == "suggest":
            if not query:
                console.print("[red]Provide a query: pos links suggest \"topic\"[/red]")
                raise typer.Exit(1)
            results = link_service.suggest_links(query, limit=limit)
        elif action == "related":
            if not query:
                console.print("[red]Provide a query: pos links related \"topic\"[/red]")
                raise typer.Exit(1)
            results = link_service.find_related(query, limit=limit)
        elif action == "orphans":
            results = link_service.find_orphans(limit=limit)
        elif action == "hub":
            if not query:
                console.print("[red]Provide a topic: pos links hub \"topic\"[/red]")
                raise typer.Exit(1)
            hub_path = link_service.create_hub(query)
            console.print(f"✅ Created hub: [cyan]{hub_path}[/cyan]")
            return
        else:
            console.print(f"[red]Unknown action: {action}[/red]")
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Link operation failed: {e}[/red]")
        raise typer.Exit(1)

    if not results:
        console.print("📭 No results.")
        return

    console.print(json.dumps(results, indent=2, default=str))


# ========== Weekly Review ==========

@app.command()
def weekly_review():
    """Generate the weekly review from the vault (overwrites same week's file)."""
    try:
        path = review_service.generate_review()
        console.print(f"✅ Weekly review generated: [cyan]{path.name}[/cyan]")
        console.print(f"   [dim]{path}[/dim]")
    except Exception as e:
        console.print(f"[red]Review generation failed: {e}[/red]")
        raise typer.Exit(1)


@app.command("review")
def review_alias():
    """Alias for 'weekly-review'."""
    weekly_review()


# ========== Maintenance ==========

@app.command()
def process(
    today: bool = typer.Option(True, "--today/--all", help="Process today or all journals"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be done"),
):
    """Re-process journal entries."""
    vault = get_vault()
    
    if today:
        from datetime import datetime
        journal_path = vault.get_journal_path(datetime.now())
        if journal_path.exists():
            content = vault.read_file(journal_path)
            console.print(f"🔄 Processing today's journal...")
            result = asyncio.run(process_entry(content, "Reprocess"))
            for change in result.changes_made:
                console.print(f"   {change}")
        else:
            console.print("📭 No journal for today")
    else:
        console.print("🔄 Full reprocess not yet implemented")


@app.command()
def init(
    path: Optional[Path] = typer.Option(None, "--path", help="Vault path (default: ~/projects/personal-os)"),
):
    """Initialize Personal OS vault."""
    config = get_config()
    
    if path:
        config.vault.root = path.expanduser()
    
    vault = get_vault(config.vault)
    config.save()
    
    console.print(f"✅ Personal OS initialized at [cyan]{config.vault.root}[/cyan]")
    console.print("\nNext steps:")
    console.print("  pos              # Launch TUI")
    console.print("  pos note \"...\"   # Quick capture")
    console.print("  pos voice        # Voice capture")


# ========== Config ==========

@app.command()
def config(
    show: bool = typer.Option(False, "--show", help="Show current config"),
    edit: bool = typer.Option(False, "--edit", help="Edit config in $EDITOR"),
):
    """Manage configuration."""
    config = get_config()
    
    if show:
        import tomli_w
        console.print(tomli_w.dumps(config._to_dict()))
    
    if edit:
        import subprocess
        subprocess.run([os.environ.get("EDITOR", "vim"), str(config.config_path)])


if __name__ == "__main__":
    app()