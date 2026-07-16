"""Background processor - extracts knowledge from entries automatically."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from personal_os.core.vault import get_vault, VaultManager
from personal_os.core.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    """Result of processing an entry."""
    extractions: List[Dict[str, Any]] = field(default_factory=list)
    changes_made: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class KnowledgeExtractor:
    """Extracts structured knowledge from natural language entries."""
    
    def __init__(self, vault: VaultManager):
        self.vault = vault
        self._compile_patterns()
    
    def _compile_patterns(self):
        """Compile regex patterns for extraction."""
        import re
        self.patterns = {
            'progress': [
                re.compile(r'(\d+(?:\.\d+)?)\s*hours?\s+(?:on|working on|fixing|developing|building)\s+([^.]+)', re.IGNORECASE),
                re.compile(r'(?:spent|worked)\s+(\d+(?:\.\d+)?)\s*hours?\s+(?:on|with)\s+([^.]+)', re.IGNORECASE),
                re.compile(r'(\d+(?:\.\d+)?)\s*hrs?\s+([^.]+)', re.IGNORECASE),
            ],
            'decision': [
                re.compile(r'(?:we|i|team)\s+(?:decided|decided to|chose to|switched to|will use|going with)\s+([^.]+)', re.IGNORECASE),
                re.compile(r'(?:decision|choice):\s*([^.]+)', re.IGNORECASE),
                re.compile(r'(?:we|i)\s+(?:agreed|concluded)\s+(?:that\s+)?([^.]+)', re.IGNORECASE),
            ],
            'completion': [
                re.compile(r'(?:finished|completed|fixed|done with|wrapped up|shipped)\s+([^.]+)', re.IGNORECASE),
                re.compile(r'(?:finally\s+)?(?:got\s+)?(?:it\s+)?working', re.IGNORECASE),
                re.compile(r'(?:closed|resolved|merged)\s+([^.]+)', re.IGNORECASE),
            ],
            'learning': [
                re.compile(r'(?:study|learning|studying|reading|practicing)\s+([^.]+)', re.IGNORECASE),
                re.compile(r'(?:learned|figured out)\s+(?:how\s+)?(?:to\s+)?([^.]+)', re.IGNORECASE),
            ],
            'deadline': [
                re.compile(r'(?:exam|deadline|due|assignment|milestone)\s+(?:on\s+)?(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}(?:st|nd|rd|th)?\s+\w+)', re.IGNORECASE),
                re.compile(r'(?:by|before)\s+(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)', re.IGNORECASE),
            ],
            'observation': [
                re.compile(r'(?:i think|i wonder|maybe|might be|seems like|looks like)\s+([^.]+)', re.IGNORECASE),
                re.compile(r'(?:idea|thought|observation):\s*([^.]+)', re.IGNORECASE),
                re.compile(r'(?:note|fyi):\s*([^.]+)', re.IGNORECASE),
            ],
            'task': [
                re.compile(r'(?:need to|should|must|have to|todo:)\s+([^.]+)', re.IGNORECASE),
                re.compile(r'(?:action item|follow up):\s*([^.]+)', re.IGNORECASE),
            ],
        }
        
        # Project aliases for fuzzy matching (alias -> canonical project name)
        # Real project names are also matched below as a fallback.
        self.project_aliases = {
            "maestro": "Maestro",
            "router": "Maestro",
            "drone": "Drone",
            "blindguard": "BlindGuard",
            "vlsi": "VLSI",
            "verilog": "VLSI",
            "fpga": "FPGA",
            "namo": "Team-NAMO",
            "team namo": "Team-NAMO",
        }

    def _identify_project(self, text: str) -> Optional[str]:
        """Identify project from text."""
        text_lower = text.lower()
        
        # Check aliases
        for alias, project in self.project_aliases.items():
            if alias in text_lower:
                return project
        
        # Check actual project names
        for project in self.vault.list_projects():
            if project['name'].lower() in text_lower:
                return project['name']
        
        return None
    
    def extract(self, text: str) -> List[Dict[str, Any]]:
        """Extract all knowledge from text."""
        extractions = []
        
        for ext_type, patterns in self.patterns.items():
            for pattern in patterns:
                matches = pattern.findall(text)
                for match in matches:
                    if isinstance(match, tuple):
                        primary = match[0] if match else ""
                    else:
                        primary = match
                    
                    project = self._identify_project(text)
                    
                    extractions.append({
                        'type': ext_type,
                        'project': project,
                        'description': primary.strip() if isinstance(primary, str) else str(primary).strip(),
                        'original': text,
                        'metadata': {}
                    })
        
        # Deduplicate
        return self._deduplicate(extractions)
    
    def _deduplicate(self, extractions: List[Dict]) -> List[Dict]:
        seen = set()
        unique = []
        for ext in extractions:
            key = (ext['type'], ext.get('project'), ext['description'][:50].lower())
            if key not in seen:
                seen.add(key)
                unique.append(ext)
        return unique


class KnowledgeApplier:
    """Applies extractions to vault files."""
    
    def __init__(self, vault: VaultManager):
        self.vault = vault
    
    def apply(self, extraction: Dict[str, Any]) -> List[str]:
        """Apply single extraction, return changes made."""
        ext_type = extraction['type']
        
        try:
            if ext_type == 'progress':
                return self._apply_progress(extraction)
            elif ext_type == 'decision':
                return self._apply_decision(extraction)
            elif ext_type == 'completion':
                return self._apply_completion(extraction)
            elif ext_type == 'learning':
                return self._apply_learning(extraction)
            elif ext_type == 'deadline':
                return self._apply_deadline(extraction)
            elif ext_type == 'observation':
                return self._apply_observation(extraction)
            elif ext_type == 'task':
                return self._apply_task(extraction)
        except Exception as e:
            logger.error(f"Failed to apply extraction: {e}")
            return [f"❌ Error applying {ext_type}: {e}"]
        
        return []
    
    def _apply_progress(self, ext: Dict) -> List[str]:
        project = ext.get('project')
        if not project:
            return ["⚠️ No project identified for progress update"]
        
        project_dir = self.vault.config.projects_dir / project
        if not project_dir.exists():
            return [f"⚠️ Project '{project}' not found"]
        
        progress_file = project_dir / "Progress.md"
        timestamp = datetime.now().strftime("%Y-%m-%d")
        
        desc = ext['description']
        entry = f"- **{timestamp}**: {desc}\n"
        
        if progress_file.exists():
            content = self.vault.read_file(progress_file)
            if "## Progress Log" in content:
                content = content.replace("## Progress Log", f"## Progress Log\n{entry}")
            else:
                content += f"\n## Progress Log\n{entry}"
        else:
            content = f"# {project} Progress\n\n## Progress Log\n{entry}"
        
        self.vault.write_file(progress_file, content, message=f"Progress: {desc[:50]}")
        return [f"✅ Updated {project}/Progress.md: {desc[:60]}"]
    
    def _apply_decision(self, ext: Dict) -> List[str]:
        project = ext.get('project')
        if not project:
            return ["⚠️ No project identified for decision"]
        
        project_dir = self.vault.config.projects_dir / project
        if not project_dir.exists():
            return [f"⚠️ Project '{project}' not found"]
        
        for target in ["Architecture.md", "Decisions.md", "README.md"]:
            target_path = project_dir / target
            if target_path.exists() or target == "Architecture.md":
                break
        
        timestamp = datetime.now().strftime("%Y-%m-%d")
        decision_entry = f"\n## Decision Log\n- **{timestamp}**: {ext['description']}\n  - Context: Extracted from daily stream\n  - Decision: {ext['description']}\n  - Rationale: [To be filled]\n"
        
        if target_path.exists():
            content = self.vault.read_file(target_path)
            if "## Decision Log" in content:
                content = content.replace("## Decision Log", f"## Decision Log{decision_entry}")
            else:
                content += decision_entry
        else:
            content = f"# {project} Architecture\n\n{decision_entry}"
        
        self.vault.write_file(target_path, content, message=f"Decision: {ext['description'][:50]}")
        return [f"✅ Added decision to {project}/{target_path.name}"]
    
    def _apply_completion(self, ext: Dict) -> List[str]:
        project = ext.get('project')
        if not project:
            return ["⚠️ No project identified for completion"]
        
        project_dir = self.vault.config.projects_dir / project
        if not project_dir.exists():
            return [f"⚠️ Project '{project}' not found"]
        
        tasks_file = project_dir / "Tasks.md"
        if not tasks_file.exists():
            return [f"⚠️ No Tasks.md found in {project}"]
        
        content = self.vault.read_file(tasks_file)
        desc = ext['description'].lower()
        
        lines = content.split('\n')
        modified = False
        for i, line in enumerate(lines):
            if line.strip().startswith("- [ ]") and any(word in line.lower() for word in desc.split()[:3]):
                lines[i] = line.replace("- [ ]", "- [x]")
                modified = True
                break
        
        if modified:
            self.vault.write_file(tasks_file, '\n'.join(lines), message=f"Complete task: {ext['description'][:50]}")
            return [f"✅ Marked task complete in {project}/Tasks.md"]
        
        return [f"ℹ️ No matching task found to complete in {project}"]
    
    def _apply_learning(self, ext: Dict) -> List[str]:
        topic = ext['description']
        skill_area = self._categorize_learning(topic)
        skill_dir = self.vault.config.skills_dir / skill_area
        skill_dir.mkdir(parents=True, exist_ok=True)
        
        progress_file = skill_dir / "Progress.md"
        timestamp = datetime.now().strftime("%Y-%m-%d")
        entry = f"- **{timestamp}**: Studied {topic}\n"
        
        if progress_file.exists():
            content = self.vault.read_file(progress_file)
            if "## Learning Log" in content:
                content = content.replace("## Learning Log", f"## Learning Log\n{entry}")
            else:
                content += f"\n## Learning Log\n{entry}"
        else:
            content = f"# {skill_area} Learning Progress\n\n## Learning Log\n{entry}"
        
        self.vault.write_file(progress_file, content, message=f"Learning: {topic[:50]}")
        return [f"✅ Added learning entry to {skill_area}/Progress.md"]
    
    def _categorize_learning(self, topic: str) -> str:
        topic_lower = topic.lower()
        if any(kw in topic_lower for kw in ["verilog", "systemverilog", "hdl", "rtl", "vlsi", "asic", "fpga", "vivado", "vitis"]):
            return "VLSI"
        elif any(kw in topic_lower for kw in ["embedded", "stm32", "esp32", "microcontroller", "rtos", "firmware", "c++", "c programming"]):
            return "Embedded"
        elif any(kw in topic_lower for kw in ["linux", "bash", "shell", "nix", "nixos", "docker", "kubernetes"]):
            return "Linux"
        elif any(kw in topic_lower for kw in ["python", "javascript", "typescript", "react", "web", "api", "database"]):
            return "Software"
        elif any(kw in topic_lower for kw in ["math", "calculus", "linear algebra", "signal", "dsp", "control"]):
            return "Mathematics"
        return "General"
    
    def _apply_deadline(self, ext: Dict) -> List[str]:
        return [f"📅 Deadline noted: {ext['description']} (manual task creation needed)"]
    
    def _apply_observation(self, ext: Dict) -> List[str]:
        project = ext.get('project')
        if not project:
            return ["💭 Observation recorded (no project context)"]
        
        project_dir = self.vault.config.projects_dir / project
        if not project_dir.exists():
            return [f"💭 Observation recorded (project '{project}' not found)"]
        
        notes_file = project_dir / "Notes.md"
        timestamp = datetime.now().strftime("%Y-%m-%d")
        entry = f"- **{timestamp}**: {ext['description']} #observation\n"
        
        if notes_file.exists():
            content = self.vault.read_file(notes_file)
            content += f"\n{entry}"
        else:
            content = f"# {project} Notes\n\n{entry}"
        
        self.vault.write_file(notes_file, content, message=f"Observation: {ext['description'][:50]}")
        return [f"💭 Added observation to {project}/Notes.md"]
    
    def _apply_task(self, ext: Dict) -> List[str]:
        project = ext.get('project')
        if not project:
            # Create in Inbox
            inbox_file = self.vault.config.inbox_dir / "Tasks.md"
            inbox_file.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d")
            entry = f"- [ ] **{timestamp}**: {ext['description']}\n"
            
            if inbox_file.exists():
                content = self.vault.read_file(inbox_file)
                content += f"\n{entry}"
            else:
                content = f"# Inbox Tasks\n\n{entry}"
            
            self.vault.write_file(inbox_file, content, message=f"Inbox task: {ext['description'][:50]}")
            return [f"📥 Added task to Inbox: {ext['description'][:60]}"]
        
        # Add to project tasks
        tasks_file = self.vault.config.projects_dir / project / "Tasks.md"
        if not tasks_file.exists():
            return [f"⚠️ No Tasks.md found in {project}"]
        
        content = self.vault.read_file(tasks_file)
        if "## Active Tasks" in content:
            content = content.replace("## Active Tasks", f"## Active Tasks\n- [ ] {ext['description']}")
        else:
            content += f"\n## Active Tasks\n- [ ] {ext['description']}\n"
        
        self.vault.write_file(tasks_file, content, message=f"New task: {ext['description'][:50]}")
        return [f"📝 Added task to {project}/Tasks.md"]


class BackgroundProcessor:
    """Background processor that handles entry processing asynchronously."""
    
    def __init__(self, vault: VaultManager):
        self.vault = vault
        self.extractor = KnowledgeExtractor(vault)
        self.applier = KnowledgeApplier(vault)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self):
        """Start the background worker."""
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("Background processor started")
    
    async def stop(self):
        """Stop the background worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Background processor stopped")
    
    async def process(self, text: str, source: str = "Text") -> ProcessingResult:
        """Process an entry immediately (for TUI)."""
        return await self._process_entry(text, source)
    
    async def queue(self, text: str, source: str = "Text"):
        """Queue an entry for background processing."""
        await self._queue.put((text, source))
    
    async def _worker(self):
        """Background worker loop."""
        while self._running:
            try:
                text, source = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._process_entry(text, source)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Background processor error: {e}")
    
    async def _process_entry(self, text: str, source: str) -> ProcessingResult:
        """Process a single entry."""
        result = ProcessingResult()
        
        # Extract knowledge
        extractions = self.extractor.extract(text)
        result.extractions = extractions
        
        # Apply each extraction
        for ext in extractions:
            changes = self.applier.apply(ext)
            result.changes_made.extend(changes)
        
        return result


async def process_entry(text: str, source: str = "Text") -> ProcessingResult:
    """Convenience function to process an entry."""
    vault = get_vault()
    processor = BackgroundProcessor(vault)
    return await processor.process(text, source)