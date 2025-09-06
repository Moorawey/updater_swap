import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple
import remote_config as rc

FD_NAMES = {"fdctl", "firedancer"}
AGAVE_NAMES = {"agave-validator", "solana-validator"}


# ============================ SSH Settings & Base =============================

@dataclass(frozen=True)
class SSHSettings:
    host: str
    user: str
    port: int = 22
    identity_file: Optional[Path] = Path.home() / ".ssh/id_ed25519"
    strict_host_key_checking: str = "accept-new"  # yes|no|accept-new
    connect_timeout: int = 10
    server_alive_interval: int = 30
    server_alive_count_max: int = 3
    extra_ssh_opts: Tuple[str, ...] = ()


def _ssh_build_args(cfg: SSHSettings, *, for_scp: bool = False) -> list[str]:
    args: list[str] = []
    # Port flag differs between ssh and scp
    if for_scp:
        if getattr(cfg, "port", None):
            args += ["-P", str(cfg.port)]
    else:
        args += ["-p", str(cfg.port)]
    # Identity file
    if cfg.identity_file:
        args += ["-i", str(Path(cfg.identity_file).expanduser())]
    # Common -o options
    args += [
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={cfg.connect_timeout}",
        "-o", f"StrictHostKeyChecking={cfg.strict_host_key_checking}",
        "-o", f"ServerAliveInterval={cfg.server_alive_interval}",
        "-o", f"ServerAliveCountMax={cfg.server_alive_count_max}",
        "-o", "IdentitiesOnly=yes",
        # Speed up repeated ssh calls via master connection
        "-o", "ControlMaster=auto",
        "-o", "ControlPath=~/.ssh/cm-%r@%h:%p",
        "-o", "ControlPersist=60s",
    ]
    # Extra user-provided options
    if cfg.extra_ssh_opts:
        for opt in cfg.extra_ssh_opts:
            args += ["-o", opt]
    return args


def _base_ssh_cmd(cfg: SSHSettings) -> list[str]:
    return ["ssh", *_ssh_build_args(cfg, for_scp=False), f"{cfg.user}@{cfg.host}"]


def build_ssh_command(cfg: SSHSettings, remote_command: Sequence[str] | str | None) -> list[str]:
    cmd = _base_ssh_cmd(cfg)
    if remote_command is None:
        return cmd
    if isinstance(remote_command, str):
        return [*cmd, remote_command]
    quoted = " ".join(shlex.quote(t) for t in remote_command)
    return [*cmd, quoted]


def run_remote(
        cfg: SSHSettings,
        remote_command: Sequence[str] | str,
        timeout: Optional[int] = None,
        login_shell: bool = True,
) -> subprocess.CompletedProcess:
    if isinstance(remote_command, (list, tuple)):
        rc_str = " ".join(shlex.quote(t) for t in remote_command)
    else:
        rc_str = remote_command
    if login_shell:
        rc_str = f"bash -lc {shlex.quote(rc_str)}"
    cmd = build_ssh_command(cfg, rc_str)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def run_local(cmd: Sequence[str] | str, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def check_connection(cfg: SSHSettings) -> Tuple[bool, str]:
    cmd = build_ssh_command(cfg, ["echo", "__PING__"])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    ok = (proc.returncode == 0) and ("__PING__" in (proc.stdout or ""))
    return ok, proc.stderr.strip()


# ========================= .env → SSHSettings helper ==========================

def _load_env_file(path: Path) -> dict:
    data: dict = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip().strip("'").strip('"')
    return data


def _get(env: dict, key: str, default: str | None = None) -> str:
    if key in os.environ:
        return os.environ[key]
    if key in env:
        return env[key]
    if default is not None:
        return default
    raise KeyError(f"Отсутствует обязательная переменная: {key}")


def _get_int(env: dict, key: str, default: int) -> int:
    s = _get(env, key, str(default))
    try:
        return int(s)
    except ValueError:
        raise ValueError(f"{key} должно быть числом, получено: {s!r}")


def _expand_path(s: str | None) -> Path | None:
    return None if not s else Path(os.path.expandvars(os.path.expanduser(s)))


def build_server_from_env(env_file: Path) -> SSHSettings:
    env = _load_env_file(env_file)
    host = _get(env, "SSH_HOST")
    user = _get(env, "SSH_USER")
    port = _get_int(env, "SSH_PORT", 22)

    identity_file = _expand_path(_get(env, "SSH_IDENTITY_FILE", str(Path.home() / ".ssh/id_ed25519")))
    strict = _get(env, "SSH_STRICT_HOST_KEY_CHECKING", "accept-new")
    connect_timeout = _get_int(env, "SSH_CONNECT_TIMEOUT", 10)
    alive_interval = _get_int(env, "SSH_SERVER_ALIVE_INTERVAL", 30)
    alive_count_max = _get_int(env, "SSH_SERVER_ALIVE_COUNT_MAX", 3)

    return SSHSettings(
        host=host,
        user=user,
        port=port,
        identity_file=identity_file,
        strict_host_key_checking=strict,
        connect_timeout=connect_timeout,
        server_alive_interval=alive_interval,
        server_alive_count_max=alive_count_max,
    )


# ============================ Remote path helpers =============================

def remote_expand_path(cfg: SSHSettings, path_str: str) -> str:
    """
    Раскрывает ~ и $VARS в пути на удалённом хосте и нормализует его.
    """
    # Simple memoization to avoid repeated remote calls for the same path/user/host
    global _REMOTE_EXPAND_CACHE
    try:
        cache_key = (cfg.host, cfg.user, path_str)
        if _REMOTE_EXPAND_CACHE.get(cache_key):
            return _REMOTE_EXPAND_CACHE[cache_key]
    except NameError:
        _REMOTE_EXPAND_CACHE = {}

    cmd = (
        "python3 - <<'PY'\n"
        "import os\n"
        f"p = {path_str!r}\n"
        "p = os.path.expanduser(os.path.expandvars(p))\n"
        "print(os.path.realpath(p))\n"
        "PY"
    )
    res = run_remote(cfg, cmd, login_shell=False)
    out = (res.stdout or "").strip()
    if res.returncode != 0 or not out:
        raise RuntimeError(
            "Не удалось раскрыть путь на удалённом хосте: " + path_str + "\n" + (res.stderr or res.stdout or "").strip()
        )
    _REMOTE_EXPAND_CACHE[cache_key] = out
    return out


def _sh_q(s: str) -> str:
    """Мягкое экранирование для печати в логи/шелл."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ============================ /proc helpers & detect ===========================

def _read_text(p: Path) -> str:
    try:
        return p.read_text(errors="ignore")
    except Exception:
        return ""


def _readlink(p: Path) -> str:
    try:
        return os.readlink(p)
    except Exception:
        return ""


def _proc_comm(pid: str) -> str:
    return _read_text(Path("/proc") / pid / "comm").strip()


def _proc_cmdline(pid: str) -> str:
    raw = _read_text(Path("/proc") / pid / "cmdline")
    return raw.replace("\x00", " ").strip()


def _proc_exe_basename(pid: str) -> str:
    exe = _readlink(Path("/proc") / pid / "exe")
    return Path(exe).name if exe else ""


def _classify_pid(pid: str) -> Optional[str]:
    comm = _proc_comm(pid)
    exe_base = _proc_exe_basename(pid)
    args = _proc_cmdline(pid)

    name_pool = {comm, exe_base}
    if name_pool & FD_NAMES or "fdctl" in args or "firedancer" in args:
        return "FD"
    if name_pool & AGAVE_NAMES or "agave-validator" in args or "solana-validator" in args:
        return "AGAVE"
    return None


def _proc_uses_path(pid: str, needle: str) -> bool:
    """Проверяем, имеет ли процесс файлы/dirs в заданном пути (ledger_dir)."""
    proc = Path("/proc") / pid
    needle = str(needle)
    # cwd
    try:
        if needle and needle in _readlink(proc / "cwd"):
            return True
    except Exception:
        pass
    # fd/*
    try:
        for fd in (proc / "fd").iterdir():
            target = _readlink(fd)
            if needle and needle in target:
                return True
    except Exception:
        pass
    # cmdline (полезно для agave)
    try:
        if needle and needle in _proc_cmdline(pid):
            return True
    except Exception:
        pass
    return False


def detect_client_local(ledger_dir: str | Path | None = None) -> str:
    ledger_dir = str(ledger_dir) if ledger_dir else ""
    found: list[tuple[str, str]] = []

    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        kind = _classify_pid(pid)
        if not kind:
            continue
        if ledger_dir and not _proc_uses_path(pid, ledger_dir):
            continue
        found.append((pid, kind))

    if found:
        kinds = {k for _, k in found}
        if "AGAVE" in kinds and "FD" in kinds:
            return "AGAVE"
        return found[0][1]

    if ledger_dir:
        return detect_client_local(None)
    return "unknown"


def detect_client_remote_type(cfg: SSHSettings, ledger_dir: str | Path | None = None) -> str:
    """
    Гибридный детект клиента на SECONDARY:
      1) Быстрый shell: pgrep + шаблоны 'run|run1|run-agave' для FD.
      2) Если нет FD — ищем agave/solana-validator.
      3) Если не нашли — python-парсер /proc (login_shell=True) с фильтром ledger_dir только для Agave.
      Приоритет: FD > AGAVE > unknown.
    """
    # 1) быстрый FD
    fd_quick = (
        "bash -lc '"
        "pgrep -a fdctl 2>/dev/null | egrep -q \"(^| )run( |$)| run1 | run-agave\" && echo FD && exit 0; "
        "pgrep -a firedancer 2>/dev/null | egrep -q \"(^| )run( |$)| run1 | run-agave\" && echo FD && exit 0; "
        "exit 1'"
    )
    r = run_remote(cfg, fd_quick)
    if (r.stdout or "").strip().upper() == "FD":
        return "FD"

    # 2) быстрый agave
    agave_quick = (
        "bash -lc '"
        "pgrep -ax agave-validator >/dev/null 2>&1 && echo AGAVE && exit 0; "
        "pgrep -ax solana-validator >/dev/null 2>&1 && echo AGAVE && exit 0; "
        "exit 1'"
    )
    r = run_remote(cfg, agave_quick)
    if (r.stdout or "").strip().upper() == "AGAVE":
        return "AGAVE"

    # 3) точный /proc
    ldir = str(ledger_dir) if ledger_dir else ""
    remote_py = rf"""
import os, re
LDIR = {ldir!r}
AGAVE_NAMES = {{'agave-validator','solana-validator'}}
FD_NAMES    = {{'fdctl','firedancer'}}

def rtxt(p, bin=False):
    try:
        with open(p, 'rb' if bin else 'r') as f:
            return f.read()
    except Exception:
        return b'' if bin else ''

def rlink(p):
    try:
        return os.readlink(p)
    except Exception:
        return ''

def proc_uses_path(pid, needle):
    if not needle:
        return True
    proc = f"/proc/{{pid}}"
    try:
        if needle in rlink(os.path.join(proc, "cwd")):
            return True
    except Exception:
        pass
    try:
        for fd in os.listdir(os.path.join(proc, "fd")):
            if needle in rlink(os.path.join(proc, "fd", fd)):
                return True
    except Exception:
        pass
    try:
        args = rtxt(f"/proc/{{pid}}/cmdline", bin=True).replace(b"\\x00", b" ").decode(errors="ignore")
        if needle in args:
            return True
    except Exception:
        pass
    return False

seen_fd = False
seen_agave = False
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        with open(f"/proc/{{pid}}/comm") as f: comm = f.read().strip()
    except Exception:
        comm = ''
    try:
        exe = os.readlink(f"/proc/{{pid}}/exe")
    except Exception:
        exe = ''
    try:
        args = rtxt(f"/proc/{{pid}}/cmdline", bin=True).replace(b"\\x00", b" ").decode(errors="ignore")
    except Exception:
        args = ''

    base_comm = comm.rsplit("/",1)[-1].lower()
    base_exe  = exe.rsplit("/",1)[-1].lower()
    low_args  = (args or '').lower()

    fd_name  = (base_comm in FD_NAMES) or (base_exe in FD_NAMES) or (' firedancer' in low_args)
    run_mode = (' run ' in low_args) or (' run1 ' in low_args) or (' run-agave' in low_args)
    if fd_name and run_mode and not any(tok in low_args for tok in (' set-identity', ' --help', ' --version')):
        seen_fd = True

    agave_hit = (
        (base_comm in AGAVE_NAMES) or
        (base_exe  in AGAVE_NAMES)  or
        re.search(r'(^|\\s)(agave-validator|solana-validator)(\\s|$)', low_args) is not None
    )
    if agave_hit:
        if LDIR:
            if proc_uses_path(pid, LDIR):
                seen_agave = True
        else:
            seen_agave = True

print('FD' if seen_fd else ('AGAVE' if seen_agave else 'unknown'))
"""
    r = run_remote(cfg, "python3 - <<'PY'\n" + remote_py + "\nPY")
    out = (r.stdout or "").strip().upper()
    return out if out in ("FD", "AGAVE", "UNKNOWN") else "unknown"


# ==================== Remote CLI discovery & command build ====================
def _remote_find_keygen(cfg: SSHSettings) -> str:
    cmd = ["/bin/bash", "-lc", "command -v solana-keygen || command -v agave-keygen || echo"]
    res = run_remote(cfg, cmd, login_shell=False)
    path = (res.stdout or "").strip()
    if not path:
        raise RuntimeError("На SECONDARY не найден solana-keygen/agave-keygen в PATH.")
    return remote_expand_path(cfg, path)


def _remote_find_agave_cli(cfg: SSHSettings) -> str:
    """
    Возвращает абсолютный путь к agave-validator или solana-validator на SECONDARY (user=root).
    Порядок:
      1) PATH (login-shell).
      2) Подгрузка профилей ~/.profile, ~/.bash_profile, ~/.bashrc и повторная проверка PATH.
      3) Фоллбек по стандартным путям установки Solana в $HOME/.local/share/solana/install/...
      4) Если ничего — подробная диагностика и ошибка.
    """
    # 1) через PATH «как есть»
    r1 = run_remote(cfg, "command -v agave-validator || command -v solana-validator || echo")
    p1 = (r1.stdout or "").strip()
    if p1:
        return remote_expand_path(cfg, p1)

    # 2) подгрузить профили и повторить
    r2 = run_remote(
        cfg,
        "set -a; "
        "[ -f ~/.profile ] && . ~/.profile; "
        "[ -f ~/.bash_profile ] && . ~/.bash_profile; "
        "[ -f ~/.bashrc ] && . ~/.bashrc; "
        "command -v agave-validator || command -v solana-validator || echo"
    )
    p2 = (r2.stdout or "").strip()
    if p2:
        return remote_expand_path(cfg, p2)

    # 3) фоллбек на типовые абсолютные пути Solana
    probe = r'''
set -e
# active_release
cand1="$HOME/.local/share/solana/install/active_release/bin/agave-validator"
cand2="$HOME/.local/share/solana/install/active_release/bin/solana-validator"
if [ -x "$cand1" ]; then echo "$cand1"; exit 0; fi
if [ -x "$cand2" ]; then echo "$cand2"; exit 0; fi
# последний релиз из releases/*
c3="$(ls -1dt "$HOME"/.local/share/solana/install/releases/*/bin/agave-validator 2>/dev/null | head -n1 || true)"
c4="$(ls -1dt "$HOME"/.local/share/solana/install/releases/*/bin/solana-validator 2>/dev/null | head -n1 || true)"
if [ -n "$c3" ] && [ -x "$c3" ]; then echo "$c3"; exit 0; fi
if [ -n "$c4" ] && [ -x "$c4" ]; then echo "$c4"; exit 0; fi
exit 3
'''.strip()
    r3 = run_remote(cfg, probe)
    p3 = (r3.stdout or "").strip()
    if p3:
        return remote_expand_path(cfg, p3)

    # 4) развернутая диагностика для понимания окружения на SECONDARY
    diag = run_remote(
        cfg,
        'echo "USER=$(id -un) HOME=$HOME PATH=$PATH"; '
        'ls -ld "$HOME/.local/share/solana/install" || true; '
        'ls -ld "$HOME/.local/share/solana/install/active_release/bin" || true; '
        'ls -l "$HOME/.local/share/solana/install/active_release/bin" | egrep -i "agave|solana-vali" || true; '
        'echo DONE'
    )
    raise RuntimeError(
        "На SECONDARY не найден agave-validator/solana-validator ни в PATH, ни в стандартных путях.\n"
        f"--- STDOUT ---\n{(diag.stdout or '').strip()}\n--- STDERR ---\n{(diag.stderr or '').strip()}"
    )


def _remote_find_fdctl(cfg: SSHSettings) -> str:
    cmd = ["/bin/bash", "-lc", "command -v fdctl || echo"]
    res = run_remote(cfg, cmd, login_shell=False)
    path = (res.stdout or "").strip()
    if not path:
        raise RuntimeError("На SECONDARY не найден fdctl в PATH.")
    return remote_expand_path(cfg, path)


def _remote_guess_fd_config(cfg: SSHSettings) -> Optional[str]:
    remote_py = r"""
import os
cands = [
    os.environ.get('FD_CONFIG') or '',
    os.path.expanduser('~/config.toml'),
    os.path.expanduser('~/firedancer/config.toml'),
    os.path.expanduser('~/.config/firedancer/config.toml'),
    '/etc/firedancer/config.toml',
]
for p in cands:
    if p and os.path.isfile(p):
        print(p)
        break
"""
    # тоже без вложенного bash -lc тут — run_remote сам обернёт
    res = run_remote(cfg, "python3 - <<'PY'\n" + remote_py + "\nPY")
    out = (res.stdout or "").strip()
    return remote_expand_path(cfg, out) if out else None


def arm_remote_set_identity(secondary_cfg: SSHSettings, cmd_no_shell: str) -> subprocess.Popen:
    """
    Открываем SSH-сессию заранее: удалённая сторона ждёт ENTER, затем exec <cmd>.
    Важно: тут НЕ login-shell, т.к. команда уже абсолютами.
    """
    remote_sh = f'read -r _; exec {cmd_no_shell}'
    ssh_cmd = build_ssh_command(secondary_cfg, remote_sh)
    return subprocess.Popen(
        ssh_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _build_remote_set_identity_cmd_no_shell(
        remote_client: str,
        secondary_cfg: SSHSettings,
        remote_ledger: Path,
        new_key_path_str: str,
) -> str:
    """Собирает ПОЛНУЮ команду для SECONDARY (без login-shell), только абсолюты."""
    kind = remote_client.upper()
    LEDGER = remote_expand_path(secondary_cfg, str(remote_ledger))
    KEY = remote_expand_path(secondary_cfg, str(new_key_path_str))

    if kind == "AGAVE":
        cli = remote_expand_path(secondary_cfg, getattr(rc, "REMOTE_AGAVE_CLI",
                                                        "$HOME/.local/share/solana/install/active_release/bin/agave-validator"))
        return f'{shlex.quote(cli)} --ledger {shlex.quote(LEDGER)} set-identity {shlex.quote(KEY)}'
    elif kind == "FD":
        fd = remote_expand_path(secondary_cfg, getattr(rc, "REMOTE_FDCTL", "$HOME/firedancer/bin/fdctl"))
        cfg = remote_expand_path(secondary_cfg, getattr(rc, "REMOTE_FD_CONFIG_PATH", "/home/solana/config.toml"))
        return f'{shlex.quote(fd)} set-identity --config {shlex.quote(cfg)} {shlex.quote(KEY)} --force'
    else:
        raise RuntimeError(f"[SECONDARY] unknown client '{remote_client}'")


# =============================== Tower helpers ================================

def remove_tower_on_secondary(pubkey: str, secondary_cfg: SSHSettings, remote_ledger: Path) -> None:
    """
    Удаляет на SECONDARY все файлы tower*-<PUBKEY>.bin в каталоге remote_ledger.
    Бросает исключение, если после удаления что-то осталось.
    """
    dest_dir = remote_expand_path(secondary_cfg, str(remote_ledger))
    remote_py = rf"""
import os
DIR = {dest_dir!r}
PUB = {pubkey!r}
removed = 0
left = 0
try:
    for name in os.listdir(DIR):
        if not name.startswith("tower"): continue
        if not name.endswith(f"-{{PUB}}.bin"): continue
        p = os.path.join(DIR, name)
        try:
            os.unlink(p); removed += 1
        except Exception:
            left += 1
    for name in os.listdir(DIR):
        if name.startswith("tower") and name.endswith(f"-{{PUB}}.bin"):
            left += 1
    print(f"OK {{removed}} {{left}}")
except Exception as e:
    print("ERR", str(e))
"""
    res = run_remote(secondary_cfg, "python3 - <<'PY'\n" + remote_py + "\nPY")
    out = (res.stdout or "").strip()
    if not out.startswith("OK "):
        raise RuntimeError(f"[SECONDARY] удаление tower-файлов не удалось: {out or res.stderr}")
    try:
        _, removed_str, left_str = out.split(maxsplit=2)
        left = int(left_str)
    except Exception:
        raise RuntimeError(f"[SECONDARY] неожиданный ответ при удалении tower-файлов: {out}")
    if left > 0:
        raise RuntimeError(
            f"[SECONDARY] после удаления осталось {left} tower-файл(ов). Проверьте права/путь: {dest_dir}")


def _local_find_agave_cli() -> str | None:
    """Возвращает путь к agave-validator или solana-validator из PATH, либо None."""
    for name in ("agave-validator", "solana-validator"):
        p = shutil.which(name)
        if p:
            return p
    # Если PATH «голый» (systemd/cron) — попробуем login-shell
    proc = run_local(["/bin/bash", "-lc", "command -v agave-validator || command -v solana-validator || echo"])
    path = (proc.stdout or "").strip()
    return path or None


def _local_find_keygen() -> str | None:
    """Возвращает путь к solana-keygen или agave-keygen из PATH, либо None."""
    p = shutil.which("solana-keygen") or shutil.which("agave-keygen")
    if p:
        return p
    # Если PATH «голый» (systemd/cron), попробуем login-shell:
    proc = run_local(["/bin/bash", "-lc", "command -v solana-keygen || command -v agave-keygen || echo"])
    path = (proc.stdout or "").strip()
    return path or None


def get_remote_pubkey_from_keyfile_via_keygen(cfg: SSHSettings, key_path_str: str) -> str:
    """
    SECONDARY: вернуть pubkey из keypair.json.
    Порядок:
      1) Проверяем читаемость ключа (после expand $HOME/~/).
      2) Пытаемся через PATH: solana-keygen/agave-keygen.
      3) Фоллбек: solana address -k.
      4) Резерв: явные пути keygen в $HOME/.local/share/solana/install/...
    ВАЖНО: run_remote по умолчанию даёт login-shell, поэтому передаём ОДНУ строку.
    """
    # аккуратно раскрываем $HOME/~/ на удалёнке один раз
    key_path = remote_expand_path(cfg, key_path_str)

    # 1) ключ читается?
    res_chk = run_remote(cfg, f'[ -r {shlex.quote(key_path)} ] && echo OK || echo NO_KEY')
    if (res_chk.stdout or "").strip() != "OK":
        raise RuntimeError(f"Удалённый ключ не найден/не читается: {key_path}")

    # 2) keygen через PATH
    cmd_kgen = (
        f'(command -v solana-keygen >/dev/null 2>&1 && solana-keygen pubkey {shlex.quote(key_path)}) || '
        f'(command -v agave-keygen  >/dev/null 2>&1 && agave-keygen  pubkey {shlex.quote(key_path)}) || '
        f'echo'
    )
    r1 = run_remote(cfg, cmd_kgen)
    out1 = (r1.stdout or "").strip()
    if out1:
        return out1

    # 3) fallback: solana address -k
    cmd_sol = f'command -v solana >/dev/null 2>&1 && solana address -k {shlex.quote(key_path)} || echo'
    r2 = run_remote(cfg, cmd_sol)
    out2 = (r2.stdout or "").strip()
    if out2:
        return out2

    # 4) резервные явные пути (частая инсталляция у root)
    probe = rf'''
set -e
K1="$HOME/.local/share/solana/install/active_release/bin/solana-keygen"
if [ -x "$K1" ]; then "$K1" pubkey {shlex.quote(key_path)} && exit 0; fi
K2="$(ls -1dt "$HOME"/.local/share/solana/install/releases/*/bin/solana-keygen 2>/dev/null | head -n1 || true)"
if [ -n "$K2" ] && [ -x "$K2" ]; then "$K2" pubkey {shlex.quote(key_path)} && exit 0; fi
exit 3
'''.strip()
    r3 = run_remote(cfg, probe)
    out3 = (r3.stdout or "").strip()
    if out3:
        return out3

    # Диагностика — покажем окружение root на SECONDARY
    diag = run_remote(cfg,
                      f'echo "USER=$(id -un) HOME=$HOME SHELL=$SHELL"; '
                      f'echo "PATH=$PATH"; '
                      f'echo "KEY={shlex.quote(key_path)}"; '
                      f'(ls -l {shlex.quote(key_path)} || true); '
                      f'(command -v solana-keygen || true); '
                      f'(command -v agave-keygen || true); '
                      f'(command -v solana || true); '
                      f'echo DONE'
                      )
    raise RuntimeError(
        "На SECONDARY не найден solana-keygen/agave-keygen и не сработал fallback `solana address -k`.\n"
        f"--- STDOUT ---\n{(diag.stdout or '').strip()}\n--- STDERR ---\n{(diag.stderr or '').strip()}"
    )


# =============================== Tower helpers ================================
def get_local_pubkey_from_keyfile(keyfile: Path) -> str:
    """Возвращает pubkey из локального keypair.json через keygen/address."""
    keyfile = Path(keyfile).expanduser()
    keygen = _local_find_keygen()
    if keygen:
        proc = run_local([keygen, "pubkey", str(keyfile)])
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return (proc.stdout or "").strip()
    # fallback через `solana address -k`
    proc = run_local(["bash", "-lc", f"solana address -k {shlex.quote(str(keyfile))} || echo"])
    out = (proc.stdout or "").strip()
    if out:
        return out
    raise RuntimeError(f"Не удалось получить pubkey из {keyfile}")


def get_local_identity_from_monitor(ledger_path: Path, agave_bin: str = "agave-validator",
                                    wait_sec: float = 3.0) -> str:
    """
    Запускает `agave-validator --ledger <path> monitor`, читает строки до "Identity:",
    затем аккуратно завершает процесс.
    """
    proc = subprocess.Popen(
        [agave_bin, "--ledger", str(ledger_path), "monitor"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    identity: Optional[str] = None
    start = time.time()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line_stripped = line.strip()
            if line_stripped.startswith("Identity:"):
                identity = line_stripped.split(":", 1)[1].strip()
                break
            if time.time() - start > wait_sec:
                break
    finally:
        # Попробуем корректно остановить
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    if not identity:
        raise RuntimeError("Не удалось извлечь Identity из вывода 'agave-validator monitor'")
    return identity
