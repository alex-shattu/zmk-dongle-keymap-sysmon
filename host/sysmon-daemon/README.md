# sysmon-daemon

> **Note:** this document is in Russian. Everything you need to *run* the
> daemon is the two commands under «Установка демона» plus the launchd section;
> the protocol is summarised in English in the repository README.

Демон для macOS: собирает метрики системы (CPU, RAM, сеть, диск, температура/thermal state)
и раз в 500 мс шлёт их по USB CDC-ACM (serial) на донгл с TFT-экраном
ST7789V 240×240. Протокол — текстовый, построчный (`S3|...`).

## Прошивка донгла

Прошивка собирается в GitHub Actions вашего конфиг-репозитория — см. корневой
README. Скачать артефакт, дважды быстро нажать reset на плате (появится диск
**NICENANO**) и скопировать на него `zmk.uf2`.

Донгл выставляет **два** USB-интерфейса: HID (клавиатура/мышь) и serial
(`/dev/cu.usbmodem*`). Демон находит нужный порт сам handshake'ом: шлёт `PING`,
sysmon-порт отвечает `SYSMON1`. Ни VID/PID, ни номер порта значения не имеют.

## Установка демона

Нужен Python ≥ 3.9. Из каталога `host/sysmon-daemon`:

```sh
python3 -m venv ~/.venvs/sysmon
~/.venvs/sysmon/bin/pip install -e .
```

Если репозиторий лежит на **внешнем диске**, а демон планируется запускать через
launchd — ставить лучше без `-e` (`pip install .`): тогда код копируется в venv на
внутреннем диске и агент не зависит ни от разрешения macOS на съёмный том, ни от
того, подключён ли диск в момент входа в систему (см. «Доступ к съёмному тому» ниже).
Минус: после правок в репозитории нужно переустанавливать пакет.

### Ручной запуск

```sh
~/.venvs/sysmon/bin/python -m sysmon_daemon --verbose
```

Флаги:

| Флаг         | Описание                                                              |
|--------------|-----------------------------------------------------------------------|
| `--interval` | период отправки в секундах (по умолчанию `0.5`)                        |
| `--port`     | конкретный порт (например `/dev/cu.usbmodem14201`) — пропустить автопоиск |
| `--verbose`  | debug-логирование (в stderr)                                          |

Если устройство не подключено или пропало — демон сам переподключается
(backoff 1–5 с), убивать/перезапускать его не нужно.

## Автозапуск через launchd

1. Скопировать шаблон, подставив свой домашний каталог (launchd **не** раскрывает
   `~` и переменные окружения — пути обязаны быть абсолютными):

   ```sh
   mkdir -p ~/Library/LaunchAgents ~/Library/Logs
   sed "s|/Users/YOURUSER|$HOME|g" com.user.sysmon-daemon.plist \
     > ~/Library/LaunchAgents/com.user.sysmon-daemon.plist
   ```

   Если venv не в `~/.venvs/sysmon`, поправить путь к `python` в получившемся файле.

2. Загрузить и запустить агента:

   ```sh
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.sysmon-daemon.plist
   launchctl kickstart -k gui/$(id -u)/com.user.sysmon-daemon
   ```

3. Логи и проверка состояния:

   ```sh
   tail -f ~/Library/Logs/sysmon-daemon.log
   launchctl print gui/$(id -u)/com.user.sysmon-daemon | grep -E 'state|last exit'
   ```

   `last exit code = 78: EX_CONFIG` означает, что launchd не нашёл исполняемый файл —
   почти всегда это неподставленный путь в plist (лога при этом тоже не будет,
   поскольку он пишется по такому же пути).

Остановить/выгрузить:

```sh
launchctl bootout gui/$(id -u)/com.user.sysmon-daemon
```

### Доступ к съёмному тому (editable-установка с внешнего диска)

Если пакет установлен через `pip install -e` и репозиторий лежит на внешнем диске,
launchd-агент упрётся в TCC: macOS требует разрешение «Removable Volumes», а показать
диалог фоновому агенту толком негде. Симптомы — на дисплее `NO DATA`, а в логе:

```
PermissionError: [Errno 1] Operation not permitted: '/Volumes/.../sysmon_daemon/__init__.py'
```

До ответа на диалог процесс просто висит в `open()` (лог пустой, порт не открыт);
после отказа — падает с ошибкой выше, и повторный диалог уже не появляется.

Варианты:

- **Надёжно**: переустановить пакет без `-e` — код переедет на внутренний диск,
  разрешение вообще не потребуется.
- **Разрешить доступ**: сбросить решение TCC и перезапустить агента, затем нажать
  «Разрешить» в диалоге. `tccutil` умеет только bundle-id, а для CLI-бинарника python
  запись хранится под хэшем пути, поэтому сбрасывать приходится службу целиком
  (другие приложения переспросят доступ при следующем обращении):

  ```sh
  launchctl bootout gui/$(id -u)/com.user.sysmon-daemon
  tccutil reset SystemPolicyRemovableVolumes
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.sysmon-daemon.plist
  ```

  Разрешение привязано к конкретному пути бинарника python, так что после обновления
  Homebrew-питона (меняется путь вида `.../Cellar/python@3.14/3.14.6/...`) диалог
  появится снова.

## Ограничения на Apple Silicon

- **Температура**: непривилегированного источника температуры CPU нет
  (`pmset -g therm` показывает "CPU die temperature" только на Intel),
  поэтому на экране вместо температуры будет `-`.
- **Thermal state**: определяется эвристикой по `pmset -g therm`
  (строка `CPU_Speed_Limit` при троттлинге; "No thermal warning..." = `nominal`),
  fallback — `sysctl kern.thermalpressurelevel`. Если распарсить не удалось — `-`.
- **RAM pressure**: `sysctl kern.memorystatus_vm_pressure_level` даёт только грубые
  уровни (1/2/4), а не проценты, поэтому как pressure отправляется
  `psutil.virtual_memory().percent` (занято относительно доступной памяти).
  Число на экране не показывается — прошивка использует его только для цвета MEM-бара.

## Протокол S3 (для отладки)

Хост шлёт одну строку на выборку, `\n` в конце, поля через `|`, N/A = `-`:

```
S3|<cpu%>|<ram_used_mb>|<ram_free_mb>|<ram_pressure%>|<net_up_kbps>|<net_down_kbps>|<disk_used_gb>|<disk_free_gb>|<temp_c>|<thermal_state>|<net_iface>
```

| Поле            | Формат                                   |
|-----------------|-------------------------------------------|
| `cpu%`          | целое 0–100 (усреднение по всем ядрам)    |
| `ram_used_mb`, `ram_free_mb` | целые, МиБ (÷1024²)          |
| `ram_pressure%` | целое 0–100 или `-`                       |
| `net_up_kbps`, `net_down_kbps` | КБ/с, 1 знак после точки   |
| `disk_used_gb`, `disk_free_gb` | ДЕСЯТИЧНЫЕ ГБ (÷10⁹), 1 знак после точки |
| `temp_c`        | °C, 1 знак после точки, или `-`           |
| `thermal_state` | `nominal`/`fair`/`serious`/`critical`/`-` |
| `net_iface`     | токен 1–7 симв. из `[A-Z0-9-]` (`WI-FI`/`ETH`/`VPN`) или `-` |

Пример: `S3|23|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal|WI-FI`

S3 заменяет total-поля S1/S2 на пары «занято/свободно»:

- **RAM**: `ram_used_mb` = total − available (≈ «Memory Used» в Activity
  Monitor), `ram_free_mb` = available (реально доступная память, включая
  inactive); сумма used + free = объём RAM.
- **DISK**: десятичные ГБ, как показывает сама macOS. `disk_free_gb` —
  доступное место **с учётом purgeable** (как Finder/Настройки; читается
  через `osascript` → `NSURLVolumeAvailableCapacityForImportantUsageKey`,
  кэш 30 с, при ошибке — fallback на psutil). `disk_used_gb` — занято
  системной volume group (тома `/` + `/System/Volumes/Data`), т.е. то же
  «занято», что в Finder. Поэтому used + free ≠ объёму диска: purgeable и
  служебные тома (VM, Preboot…) в сумму не входят.

Прошивка принимает и старые S1/S2 (конвертируя total-поля), и S3;
демон всегда шлёт S3. `net_iface` и `temp_c`/`thermal_state` показывает только
UI отдельного устройства (бейдж в NET-тайле скрывается при `-`); на донгле для
них нет места, и они игнорируются.

Тип интерфейса определяется по `route -n get default` (устройство
default-маршрута) + `networksetup -listallhardwareports` (порт «Wi-Fi» →
`WI-FI`, остальные аппаратные порты → `ETH`); default-маршрут через туннель
(`utun*` и т.п., т.е. активный VPN) → `VPN`. Результат кэшируется на ~30 с;
любая ошибка → `-`.

Handshake: `PING` → `SYSMON1`. Неизвестные строки прошивка молча игнорирует;
без пакетов дольше 3 с отдельное устройство показывает "NO DATA", а донгл
заменяет значения нижней половины на `--` (верхняя половина продолжает
работать — клавиатура от демона не зависит).

Проверить руками (сначала остановить демона, порт занимает один процесс):

```sh
screen /dev/cu.usbmodemXXX 115200
# набрать PING и Enter — sysmon-порт ответит SYSMON1
# затем можно вставить строку S3|... из примера выше
# выход из screen: Ctrl-A, затем K
```

## Тесты

```sh
~/.venvs/sysmon/bin/pip install -e '.[dev]'
~/.venvs/sysmon/bin/python -m pytest tests/
```
