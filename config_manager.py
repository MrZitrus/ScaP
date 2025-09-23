"""
Centralized configuration management for StreamScraper.
Combines settings from .env, config.json, and environment variables.
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ConfigManager:
    """Centralized configuration manager for StreamScraper."""

    def __init__(self, config_file: str = "config.json", env_file: str = ".env"):
        """
        Initialize the configuration manager.

        Args:
            config_file (str): Path to the config.json file
            env_file (str): Path to the .env file
        """
        # Load environment variables
        load_dotenv(env_file)

        # Initialize config
        self.config = {
            # Default values
            "download": {
                "directory": "downloads",
                "max_parallel_downloads": 3,
                "max_parallel_extractions": 5,
                "scan_on_startup": True,
                "db_path": "media.db"
            },
            "server": {
                "port": 5000,
                "debug": True,
                "host": "0.0.0.0"
            },
            "real_debrid": {
                "enabled": True,
                "api_key": "TMRQHX6JQMBKAEF6Z33IHACMRC2SVDGCY44ZVZ7T3ZA4IWXH2FGA"
            },
            "jellyfin": {
                "enabled": False,
                "url": "",
                "api_key": "",
                "user_id": ""
            },
            "logging": {
                "level": "DEBUG",
                "file": "logs/streamscraper.log"
            },
            "gemini": {
                "enabled": False,
                "api_key": "",
                "model": "gemini-1.5-pro-latest",
                "auto_enhance_metadata": False
            },
            "language": {
                "prefer": ["de", "deu", "ger"],
                "require_dub": True,
                "sample_seconds": 45,
                "remux_to_de_if_present": True,
                "accept_on_error": False,
                "verify_with_whisper": True
            },
            "language_priority": {
                "enabled": True,
                "priorities": [
                    ["de", None],     # Deutsch
                    ["en", "de"],     # Englisch mit German Dub
                    ["en", None],     # Englisch
                    ["ja", "de"],     # Japanisch mit German Dub
                    ["ja", "en"],     # Japanisch mit English Dub
                    ["ja", None]      # Japanisch (Original)
                ]
            }
        }

        # Load config from file
        self._load_config_file(config_file)

        # Override with environment variables
        self._load_env_variables()

        # Log configuration (excluding sensitive data)
        self._log_config()

    def _load_config_file(self, config_file: str) -> None:
        """
        Load configuration from JSON file.

        Args:
            config_file (str): Path to the config file
        """
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    file_config = json.load(f)

                # Update config with file values
                self._update_nested_dict(self.config, file_config)
                logger.info(f"Loaded configuration from {config_file}")
            else:
                logger.warning(f"Config file {config_file} not found, using defaults")
        except Exception as e:
            logger.error(f"Error loading config file: {str(e)}")

    def _load_env_variables(self) -> None:
        """Load configuration from environment variables."""
        # Download settings
        if os.getenv('DOWNLOAD_DIR'):
            self.config['download']['directory'] = os.getenv('DOWNLOAD_DIR')

        if os.getenv('MAX_PARALLEL_DOWNLOADS'):
            try:
                self.config['download']['max_parallel_downloads'] = int(os.getenv('MAX_PARALLEL_DOWNLOADS'))
            except ValueError:
                logger.warning("Invalid MAX_PARALLEL_DOWNLOADS value, using default")

        if os.getenv('MAX_PARALLEL_EXTRACTIONS'):
            try:
                self.config['download']['max_parallel_extractions'] = int(os.getenv('MAX_PARALLEL_EXTRACTIONS'))
            except ValueError:
                logger.warning("Invalid MAX_PARALLEL_EXTRACTIONS value, using default")

        if os.getenv('SCAN_ON_STARTUP'):
            self.config['download']['scan_on_startup'] = os.getenv('SCAN_ON_STARTUP').lower() in ('true', '1', 't')

        if os.getenv('DB_PATH'):
            self.config['download']['db_path'] = os.getenv('DB_PATH')

        # Language priority settings
        if os.getenv('LANGUAGE_PRIORITY_ENABLED'):
            self.config['language_priority']['enabled'] = os.getenv('LANGUAGE_PRIORITY_ENABLED').lower() in ('true', '1', 't')

        # Server settings
        if os.getenv('FLASK_PORT'):
            try:
                self.config['server']['port'] = int(os.getenv('FLASK_PORT'))
            except ValueError:
                logger.warning("Invalid FLASK_PORT value, using default")

        if os.getenv('FLASK_DEBUG'):
            self.config['server']['debug'] = os.getenv('FLASK_DEBUG').lower() in ('true', '1', 't')

        # Jellyfin settings
        if all([os.getenv('JELLYFIN_URL'), os.getenv('JELLYFIN_API_KEY'), os.getenv('JELLYFIN_USER_ID')]):
            self.config['jellyfin']['enabled'] = True
            self.config['jellyfin']['url'] = os.getenv('JELLYFIN_URL')
            self.config['jellyfin']['api_key'] = os.getenv('JELLYFIN_API_KEY')
            self.config['jellyfin']['user_id'] = os.getenv('JELLYFIN_USER_ID')

    def _update_nested_dict(self, d: Dict, u: Dict) -> Dict:
        """
        Update a nested dictionary with another dictionary.

        Args:
            d (Dict): Target dictionary
            u (Dict): Source dictionary

        Returns:
            Dict: Updated dictionary
        """
        for k, v in u.items():
            if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                d[k] = self._update_nested_dict(d[k], v)
            else:
                d[k] = v
        return d

    def _log_config(self) -> None:
        """Log the current configuration (excluding sensitive data)."""
        # Create a copy of the config without sensitive data
        safe_config = self.config.copy()

        # Remove sensitive data
        if 'real_debrid' in safe_config and 'api_key' in safe_config['real_debrid']:
            safe_config['real_debrid']['api_key'] = '***' if safe_config['real_debrid']['api_key'] else ''

        if 'jellyfin' in safe_config and 'api_key' in safe_config['jellyfin']:
            safe_config['jellyfin']['api_key'] = '***' if safe_config['jellyfin']['api_key'] else ''

        logger.debug(f"Current configuration: {json.dumps(safe_config, indent=2)}")

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by key.

        Args:
            key (str): Configuration key (dot notation for nested keys)
            default (Any): Default value if key not found

        Returns:
            Any: Configuration value
        """
        keys = key.split('.')
        value = self.config

        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key: str, value: Any) -> None:
        """
        Set a configuration value.

        Args:
            key (str): Configuration key (dot notation for nested keys)
            value (Any): Value to set
        """
        keys = key.split('.')
        config = self.config

        # Navigate to the last level
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        # Set the value
        config[keys[-1]] = value

    def save(self, config_file: str = "config.json") -> bool:
        """
        Save the current configuration to a file.

        Args:
            config_file (str): Path to the config file

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
            logger.info(f"Configuration saved to {config_file}")
            return True
        except Exception as e:
            logger.error(f"Error saving configuration: {str(e)}")
            return False

    def get_language_priority(self) -> List[Tuple[Optional[str], Optional[str]]]:
        """
        Get the language priority configuration.

        Returns:
            List[Tuple[Optional[str], Optional[str]]]: List of (audio_lang, dub_lang) tuples
        """
        if not self.config.get('language_priority', {}).get('enabled', True):
            return []

        priorities = self.config.get('language_priority', {}).get('priorities', [])
        if not priorities:
            # Fallback to default priorities
            return [
                ("de", None),     # Deutsch
                ("en", "de"),     # Englisch mit German Dub
                ("en", None),     # Englisch
                ("ja", "de"),     # Japanisch mit German Dub
                ("ja", "en"),     # Japanisch mit English Dub
                ("ja", None)      # Japanisch (Original)
            ]

        # Convert list of lists to list of tuples
        return [(audio, dub) for audio, dub in priorities]


# Singleton instance
_config_manager = None


def get_config() -> ConfigManager:
    """
    Get the singleton ConfigManager instance.

    Returns:
        ConfigManager: The configuration manager instance
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager
