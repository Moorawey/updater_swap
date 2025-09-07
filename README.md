### RU
## update-hotswap — быстрый и безопасный hot-swap между Agave и Firedancer

Этот инструмент автоматизирует безопасную смену validator identity между двумя узлами (MAIN ↔ SECONDARY) без рестартов процесса. Поддерживаются клиенты Agave (solana-validator/agave-validator) и Firedancer (`fdctl`). Проект совместим со всеми клиентами. Протестирован только на клиентах с патчем на голосование

### Возможности
- Проверка текущей `Identity` через `agave-validator --ledger monitor` (поэтому даже для FD-валидатора нужен установленный Agave CLI).
- Сверка публичных ключей локального и удалённого ключфайла до операции.
- Подробный verbose-лог и устойчивость к transient-ошибкам админ-RPC.

---

## Требования
- Python 3.9+ на MAIN (где запускается скрипт).
- Доступ по SSH с MAIN → SECONDARY (ключевой доступ, без пароля).
- Установленный Agave CLI на ОБОИХ узлах (даже если валидатор — FD):
  - `agave-validator` (или `solana-validator`) актуальной версии Agave 2.3.6.
- Если SECONDARY = FD, то на SECONDARY должны быть:
  - `fdctl` и корректный `config.toml` (путь указывается в `remote_config.py`).

---

## Установка и обновление

### Клонирование
```bash
git clone https://github.com/Moorawey/updater_swap.git
cd update-hotswap
```

### Обновление
```bash
git pull --rebase
```

Файлы проекта:
- `hotswap_for_update.py` — CLI-обёртка.
- `verify_identity.py` — логика сверок и запуск `perform_swap`.
- `swap.py` — быстрые сценарии swap (sequential/armed/bg).
- `remote_config.py` — пути, бинарники и SSH-конфиг SECONDARY.
- `uttils.py` — SSH, обнаружение клиентов, утилиты.

---

## Конфигурация: `remote_config.py`

- `LEDGER_PATH_DEFAULT`: дефолтный путь к леджеру на сервере-MAIN.
- `LOCAL_VALIDATOR_KEY`: путь к ключу валидатора на сервере-MAIN.
- `LOCAL_UNSTAKED_IDENTITY`: путь к unstaked identity (сервер-MAIN).
- `REMOTE_VALIDATOR_KEY`: строка пути к ключу валидатора на SECONDARY (можно с `$HOME`/`~`).
- `REMOTE_LEDGER_PATH`: строка пути к леджеру на сервере-SECONDARY (если отличается от MAIN). Можно оставить `None` и передать через `--remote-ledger`.
- `AGAVE_CLI_LOCAL`: путь к бинарю Agave на MAIN (фиксированная установка).
- `FDCTL_LOCAL`, `FD_CONFIG_LOCAL`: пути к `fdctl` и `config.toml` на MAIN (если используется FD на сервере-MAIN).
- `REMOTE_FDCTL`, `REMOTE_FD_CONFIG_PATH`: пути к `fdctl` и конфигу на SECONDARY.
- `SECONDARY`: объект SSH (собирается из `.env` рядом с `remote_config.py`).

Дополнительно поддерживается опциональная переменная `REMOTE_AGAVE_CLI` (если бинарь Agave на SECONDARY не в стандартных путях).

---

## SSH и файл `.env`

Файл `.env` должен лежать рядом с `remote_config.py`. Пример:
```dotenv
# Обязательные
SSH_HOST=1.2.3.4
SSH_USER=solana

# Опциональные (разумные дефолты уже установлены)
SSH_PORT=22
SSH_IDENTITY_FILE=<ssh path>
SSH_STRICT_HOST_KEY_CHECKING=accept-new
SSH_CONNECT_TIMEOUT=10
SSH_SERVER_ALIVE_INTERVAL=30
SSH_SERVER_ALIVE_COUNT_MAX=3
```

Скрипт использует ControlMaster для ускорения множества SSH-вызовов, так что повторные подключения быстрые.

---

## Быстрый старт

1) Убедитесь, что на ОБОИХ узлах установлен Agave CLI (Jito-Agave 2.3.6) и бинарь доступен в стандартных путях или указан в `remote_config.py`.

2) Запустите проверку и swap:
```bash
python3 hotswap_for_update.py verify --yes
```

По умолчанию:
- используется `--ledger` из `remote_config.py` (или укажите вручную);
- sequential-режим для FD/AGAVE (самый стабильный);
- включён подробный лог (можно отключить флагом `--quiet`);
- выполняются pre-flight проверки путей на MAIN/SECONDARY; при успехе — перенос tower и запуск swap.

Тише логи:
```bash
python3 hotswap_for_update.py verify --yes --quiet
```

---

## Параметры CLI (`hotswap_for_update.py verify`)

- `--ledger /path` — путь к леджеру на MAIN (по умолчанию `LEDGER_PATH_DEFAULT`).
- `--key /path` или `--local-validator-key /path` — ключ валидатора на MAIN.
- `--local-unstaked-identity /path` — unstaked identity на MAIN.
- `--remote-validator-key "$HOME/..."` — путь к ключу валидатора на SECONDARY (разрешены `$HOME`/`~`).
- `--remote-ledger /path` — путь к леджеру на SECONDARY (если отличается от MAIN).
- `--yes` — без подтверждений (non-interactive).
- `--fast` — быстрый режим сверок (без `monitor`, сверяем только ключи).
- `--main-client {AGAVE|FD}` — принудительно указать клиент на MAIN.
- `--remote-client {AGAVE|FD}` — принудительно указать клиент на SECONDARY.
- `--verbose` — подробные логи (команды, rc, stdout/stderr на SECONDARY, очистка tower и пр.).

Примечание: флаг режима FD (sequential/armed/bg) управляется из кода (`swap.perform_swap`), по умолчанию `sequential`.

---

## Типичные сценарии

### 1) Без флагов (используются дефолты из `remote_config.py`)
При запуске покажет всю информацию, с которой будет работать операция смены ключей на обоих серверах и остановится, для подтверждения пользователем клавишей ENTER
```bash
python3 hotswap_for_update.py verify
```

### 2) Быстрый swap без подтверждений
```bash
python3 hotswap_for_update.py verify --yes --fast
```

### 3) Разные пути леджера на узлах
```bash
python3 hotswap_for_update.py verify \
  --ledger /mnt/nvme1/ledger \
  --remote-ledger /home/solana/solana/ledger \
  --yes --verbose
```

### 4) Принудительный выбор клиентов (например, AGAVE → FD)
```bash
python3 hotswap_for_update.py verify \
  --main-client AGAVE --remote-client FD \
  --yes --verbose
```

### 5) Как делаю я (автор), при правильно заполненном `remote_config.py`
```bash
python3 hotswap_for_update.py verify --yes
```

---

## Как это работает (коротко)
1) Скрипт сверяет, что текущая `Identity` на MAIN совпадает с `MAIN key` и с `SECONDARY key` — это гарантия, что вы меняете именно голосующую `Identity`.
2) Предварительно «прогревает» SECONDARY (SSH/ledger) и копирует tower‑файл для текущего PUBKEY (при наличии). Перед копированием удаляются старые `tower*-PUBKEY.bin` на SECONDARY.
3) В sequential-режиме:
   - MAIN: выполняется `set-identity` на unstaked-ключ.
   - После завершения шага на MAIN — SECONDARY: выполняется `set-identity` на валидаторский ключ.
4) В verbose-режиме печатаются команды, rc/stdout/stderr на SECONDARY и статусы очистки tower.

---

## Частые вопросы и ошибки

- “UNKNOWN client на SECONDARY” — укажите корректный `--remote-ledger` (или задайте `REMOTE_LEDGER_PATH`), либо принудительно `--remote-client`. Детектор ищет процессы и сверяет по пути леджера.
- “admin rpc error: oneshot canceled” (Agave) — транзиентная ошибка админ-RPC; повтор обычно проходит. Скрипт в verbose-режиме покажет rc/stdout/stderr и текущую `Identity`.
- FD зависает на `set-identity` — чаще всего гонка (одновременно идут два `set-identity`) либо `fdctl` не прикрепился к реальному admin/shmem процесса. Убедитесь, что используется правильный `--config`/shmem и нет других активных `fdctl set-identity`.

---

## Рекомендации по эксплуатации
- Держите Agave CLI доступным на обоих узлах: он нужен для `monitor` и для ключевых утилит.
- Для FD используйте версии `fdctl` и запущенного firedancer из одной сборки.

---

## Лицензия
Документация и скрипты предоставляются «как есть». Используйте с учётом рисков эксплуатации валидатора и соблюдайте требования сети.


### EN
---

## update-hotswap — fast and safe hot-swap between Agave and Firedancer (EN)

This tool automates a safe validator identity change between two nodes (MAIN ↔ SECONDARY) without restarting the process. It supports Agave (solana-validator/agave-validator) and Firedancer (`fdctl`). The project is compatible with all clients. Tested only on clients with a patched voting flow.

### Features
- Reads current `Identity` via `agave-validator --ledger monitor` (therefore Agave CLI is required even for FD validators).
- Compares public keys from local and remote keypair files before the operation.
- Detailed verbose logging and resilience to transient admin-RPC errors.

---

## Requirements
- Python 3.9+ on MAIN (where you run the script).
- SSH access from MAIN → SECONDARY (key-based, passwordless).
- Agave CLI installed on BOTH nodes (even if the validator is FD):
  - `agave-validator` (or `solana-validator`) of the current Agave 2.3.6 version.
- If SECONDARY = FD, the SECONDARY node must have:
  - `fdctl` and a valid `config.toml` (path configured in `remote_config.py`).

---

## Installation & Update

### Clone
```bash
git clone https://github.com/Moorawey/updater_swap.git
cd update-hotswap
```

### Update
```bash
git pull --rebase
```

Project files:
- `hotswap_for_update.py` — CLI wrapper.
- `verify_identity.py` — verification logic and `perform_swap` entry point.
- `swap.py` — fast swap scenarios (sequential/armed/bg).
- `remote_config.py` — paths, binaries and SECONDARY SSH config.
- `uttils.py` — SSH, client detection, helpers.

---

## Configuration: `remote_config.py`

- `LEDGER_PATH_DEFAULT`: default ledger path on the MAIN server.
- `LOCAL_VALIDATOR_KEY`: validator key path on the MAIN server.
- `LOCAL_UNSTAKED_IDENTITY`: unstaked identity path (MAIN server).
- `REMOTE_VALIDATOR_KEY`: validator key path on SECONDARY (strings may use `$HOME`/`~`).
- `REMOTE_LEDGER_PATH`: ledger path on the SECONDARY server (if it differs from MAIN). You can leave it `None` and pass `--remote-ledger` instead.
- `AGAVE_CLI_LOCAL`: Agave binary path on MAIN (fixed installation).
- `FDCTL_LOCAL`, `FD_CONFIG_LOCAL`: `fdctl` and `config.toml` paths on MAIN (if FD is used on the MAIN server).
- `REMOTE_FDCTL`, `REMOTE_FD_CONFIG_PATH`: `fdctl` and config paths on SECONDARY.
- `SECONDARY`: SSH settings object (built from `.env` next to `remote_config.py`).

Additionally, optional `REMOTE_AGAVE_CLI` is supported (set this if Agave binary on SECONDARY is not in standard locations).

---

## SSH and `.env`

Place `.env` next to `remote_config.py`. Example:
```dotenv
# Required
SSH_HOST=1.2.3.4
SSH_USER=solana

# Optional (reasonable defaults already set)
SSH_PORT=22
SSH_IDENTITY_FILE=<ssh path>
SSH_STRICT_HOST_KEY_CHECKING=accept-new
SSH_CONNECT_TIMEOUT=10
SSH_SERVER_ALIVE_INTERVAL=30
SSH_SERVER_ALIVE_COUNT_MAX=3
```

The script uses SSH ControlMaster to speed up multiple SSH calls, so repeated connections are fast.

---

## Quick Start

1) Make sure Agave CLI is installed on BOTH nodes (Jito-Agave 2.3.6) and the binary is either in PATH or configured in `remote_config.py`.

2) Run verification and swap:
```bash
python3 hotswap_for_update.py verify --yes
```

By default:
- `--ledger` is taken from `remote_config.py` (or set it explicitly);
- sequential mode is used for FD/AGAVE (stable and fast);
- verbose logging is enabled (use `--quiet` to reduce output);
- pre-flight checks validate MAIN/SECONDARY paths; on success — tower copy and swap start.

Quieter logs:
```bash
python3 hotswap_for_update.py verify --yes --quiet
```

---

## CLI parameters (`hotswap_for_update.py verify`)

- `--ledger /path` — ledger path on MAIN (defaults to `LEDGER_PATH_DEFAULT`).
- `--key /path` or `--local-validator-key /path` — validator key on MAIN.
- `--local-unstaked-identity /path` — unstaked identity on MAIN.
- `--remote-validator-key "$HOME/..."` — validator key path on SECONDARY (`$HOME`/`~` allowed).
- `--remote-ledger /path` — ledger path on SECONDARY (if different from MAIN).
- `--yes` — non-interactive (no confirmations).
- `--fast` — fast verification (skip `monitor`, compare keys only).
- `--main-client {AGAVE|FD}` — force client on MAIN.
- `--remote-client {AGAVE|FD}` — force client on SECONDARY.
- `--verbose` — detailed logs (commands, rc, stdout/stderr on SECONDARY, tower cleanup, etc.).

Note: FD mode (sequential/armed/bg) is controlled in code (`swap.perform_swap`), default is `sequential`.

---

## Common Scenarios

### 1) No flags (use defaults from `remote_config.py`)
On start it shows all parameters that will be used for the key switch on both nodes and pauses for confirmation by pressing ENTER.
```bash
python3 hotswap_for_update.py verify
```

### 2) Fast swap without confirmations
```bash
python3 hotswap_for_update.py verify --yes --fast
```

### 3) Different ledger paths on nodes
```bash
python3 hotswap_for_update.py verify \
  --ledger /mnt/nvme1/ledger \
  --remote-ledger /home/solana/solana/ledger \
  --yes --verbose
```

### 4) Force clients (e.g. AGAVE → FD)
```bash
python3 hotswap_for_update.py verify \
  --main-client AGAVE --remote-client FD \
  --yes --verbose
```

---

## How it works (short)
1) The script checks that current `Identity` on MAIN equals `MAIN key` and `SECONDARY key` — ensuring you are changing the voting `Identity`.
2) SECONDARY is prewarmed (SSH/ledger) and the tower file for the current PUBKEY is copied to SECONDARY (if present). Old `tower*-PUBKEY.bin` files are removed on SECONDARY before copying.
3) In sequential mode:
   - MAIN: run `set-identity` to the unstaked key.
   - After MAIN completes — SECONDARY: run `set-identity` to the validator key.
4) In verbose mode the script prints commands, rc/stdout/stderr on SECONDARY, and tower cleanup status.

---

## FAQ & Errors

- “UNKNOWN client on SECONDARY” — specify the correct `--remote-ledger` (or set `REMOTE_LEDGER_PATH`), or force `--remote-client`. The detector looks for processes and filters by ledger path.
- “admin rpc error: oneshot canceled” (Agave) — transient admin-RPC error; retry usually succeeds. Verbose mode will print rc/stdout/stderr and the current `Identity`.
- FD hangs on `set-identity` — most often a race (two `set-identity` in parallel) or `fdctl` did not attach to the real admin/shmem instance. Make sure the correct `--config`/shmem is used and there are no other active `fdctl set-identity` processes.

---

## Operational recommendations
- Keep Agave CLI available on both nodes: needed for `monitor` and key utilities.
- For FD, use matching versions of `fdctl` and the running firedancer (same build).

---

## License
Documentation and scripts are provided “as is”. Use with awareness of validator operations risks and network requirements.
