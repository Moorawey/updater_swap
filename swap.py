import shlex
import subprocess
import time
from pathlib import Path

from remote_config import AGAVE_CLI_LOCAL, FDCTL_LOCAL, FD_CONFIG_LOCAL
from uttils import (
    SSHSettings,
    run_remote,
    build_ssh_command,
    _build_remote_set_identity_cmd_no_shell,
    remote_expand_path,
    remove_tower_on_secondary,
)


# -------------------- Local/remote helpers for swap orchestration --------------------
class SSHSession:
    def __init__(self, cfg, init_script=None):
        from uttils import build_ssh_command  # у тебя уже есть
        # Долгоживущая bash-сессия, читающая команды из stdin (надёжнее, чем пустой remote-cmd)
        if init_script is None:
            init_script = ["/bin/bash", "-s", "-l"]
        self._cmd = build_ssh_command(cfg, init_script)
        self.p = subprocess.Popen(self._cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)

    def run(self, line: str, wait_output=True):
        """Выполняет одну строку в открытой сессии; возвращает (out, err)."""
        if not self.p.stdin:
            raise RuntimeError("SSH stdin closed")
        # маркеры для синхронности
        marker = "__RC__=$?; echo __RC__:$__RC__"
        self.p.stdin.write(line + f"; {marker}\n")
        self.p.stdin.flush()
        if not wait_output:
            return "", ""
        out_lines = []
        while True:
            line = self.p.stdout.readline()
            if not line:
                break
            if line.startswith("__RC__:"):
                rc = int(line.split(":", 1)[1])
                if rc != 0:
                    # добираем stderr для дебага
                    err = self.p.stderr.read() if self.p.stderr else ""
                    raise RuntimeError(f"remote rc={rc}\nOUT:\n{''.join(out_lines)}\nERR:\n{err}")
                return "".join(out_lines), ""
            out_lines.append(line)

    def close(self):
        try:
            # Если процесс уже умер — не пишем в stdin (иначе Broken pipe)
            if self.p and self.p.poll() is None and self.p.stdin:
                try:
                    self.p.stdin.write("exit\n")
                    self.p.stdin.flush()
                except BrokenPipeError:
                    pass
        finally:
            try:
                # Если ещё жив — аккуратно завершим
                if self.p and self.p.poll() is None:
                    self.p.terminate()
            except Exception:
                pass


def arm_remote_set_identity(secondary_cfg: SSHSettings, cmd_no_shell: str) -> subprocess.Popen:
    """Открывает SSH на SECONDARY, удалёнка ждёт ENTER, затем exec <cmd_no_shell>."""
    remote_sh = f'read -r _; exec {cmd_no_shell}'
    ssh_cmd = build_ssh_command(secondary_cfg, remote_sh)
    return subprocess.Popen(ssh_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def build_local_set_identity_cmd(main_client: str, main_ledger: Path, key: Path) -> list[str]:
    """Строит команду set-identity для MAIN (строго из remote_config путей)."""
    kind = (main_client or "").upper()
    if kind == "AGAVE":
        cli = str(Path(AGAVE_CLI_LOCAL).expanduser())
        return [cli, "--ledger", str(main_ledger), "set-identity", str(key)]
    if kind == "FD":
        fd = str(Path(FDCTL_LOCAL).expanduser())
        cfg = str(Path(FD_CONFIG_LOCAL).expanduser())
        return [fd, "set-identity", "--config", cfg, str(key), "--force"]
    raise RuntimeError(f"[MAIN] unknown client '{main_client}'")


def _spawn_set_identity_main_async(main_client: str, main_ledger: Path, key: Path) -> subprocess.Popen:
    cmd = build_local_set_identity_cmd(main_client, main_ledger, key)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _prewarm_secondary(secondary_cfg: SSHSettings, remote_ledger: Path) -> None:
    """Поднимает master-SSH и прогревает файловые страницы SECONDARY (без побочек)."""
    run_remote(secondary_cfg, "true")  # ControlMaster / known_hosts / auth
    led = remote_expand_path(secondary_cfg, str(remote_ledger))
    run_remote(secondary_cfg, f'test -r {shlex.quote(led)}/CURRENT || true', login_shell=False)


def _trigger_remote_bg(secondary_cfg: SSHSettings, cmd_no_shell: str, ack: str = "OK") -> None:
    """
    Запускает команду на SECONDARY в фоне и сразу возвращает управление.
    Не держит ssh-сессию. Даёт лёгкий ACK, что шёлл принял команду.
    """
    remote_sh = f'echo {shlex.quote(ack)}; nohup setsid {cmd_no_shell} >/dev/null 2>&1 & disown'
    res = run_remote(secondary_cfg, remote_sh, login_shell=False)
    out = (res.stdout or "").strip()
    if ack not in out:
        raise RuntimeError(f"[SECONDARY] bg-trigger: не получили ACK: {out!r}")


# ----------------------------------- SWAP -----------------------------------

def perform_swap(
        *,
        main_client: str,
        remote_client: str,
        current_voting_pubkey: str,
        main_ledger: Path,
        local_unstaked_identity: Path,
        secondary_cfg: SSHSettings,
        remote_validator_key: str,
        remote_ledger: Path,
        cleanup_remote_tower: bool = True,  # флаг сохранил, но теперь чистка идёт в той же сессии
        fd_trigger_delay_ms: int = 10,
        fd_mode: str = "armed",  # 'sequential' | 'armed' | 'bg'
        assume_yes: bool = False,
        verbose: bool = False,
) -> None:
    """
    Свап без рестартов + ускорения:
      • prewarm SECONDARY (ssh + диск)
      • ОДНА SSH-сессия внутри окна: rm tower* и ARMed-выполнение
      • FD режимы: sequential (по-умолчанию), armed, bg (fire-and-forget)
    """
    # 0) прогрев SECONDARY
    _prewarm_secondary(secondary_cfg, remote_ledger)

    # 1) заранее собираем ПОЛНУЮ команду без shell
    remote_cmd = _build_remote_set_identity_cmd_no_shell(
        remote_client=remote_client,
        secondary_cfg=secondary_cfg,
        remote_ledger=remote_ledger,
        new_key_path_str=remote_validator_key,
    )

    # 2) План + подтверждение
    print("\n[PLAN] Свап будет выполнен с параметрами:")
    print(f"       • MAIN client:               {main_client}")
    print(f"       • SECONDARY client:          {remote_client}")
    print(f"       • Voting PUBKEY:             {current_voting_pubkey}")
    print(f"       • MAIN ledger:               {main_ledger}")
    print(f"       • SECONDARY ledger:          {remote_ledger}")
    print(f"       • MAIN -> set-identity:      {local_unstaked_identity}  (unstaked)")
    print(f"       • SECONDARY -> set-identity: {remote_validator_key}  (validator)\n")
    if verbose:
        print("[VERBOSE] Режим FD:", fd_mode)
    if not assume_yes:
        try:
            input("Нажмите ENTER, чтобы продолжить. Ctrl+C — отмена… ")
        except KeyboardInterrupt:
            print("Отменено пользователем.")
            return

    rc_kind = (remote_client or "").upper()

    # 3) FD: быстрые ветки — bg и sequential
    if rc_kind == "FD":
        mode = fd_mode.lower()

        if mode == "bg":
            # секунды экономятся: на SECONDARY триггерим fdctl в фоне и сразу занимаемся MAIN
            # (при необходимости почистим tower в отдельном раунде перед этим — но лучше хост держать чистым заранее)
            if cleanup_remote_tower:
                dir_q = shlex.quote(str(remote_ledger))
                pk_q = shlex.quote(current_voting_pubkey)
                if verbose:
                    print(f"[VERBOSE] SECONDARY (FD) tower cleanup: rm -f \"{dir_q}\"/tower*-\"{pk_q}\".bin")
                run_remote(
                    secondary_cfg,
                    f'dir={dir_q}; pk={pk_q}; rm -f "$dir"/tower*-"$pk".bin || true; echo OK',
                    login_shell=False
                )
            if verbose:
                print(f"[VERBOSE] SECONDARY (FD) bg trigger: {remote_cmd}")
            _trigger_remote_bg(secondary_cfg, remote_cmd)
            p = _spawn_set_identity_main_async(main_client, main_ledger, local_unstaked_identity)
            if verbose:
                print(f"[VERBOSE] MAIN set-identity: {build_local_set_identity_cmd(main_client, main_ledger, local_unstaked_identity)}")
            if fd_trigger_delay_ms > 0:
                time.sleep(fd_trigger_delay_ms / 1000.0)
            try:
                p.wait(timeout=10 if (main_client or "").upper() == "FD" else 6)
            except Exception:
                pass
            print("SWAP (FD bg): triggered")
            return

        # sequential и armed — через одну долголивущую сессию внутри окна
        sess = SSHSession(secondary_cfg)  # одна SSH-сессия
        try:
            if cleanup_remote_tower:
                dir_q = shlex.quote(str(remote_ledger))
                pk_q = shlex.quote(current_voting_pubkey)
                if verbose:
                    print(f"[VERBOSE] SECONDARY tower cleanup: rm -f \"{dir_q}\"/tower*-\"{pk_q}\".bin")
                sess.run(f'dir={dir_q}; pk={pk_q}; rm -f "$dir"/tower*-"$pk".bin || true; echo "TOWER_OK"')

            if mode == "sequential":
                # как при ручном: MAIN, дождаться немного, затем SECONDARY
                p = _spawn_set_identity_main_async(main_client, main_ledger, local_unstaked_identity)
                if verbose:
                    print(f"[VERBOSE] MAIN set-identity: {build_local_set_identity_cmd(main_client, main_ledger, local_unstaked_identity)}")
                try:
                    p.wait(timeout=10 if (main_client or "").upper() == "FD" else 6)
                except Exception:
                    pass
                # исполняем fdctl/agave на SECONDARY без лишнего bash -lc
                # через открытую сессию делаем exec
                if verbose:
                    print(f"[VERBOSE] SECONDARY exec: {remote_cmd}")
                sess.run(f'exec {remote_cmd}', wait_output=False)
                # не ждём завершение fdctl (чтобы не попасть в блок); сессия завершится сама
                print("SWAP (FD sequential): ok")
                return

            if mode == "armed":
                # чистка башни отдельной быстрой командой
                if cleanup_remote_tower:
                    dir_q = shlex.quote(str(remote_ledger))
                    pk_q = shlex.quote(current_voting_pubkey)
                    if verbose:
                        print(f"[VERBOSE] SECONDARY tower cleanup: rm -f \"{dir_q}\"/tower*-\"{pk_q}\".bin")
                    run_remote(
                        secondary_cfg,
                        f'dir={dir_q}; pk={pk_q}; rm -f "$dir"/tower*-"$pk".bin || true; echo OK',
                        login_shell=False
                    )

                # Открываем ARM-сессию, которая ждёт ENTER и затем exec'ает команду
                arm_proc = arm_remote_set_identity(secondary_cfg, remote_cmd)
                try:
                    # Локально запускаем MAIN set-identity
                    p = _spawn_set_identity_main_async(main_client, main_ledger, local_unstaked_identity)
                    if verbose:
                        print(f"[VERBOSE] MAIN set-identity: {build_local_set_identity_cmd(main_client, main_ledger, local_unstaked_identity)}")
                    if fd_trigger_delay_ms > 0:
                        time.sleep(fd_trigger_delay_ms / 1000.0)

                    # Триггерим SECONDARY безопасным переводом строки
                    if arm_proc and arm_proc.poll() is None and arm_proc.stdin:
                        try:
                            arm_proc.stdin.write("\n")
                            arm_proc.stdin.flush()
                        except BrokenPipeError:
                            pass

                    # Ждём немного только MAIN
                    try:
                        p.wait(timeout=10 if (main_client or "").upper() == "FD" else 6)
                    except Exception:
                        pass
                    print("SWAP (FD armed-bg): ok")
                    return
                finally:
                    try:
                        if arm_proc and arm_proc.stdin:
                            try:
                                arm_proc.stdin.close()
                            except Exception:
                                pass
                    except Exception:
                        pass

            raise RuntimeError(f"[FD] unknown fd_mode='{fd_mode}' (use sequential|armed|bg)")

        finally:
            sess.close()

    # 4) SECONDARY = AGAVE: одна сессия, но последовательный сценарий
    sess = SSHSession(secondary_cfg)
    try:
        if cleanup_remote_tower:
            dir_q = shlex.quote(str(remote_ledger))
            pk_q = shlex.quote(current_voting_pubkey)
            if verbose:
                print(f"[VERBOSE] SECONDARY (AGAVE) tower cleanup: rm -f \"{dir_q}\"/tower*-\"{pk_q}\".bin")
            out, _ = sess.run(f'dir={dir_q}; pk={pk_q}; rm -f "$dir"/tower*-"$pk".bin || true; echo "TOWER_OK"')
            if verbose and out:
                print(f"[VERBOSE] SECONDARY (AGAVE) tower result: {out.strip()}")

        p = _spawn_set_identity_main_async(main_client, main_ledger, local_unstaked_identity)
        if verbose:
            print(f"[VERBOSE] MAIN set-identity: {build_local_set_identity_cmd(main_client, main_ledger, local_unstaked_identity)}")
        try:
            p.wait(timeout=6)
        except Exception:
            pass

        if verbose:
            print(f"[VERBOSE] SECONDARY (AGAVE) exec: {remote_cmd}")
        # Выполняем напрямую и ждём завершения, чтобы не прервать команду закрытием SSH
        res = run_remote(secondary_cfg, remote_cmd, login_shell=False)
        if verbose:
            so = (res.stdout or '').strip()
            se = (res.stderr or '').strip()
            print(f"[VERBOSE] SECONDARY (AGAVE) rc={res.returncode}")
            if so:
                print(f"[VERBOSE] SECONDARY (AGAVE) stdout: {so}")
            if se:
                print(f"[VERBOSE] SECONDARY (AGAVE) stderr: {se}")
        if res.returncode != 0:
            raise RuntimeError("SECONDARY (AGAVE) set-identity завершилась с ошибкой")
        print("SWAP (AGAVE sequential): ok")
    finally:
        sess.close()
