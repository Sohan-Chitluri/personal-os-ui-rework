"""Personal OS - Core package."""
__version__ = "0.1.0"
__author__ = "Morales (AI Partner)"

from personal_os.core.config import Config, get_config, set_config
from personal_os.core.vault import VaultManager, get_vault

__all__ = [
    "Config",
    "get_config",
    "set_config",
    "VaultManager",
    "get_vault",
]