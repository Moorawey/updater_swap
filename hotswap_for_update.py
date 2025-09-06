# hotswap_for_update.py
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from remote_config import (
    LEDGER_PATH_DEFAULT,
    LOCAL_VALIDATOR_KEY,
    LOCAL_UNSTAKED_IDENTITY,
    REMOTE_VALIDATOR_KEY,
)
from verify_identity import verify


@dataclass(frozen=True)
class CliArgs:
    ledger: Path
    local_validator_key: Path
    local_unstaked_identity: Path
    remote_validator_key: str
    remote_ledger: Path | None
    assume_yes: bool
    fast: bool
    force_main_client: str | None
    force_remote_client: str | None
    verbose: bool


def parse_args(argv: list[str]) -> CliArgs:
    if len(argv) < 2 or argv[1] != "verify":
        print("Использование:")
        print("python ... verify [--ledger /path] [--local-validator-key /path] [--local-unstaked-identity /path]")
        print("[--remote-validator-key '$HOME/...'] [--remote-ledger '/path/on/secondary']")
        raise SystemExit(2)

    rest = argv[2:]
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--ledger", type=Path, default=LEDGER_PATH_DEFAULT)
    p.add_argument("--key", dest="local_validator_key", type=Path, default=None)  # back-compat
    p.add_argument("--local-validator-key", type=Path, default=None)
    p.add_argument("--local-unstaked-identity", type=Path, default=None)
    p.add_argument("--remote-validator-key", type=str, default=None)
    p.add_argument("--remote-ledger", type=Path, default=None)
    p.add_argument("--yes", dest="assume_yes", action="store_true")
    p.add_argument("--fast", dest="fast", action="store_true")
    p.add_argument("--main-client", dest="force_main_client", choices=["AGAVE","FD"], default=None)
    p.add_argument("--remote-client", dest="force_remote_client", choices=["AGAVE","FD"], default=None)
    p.add_argument("-v", "--verbose", action="store_true")

    args, unknown = p.parse_known_args(rest)
    if unknown:
        print(f"Неизвестные аргументы: {' '.join(unknown)}")
        raise SystemExit(2)

    return CliArgs(
        ledger=args.ledger,
        local_validator_key=(args.local_validator_key or LOCAL_VALIDATOR_KEY),
        local_unstaked_identity=(args.local_unstaked_identity or LOCAL_UNSTAKED_IDENTITY),
        remote_validator_key=(args.remote_validator_key or str(REMOTE_VALIDATOR_KEY)),
        remote_ledger=args.remote_ledger,
        assume_yes=args.assume_yes,
        fast=args.fast,
        force_main_client=args.force_main_client,
        force_remote_client=args.force_remote_client,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    a = parse_args(sys.argv)
    try:
        code = verify(
            a.ledger,
            a.local_validator_key,
            local_unstaked_identity=a.local_unstaked_identity,
            remote_validator_key=a.remote_validator_key,
            remote_ledger=a.remote_ledger,
            assume_yes=a.assume_yes,
            fast=a.fast,
            force_main_client=a.force_main_client,
            force_remote_client=a.force_remote_client,
            verbose=a.verbose,
        )
    except KeyboardInterrupt:
        code = 130
    except Exception as e:
        print("ОШИБКА:", e)
        code = 1
    sys.exit(code)