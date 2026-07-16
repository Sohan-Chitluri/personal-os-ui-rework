"""Configuration management for Personal OS TUI."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

# Python 3.11+ has tomllib in stdlib
if sys.version_info >= (3, 11):
    import tomllib as tomli
else:
    import tomli
import tomli_w


@dataclass
class VaultConfig:
    """Vault location and structure."""
    root: Path = field(default_factory=lambda: Path.home() / "projects" / "personal-os")
    
    @property
    def journal_dir(self) -> Path:
        return self.root / "04-Personal" / "Journal"
    
    @property
    def projects_dir(self) -> Path:
        return self.root / "02-Projects"
    
    @property
    def skills_dir(self) -> Path:
        return self.root / "03-Skills"
    
    @property
    def templates_dir(self) -> Path:
        return self.root / "Templates"
    
    @property
    def inbox_dir(self) -> Path:
        return self.root / "00-Inbox"


@dataclass
class ModelConfig:
    """LLM model configuration."""
    provider: str = "local"  # local, openai, anthropic, openrouter
    model_name: str = "llama-3.1-8b-instruct"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.3
    max_tokens: int = 4096
    context_window: int = 8192


@dataclass
class VoiceConfig:
    """Voice capture configuration."""
    enabled: bool = True
    engine: str = "whisper-cpp"  # whisper-cpp, openai-whisper, faster-whisper
    model: str = "base"
    language: str = "en"
    sample_rate: int = 16000
    vad_threshold: float = 0.5
    silence_duration: float = 1.5
    model_dir: Path = field(default_factory=lambda: Path.home() / ".local" / "share" / "personal-os" / "models")


@dataclass
class SearchConfig:
    """Search configuration."""
    engine: str = "tantivy"  # tantivy, ripgrep, python
    index_path: Path = field(default_factory=lambda: Path.home() / ".cache" / "personal-os" / "search_index")
    auto_index: bool = True
    max_results: int = 50


@dataclass
class UIConfig:
    """UI/TUI configuration."""
    theme: str = "dark"  # dark, light, auto
    sidebar_width: int = 25  # percentage
    show_line_numbers: bool = False
    compact_mode: bool = False
    animations: bool = True
    vim_mode: bool = True


@dataclass
class KeyBindings:
    """Key binding configuration."""
    # Global
    quit: str = "ctrl+q"
    command_palette: str = "ctrl+p"
    toggle_sidebar: str = "ctrl+b"
    next_panel: str = "tab"
    prev_panel: str = "shift+tab"
    
    # Chat
    send_message: str = "enter"
    new_line: str = "shift+enter"
    clear_chat: str = "ctrl+l"
    scroll_up: str = "ctrl+u"
    scroll_down: str = "ctrl+d"
    
    # Navigation
    focus_chat: str = "ctrl+1"
    focus_sidebar: str = "ctrl+2"
    focus_input: str = "ctrl+i"
    
    # Panels (1-6)
    panel_projects: str = "f1"
    panel_tasks: str = "f2"
    panel_journal: str = "f3"
    panel_search: str = "f4"
    panel_calendar: str = "f5"
    panel_settings: str = "f6"
    
    # Voice
    start_voice: str = "ctrl+v"
    stop_voice: str = "ctrl+v"
    
    # Actions
    search: str = "ctrl+f"
    new_task: str = "ctrl+n"
    new_project: str = "ctrl+shift+n"


@dataclass
class Config:
    """Main configuration."""
    vault: VaultConfig = field(default_factory=VaultConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    keys: KeyBindings = field(default_factory=KeyBindings)
    
    # Runtime
    config_path: Path = field(default_factory=lambda: Path.home() / ".config" / "personal-os" / "config.toml", init=False)
    
    @classmethod
    def load(cls) -> Config:
        """Load configuration from file, with defaults."""
        config = cls()
        config.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        if config.config_path.exists():
            with open(config.config_path, "rb") as f:
                data = tomli.load(f)
            config = cls._merge(config, data)
        
        # Environment variable overrides
        config._apply_env_overrides()
        
        return config
    
    @classmethod
    def _merge(cls, config: Config, data: dict) -> Config:
        """Recursively merge TOML data into config."""
        for key, value in data.items():
            if hasattr(config, key):
                attr = getattr(config, key)
                if hasattr(attr, '__dataclass_fields__'):  # nested dataclass
                    if isinstance(value, dict):
                        setattr(config, key, cls._merge(attr, value))
                elif isinstance(value, dict):
                    # Handle Path fields
                    if key == "vault" and "root" in value:
                        config.vault.root = Path(value["root"]).expanduser()
                    elif key == "voice" and "model_dir" in value:
                        config.voice.model_dir = Path(value["model_dir"]).expanduser()
                    elif key == "search" and "index_path" in value:
                        config.search.index_path = Path(value["index_path"]).expanduser()
                else:
                    # Handle Path fields
                    if isinstance(attr, Path):
                        setattr(config, key, Path(value).expanduser())
                    else:
                        setattr(config, key, value)
        return config
    
    def _apply_env_overrides(self):
        """Apply environment variable overrides."""
        env_map = {
            "PERSONAL_OS_VAULT": ("vault", "root"),
            "PERSONAL_OS_MODEL": ("model", "model_name"),
            "PERSONAL_OS_API_KEY": ("model", "api_key"),
            "PERSONAL_OS_BASE_URL": ("model", "base_url"),
            "WHISPER_CPP_PATH": ("voice", "engine"),  # handled specially
        }
        
        for env_var, (section, field) in env_map.items():
            value = os.environ.get(env_var)
            if value:
                obj = getattr(self, section)
                if isinstance(getattr(obj, field), Path):
                    setattr(obj, field, Path(value).expanduser())
                else:
                    setattr(obj, field, value)
    
    def save(self):
        """Save configuration to file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert to serializable dict
        data = self._to_dict()
        
        with open(self.config_path, "wb") as f:
            tomli_w.dump(data, f)
    
    def _to_dict(self) -> dict:
        """Convert config to dict for serialization."""
        def convert(obj):
            if hasattr(obj, '__dataclass_fields__'):
                result = {}
                for k, v in asdict(obj).items():
                    if isinstance(v, Path):
                        result[k] = str(v)
                    elif hasattr(v, '__dataclass_fields__'):
                        result[k] = convert(v)
                    else:
                        result[k] = v
                return result
            return obj
        
        return convert(self)


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get global config instance."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def set_config(config: Config):
    """Set global config instance."""
    global _config
    _config = config