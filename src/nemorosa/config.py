"""Nemorosa configuration processing module."""

import sys
from pathlib import Path
from typing import Any

import msgspec
import yaml
from platformdirs import user_config_dir

from . import logger

APPNAME = "nemorosa"

# Get project root directory
root_path = Path(__file__).parent.parent


class GlobalConfig(msgspec.Struct):
    """Global configuration."""

    loglevel: str = "info"
    no_download: bool = False
    exclude_mp3: bool = True
    check_trackers: list[str] = msgspec.field(
        default_factory=lambda: ["flacsfor.me", "home.opsfet.ch", "52dic.vip", "open.cd", "daydream.dmhy.best"]
    )

    def __post_init__(self):
        # Validate log level
        valid_levels = ["debug", "info", "warning", "error", "critical"]
        if self.loglevel not in valid_levels:
            raise ValueError(f"Invalid loglevel '{self.loglevel}'. Must be one of: {valid_levels}")

        # Validate check_trackers is a list
        if not isinstance(self.check_trackers, list):
            raise ValueError("check_trackers must be a list")


class DownloaderConfig(msgspec.Struct):
    """Downloader configuration."""

    client: str = ""
    label: str = "nemorosa"

    def __post_init__(self):
        if not self.client:
            raise ValueError("Downloader client URL is required")

        # Validate client URL format
        if not self.client.startswith(("deluge://", "transmission+", "qbittorrent+")):
            raise ValueError(f"Invalid client URL format: {self.client}")

        # Validate label cannot be empty
        if not self.label or not self.label.strip():
            raise ValueError("Downloader label cannot be empty")


class TargetSiteConfig(msgspec.Struct):
    """Target site configuration."""

    server: str = ""
    tracker: str = ""
    api_key: str | None = None
    cookie: str | None = None

    def __post_init__(self):
        if not self.server:
            raise ValueError("Target site server URL is required")
        if not self.tracker:
            raise ValueError("Target site tracker is required")

        # At least one of api_key or cookie is required
        if not self.api_key and not self.cookie:
            raise ValueError(f"Target site '{self.server}' must have either api_key or cookie")

        # Validate server URL format
        if not self.server.startswith(("http://", "https://")):
            raise ValueError(f"Invalid server URL format: {self.server}")


class NemorosaConfig(msgspec.Struct):
    """Nemorosa main configuration class."""

    global_config: GlobalConfig = msgspec.field(name="global", default_factory=GlobalConfig)
    downloader: DownloaderConfig = msgspec.field(default_factory=DownloaderConfig)
    target_sites: list[TargetSiteConfig] = msgspec.field(name="target_site", default_factory=list)

    def __post_init__(self):
        # Validate target_sites
        if not isinstance(self.target_sites, list):
            raise ValueError("target_site must be a list")

        # Validate each target_site configuration
        for i, site in enumerate(self.target_sites):
            if not isinstance(site, TargetSiteConfig):
                raise ValueError(f"Error in target_site[{i}]: must be TargetSiteConfig instance")


def get_user_config_path() -> str:
    """Get configuration file path in user config directory.

    Returns:
        str: Configuration file path.
    """
    config_dir = user_config_dir(APPNAME)
    return str(Path(config_dir) / "config.yml")


def get_config_dir() -> str:
    """Get configuration file directory path.

    Returns:
        str: Configuration file directory path.
    """
    config_file_path = get_user_config_path()
    return str(Path(config_file_path).parent)


def find_config_path(config_path: str | None = None) -> str:
    """Find configuration file path.

    Args:
        config_path: Specified configuration file path, if None uses user config directory.

    Returns:
        Absolute path of the configuration file.

    Raises:
        FileNotFoundError: Raised when configuration file is not found.
    """
    if config_path:
        # Use specified configuration file path
        config_path = Path(config_path)
        if config_path.exists():
            return str(config_path.absolute())
        else:
            raise FileNotFoundError(f"Specified config file not found: {config_path}")

    # Only use user configuration directory
    user_config_path = Path(get_user_config_path())
    if user_config_path.exists():
        return str(user_config_path.absolute())

    raise FileNotFoundError(f"Config file not found at: {user_config_path}")


def _parse_config(config_path: str) -> dict[str, Any]:
    """Parse configuration file.

    Args:
        config_path: Configuration file path.

    Returns:
        Parsed configuration dictionary.

    Raises:
        ValueError: Raised when configuration file parsing fails.
    """
    try:
        with open(config_path, encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
            return config_data or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing YAML config file '{config_path}': {e}") from e
    except Exception as e:
        raise ValueError(f"Error reading config file '{config_path}': {e}") from e


def setup_config(config_path: str | None = None) -> NemorosaConfig:
    """Set up and load configuration.

    Args:
        config_path: Configuration file path, if None auto-detect.

    Returns:
        NemorosaConfig instance.

    Raises:
        ValueError: Raised when configuration loading or validation fails.
    """
    try:
        # Find configuration file
        actual_config_path = find_config_path(config_path)

        # Parse configuration file
        config_data = _parse_config(actual_config_path)

        # Create configuration object
        config = msgspec.convert(config_data, type=NemorosaConfig)

        return config

    except FileNotFoundError as e:
        raise ValueError(f"Configuration file not found: {e}") from e
    except (msgspec.ValidationError, ValueError) as e:
        raise ValueError(f"Error parsing configuration file: {e}") from e
    except Exception as e:
        raise ValueError(f"Failed to setup configuration: {e}") from e


def create_default_config(target_path: str | None = None) -> str:
    """Create default configuration file.

    Args:
        target_path: Target path, if None create in user config directory.

    Returns:
        Created configuration file path.
    """
    if target_path is None:
        target_path = get_user_config_path()

    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Default configuration content
    default_config = {
        "global": {
            "loglevel": "info",
            "no_download": False,
            "exclude_mp3": True,
            "check_trackers": ["flacsfor.me", "home.opsfet.ch", "52dic.vip", "open.cd", "daydream.dmhy.best"],
        },
        "downloader": {"client": "transmission+http://user:pass@localhost:9091/transmission/rpc", "label": "nemorosa"},
        "target_site": [
            {"server": "https://redacted.sh", "tracker": "flacsfor.me", "api_key": "your_api_key_here"},
            {"server": "https://orpheus.network", "tracker": "home.opsfet.ch", "api_key": "your_api_key_here"},
        ],
    }

    with open(target_path, "w", encoding="utf-8") as f:
        yaml.dump(default_config, f, default_flow_style=False, allow_unicode=True, indent=2)

    return str(target_path)


# Global configuration object
cfg: NemorosaConfig | None = None


def init_config(config_path: str | None = None) -> None:
    """Initialize global configuration object.

    Args:
        config_path: Configuration file path, if None auto-detect.

    Raises:
        ValueError: Raised when configuration loading or validation fails.
    """
    global cfg

    try:
        cfg = setup_config(config_path)
        # Log successful configuration loading
        log = logger.generate_logger("info")
        actual_config_path = find_config_path(config_path)
        log.info(f"Configuration loaded successfully from: {actual_config_path}")
    except ValueError as e:
        if "Configuration file not found" in str(e):
            # Configuration file doesn't exist, create default configuration file
            log = logger.generate_logger("info")
            log.warning("Configuration file not found. Creating default configuration...")

            # Determine configuration file path
            default_config_path = config_path or get_user_config_path()

            # Create default configuration file
            created_path = create_default_config(default_config_path)
            log.success(f"Default configuration created at: {created_path}")
            log.info("Please edit the configuration file with your settings and run nemorosa again.")
            log.info("You can also specify a custom config path with: nemorosa --config /path/to/config.yml")

            # Exit program
            sys.exit(0)
        else:
            # Other configuration errors, re-raise
            raise
