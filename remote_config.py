# remote_config.py
from pathlib import Path
from uttils import SSHSettings, build_server_from_env

# --- MAIN paths ---
LEDGER_PATH_DEFAULT = Path("/mnt/nvme1/ledger")
LOCAL_VALIDATOR_KEY = Path.home() / "solana/validator-keypair.json"
LOCAL_UNSTAKED_IDENTITY = Path.home() / "solana/unstaked-identity.json"

# --- SECONDARY paths (strings may use $HOME) ---
REMOTE_VALIDATOR_KEY = "$HOME/solana/validator-keypair.json"
# If the ledger path on SECONDARY differs from MAIN — set it here (or pass via --remote-ledger)
# Example: REMOTE_LEDGER_PATH = "/mnt/nvme1/ledger"
REMOTE_LEDGER_PATH: str | None = None
REMOTE_FDCTL = "$HOME/firedancer/bin/fdctl"
REMOTE_FD_CONFIG_PATH = "$HOME/config.toml"

# --- MAIN local binaries (fixed/known installs) ---
AGAVE_CLI_LOCAL = Path.home() / ".local/share/solana/install/active_release/bin/agave-validator"
FDCTL_LOCAL = Path.home() / "firedancer/bin/fdctl"
FD_CONFIG_LOCAL = Path.home() / "config.toml"

# --- SECONDARY SSH settings sourced from .env next to this file ---
ENV_PATH = Path(__file__).with_name(".env")
SECONDARY: SSHSettings = build_server_from_env(ENV_PATH)
