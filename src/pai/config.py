"""Configuration management for PAI.

Uses Pydantic Settings for type-safe config with env var support.
Config is read from ~/.config/pai/config.yaml with env overrides.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_config_dir() -> Path:
    """Get the PAI config directory, creating it if needed."""
    config_dir = Path.home() / ".config" / "pai"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_data_dir() -> Path:
    """Get the PAI data directory, creating it if needed."""
    data_dir = Path.home() / ".local" / "share" / "pai"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


class ClaudeSettings(BaseSettings):
    """Claude API settings."""

    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"

    model_config = SettingsConfigDict(env_prefix="ANTHROPIC_")


class LocalLLMSettings(BaseSettings):
    """Local LLM (llama.cpp) settings."""

    url: str = "http://localhost:8080"
    model: str = "default"

    model_config = SettingsConfigDict(env_prefix="PAI_LOCAL_")


class LLMRoutingSettings(BaseSettings):
    """LLM routing configuration."""

    sensitive_domains: list[str] = Field(default_factory=lambda: ["health", "finance", "personal"])
    force_local: bool = False

    model_config = SettingsConfigDict(env_prefix="PAI_ROUTING_")


class LLMSettings(BaseSettings):
    """LLM provider settings."""

    default: str = "claude"
    claude: ClaudeSettings = Field(default_factory=ClaudeSettings)
    local: LocalLLMSettings = Field(default_factory=LocalLLMSettings)
    routing: LLMRoutingSettings = Field(default_factory=LLMRoutingSettings)

    model_config = SettingsConfigDict(env_prefix="PAI_LLM_")


class DatabaseSettings(BaseSettings):
    """Database settings."""

    path: Path = Field(default_factory=lambda: get_data_dir() / "pai.db")

    model_config = SettingsConfigDict(env_prefix="PAI_DB_")


class Settings(BaseSettings):
    """Main application settings."""

    llm: LLMSettings = Field(default_factory=LLMSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    debug: bool = False

    model_config = SettingsConfigDict(
        env_prefix="PAI_",
        env_nested_delimiter="__",
    )

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> "Settings":
        """Load settings from YAML file, with env overrides."""
        if path is None:
            path = get_config_dir() / "config.yaml"

        data: dict[str, Any] = {}
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}

        return cls(**data)

    def save(self, path: Path | None = None) -> None:
        """Save settings to YAML file."""
        if path is None:
            path = get_config_dir() / "config.yaml"

        with open(path, "w") as f:
            yaml.dump(self.model_dump(mode="json"), f, default_flow_style=False)


# Global settings instance, lazily loaded
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings.from_yaml()
    return _settings


def reload_settings() -> Settings:
    """Reload settings from disk."""
    global _settings
    _settings = Settings.from_yaml()
    return _settings
