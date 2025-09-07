# verify_identity.py
from pathlib import Path

from remote_config import (
    LEDGER_PATH_DEFAULT,
    LOCAL_VALIDATOR_KEY,
    LOCAL_UNSTAKED_IDENTITY,
    REMOTE_VALIDATOR_KEY,
    SECONDARY,  # single server from .env
    REMOTE_LEDGER_PATH,
)
from uttils import (
    SSHSettings,
    run_remote,
    check_connection,
    detect_client_local,
    detect_client_remote_type, get_local_identity_from_monitor, get_local_pubkey_from_keyfile,
    get_remote_pubkey_from_keyfile_via_keygen,
)

from swap import perform_swap

# helpers expected:
# get_local_identity_from_monitor(main_ledger) -> str
# get_local_pubkey_from_keyfile(main_key: Path) -> str
# get_remote_pubkey_from_keyfile_via_keygen(cfg, key_path_str) -> str


def verify(
    main_ledger: Path,
    main_key: Path,
    *,
    local_unstaked_identity: Path | None = None,
    remote_validator_key: str | None = None,
    remote_ledger: Path | None = None,
    assume_yes: bool | None = None,
    fast: bool | None = None,
    force_main_client: str | None = None,
    force_remote_client: str | None = None,
    verbose: bool | None = None,
) -> int:
    secondary_cfg: SSHSettings = SECONDARY

    print(f"[MAIN] Ledger: {main_ledger}")
    print(f"[MAIN] Key:    {main_key}")

    # client autodetect (overridable)
    main_client = (force_main_client or detect_client_local(main_ledger))
    print(f"[MAIN] Client: {main_client}")

    ok, err = check_connection(secondary_cfg)
    if not ok:
        print("[SSH] Connection to SECONDARY failed.")
        if err:
            print("stderr:", err)
        print("Hint: eval $(ssh-agent) && ssh-add ~/.ssh/<YOUR_KEY>")
        return 3

    # choose SECONDARY ledger: --remote-ledger > REMOTE_LEDGER_PATH > main_ledger
    remote_ledger_effective = (remote_ledger or (Path(REMOTE_LEDGER_PATH) if REMOTE_LEDGER_PATH else main_ledger))
    remote_client = (force_remote_client or detect_client_remote_type(secondary_cfg, remote_ledger_effective))
    print(f"[SECONDARY] Client: {remote_client}")

    # fallback: if process detection failed on SECONDARY, guess by CLI presence
    if (remote_client or '').upper() == 'UNKNOWN' and not force_remote_client:
        r_ag = run_remote(secondary_cfg, "command -v agave-validator || command -v solana-validator || echo")
        ag = (r_ag.stdout or '').strip()
        r_fd = run_remote(secondary_cfg, "command -v fdctl || echo")
        fd = (r_fd.stdout or '').strip()
        if ag:
            remote_client = 'AGAVE'
        elif fd:
            remote_client = 'FD'
        print(f"[SECONDARY] Client (by CLI presence): {remote_client}")

    # fast mode: skip monitor, compare keys only
    r_key = remote_validator_key or str(REMOTE_VALIDATOR_KEY)
    if fast:
        main_key_pub = get_local_pubkey_from_keyfile(main_key)
        secondary_key_pub = get_remote_pubkey_from_keyfile_via_keygen(secondary_cfg, r_key)
        print(f"[FAST] MAIN pubkey: {main_key_pub}")
        print(f"[FAST] SECONDARY pubkey: {secondary_key_pub}")
        current_voting = main_key_pub
    else:
        # full verification via monitor
        main_identity = get_local_identity_from_monitor(main_ledger)
        print(f"[MAIN] Identity (monitor): {main_identity}")

        main_key_pub = get_local_pubkey_from_keyfile(main_key)
        print(f"[MAIN] Pubkey from keyfile: {main_key_pub}")

        secondary_key_pub = get_remote_pubkey_from_keyfile_via_keygen(secondary_cfg, r_key)
        print(f"[SECONDARY] Pubkey from remote validator key: {secondary_key_pub}")

        ok_main = (main_identity == main_key_pub)
        ok_remote = (main_identity == secondary_key_pub)

        print("\nVERIFICATION RESULTS:")
        print(f"  MAIN:      Identity(monitor) == MAIN key ? {'OK' if ok_main else 'MISMATCH'}")
        print(f"  SECONDARY: Identity(monitor) == SECONDARY key ? {'OK' if ok_remote else 'MISMATCH'}")

        if not (ok_main and ok_remote):
            return 1
        current_voting = main_identity

    # confirmation before SWAP (unless --yes)
    if not assume_yes:
        try:
            input("Press ENTER to start SWAP (Ctrl+C to cancel)… ")
        except KeyboardInterrupt:
            print("Cancelled by user.")
            return 130

    # SWAP
    perform_swap(
        main_client=main_client,
        remote_client=remote_client,
        current_voting_pubkey=current_voting,
        main_ledger=main_ledger,
        local_unstaked_identity=(local_unstaked_identity or LOCAL_UNSTAKED_IDENTITY),
        secondary_cfg=secondary_cfg,
        remote_validator_key=r_key,
        remote_ledger=remote_ledger_effective,
        cleanup_remote_tower=True,
        assume_yes=(assume_yes or False),
        verbose=(verbose or False),
    )
    return 0
