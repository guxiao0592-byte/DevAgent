"""Configuration manager for DevAgent."""

import os
import yaml
from typing import Any


DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "configs", "config.yaml")


def load_config(config_path: str = None) -> dict:
    """Load configuration from YAML file, with environment variable substitution."""
    if config_path is None:
        config_path = os.environ.get("DEVAGENT_CONFIG", DEFAULT_CONFIG_PATH)

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r") as f:
        content = f.read()

    # Substitute environment variables
    import re
    def env_sub(match):
        var_name = match.group(1)
        default = match.group(2)
        return os.environ.get(var_name, default) if default else os.environ.get(var_name, "")

    content = re.sub(r'\$\{([^}:]+)(?::([^}]*))?\}', env_sub, content)
    config = yaml.safe_load(content)
    return config


def get_llm_config(config: dict) -> dict:
    """Get the active LLM provider configuration."""
    return config.get("model", {})


def get_workflow_config(config: dict) -> dict:
    """Get workflow settings."""
    return config.get("workflow", {})
