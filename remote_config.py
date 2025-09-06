# remote_config.py
from pathlib import Path
from uttils import SSHSettings, build_server_from_env

# --- пути MAIN ---
LEDGER_PATH_DEFAULT = Path("/mnt/nvme1/ledger")
LOCAL_VALIDATOR_KEY = Path.home() / "solana/validator-keypair.json"
LOCAL_UNSTAKED_IDENTITY = Path.home() / "solana/unstaked-identity.json"

# --- пути SECONDARY (строки можно с $HOME) ---
REMOTE_VALIDATOR_KEY = "$HOME/solana/validator-keypair.json"
# Если путь к леджеру на SECONDARY отличается от MAIN — укажи здесь (или передай через --remote-ledger)
# Пример: REMOTE_LEDGER_PATH = "/mnt/nvme1/ledger"
REMOTE_LEDGER_PATH: str | None = None
REMOTE_FDCTL = "$HOME/firedancer/bin/fdctl"
REMOTE_FD_CONFIG_PATH = "$HOME/config.toml"

# --- локальные бинарники MAIN (фиксированные версии) ---
AGAVE_CLI_LOCAL = Path.home() / ".local/share/solana/install/active_release/bin/agave-validator"
FDCTL_LOCAL = Path.home() / "firedancer/bin/fdctl"
FD_CONFIG_LOCAL = Path.home() / "config.toml"

# --- единственный сервер берём из .env рядом с этим файлом (можешь поменять путь при желании) ---
ENV_PATH = Path(__file__).with_name(".env")
SECONDARY: SSHSettings = build_server_from_env(ENV_PATH)
