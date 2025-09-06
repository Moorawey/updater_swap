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
- будет выведен план, выполнены сверки и запущен быстрый swap.

Для подробного логирования. Запуск через --verbose запускает скрипт без ручного подтверждения через клавишу ENTER
```bash
python3 hotswap_for_update.py verify --yes --verbose
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

---

## Как это работает (коротко)
1) Скрипт сверяет, что текущая `Identity` на MAIN совпадает с `MAIN key` и с `SECONDARY key` — это гарантия, что вы меняете именно голосующую `Identity`.
2) Предварительно «прогревает» SECONDARY (SSH/ledger) и очищает tower-файлы SECONDARY для текущего PUBKEY (Актуально только для патченных клиентов).
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


