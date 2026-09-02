"""Environment configuration for AAMAS.

Loads local configuration from `.env`.
"""

from pathlib import Path

from dotenv import load_dotenv

from src.logging_config import get_logger

logger = get_logger(__name__)


class EnvInfo:
    """Resolved environment configuration."""

    def __init__(self, name: str, env_file: str | None, loaded: bool):
        self.name = name
        self.env_file = env_file
        self.loaded = loaded

    @property
    def is_local(self) -> bool:
        return True


def load_environment(explicit_path: str | None = None) -> EnvInfo:
    """Load configuration variables from dotenv file."""
    if explicit_path:
        p = Path(explicit_path)
        if p.is_file():
            load_dotenv(p, override=True)
            return EnvInfo("local", str(p), True)
        return EnvInfo("local", str(p), False)

    for candidate in (".env", ".env.local"):
        p = Path(candidate)
        if p.is_file():
            load_dotenv(p, override=True)
            return EnvInfo("local", candidate, True)

    return EnvInfo("local", None, False)


def env_banner(env: EnvInfo) -> str:
    """Build the plain banner text."""
    file_note = env.env_file or "no env file (process env only)"
    return f"AAMAS Local Edge Monitor | source: {file_note}"


def print_env_banner(env: EnvInfo) -> None:
    """Log startup banner."""
    logger.info("=================================================================")
    logger.info("  %s", env_banner(env))
    logger.info("=================================================================")
