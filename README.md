# ardupilot-repro

[![CI](https://github.com/alexqzm/ardupilot-repro/actions/workflows/ci.yml/badge.svg)](https://github.com/alexqzm/ardupilot-repro/actions/workflows/ci.yml)

Воспроизводимость официальных прошивок [ArduPilot](https://ardupilot.org): можно ли, имея только
открытый исходный код, получить побитово ту же прошивку, что опубликована на
[firmware.ardupilot.org](https://firmware.ardupilot.org). Инструмент `aprepro` пересобирает
прошивки из коммита выпуска с закреплённым тулчейном, сверяет их с официальными, разбирает
расхождения и выпускает аттестацию пересборки — свидетельство s24 «независимая контрольная
пересборка» для гейта [scii-gate](https://github.com/alexqzm/scii-gate).

Исследование — часть работы над диссертацией «Метод обеспечения целостности цепочки сборки и
обновления программного обеспечения киберфизических систем на основе графа доказательств»:
проверка того, что свидетельства s23 и s24 выполнимы для реальной цепочки выпуска ПО автопилота
БВС, и при каких условиях.

## Результаты

План зафиксирован до прогона: 12 групп «изделие × выпуск» (Copter и Plane 4.4.4, 4.5.7, 4.6.3,
4.7.2; Rover 4.4.0, 4.5.7, 4.6.3, 4.7.2), в каждой 8 плат постоянной панели и 4 случайные платы, плюс
группа сборки из тега — 152 сборки. Полная таблица — [`docs/results.md`](docs/results.md).

<!-- results:begin -->
Результатов пока нет.
<!-- results:end -->

**Находки до прогона** (подробно — [`docs/methodology.md`](docs/methodology.md#находки-до-прогона)):

- **Прошивка зависит от реализации zlib в интерпретаторе Python.** ArduPilot сжимает встроенные
  файлы ROMFS модулем `zlib` интерпретатора, который выполняет сборку. Copter 4.7.2 для CubeOrange,
  собранный интерпретатором с zlib-ng, на 112 байт длиннее официального; с классическим zlib —
  совпадает побитово. Распакованное содержимое ROMFS в обоих случаях одинаково. Эта зависимость
  не упомянута в документированной среде сборки ArduPilot.
- **Тег Copter-4.6.3 не указывает на коммит официальной сборки.** Официальные прошивки Copter
  stable-4.6.3 собраны из `3fc7011a`, тег указывает на следующий коммит `92b0cd78`, который меняет
  тип выпуска с BETA на OFFICIAL. В образах официальных прошивок зашит тип BETA, рядом лежит
  `firmware-version.txt` с OFFICIAL.

## Как это работает

```text
манифест firmware.ardupilot.org ─► план (группы, панель, случайная выборка, зерно = SHA-256 манифеста)
                                     │
для каждой платы:  официальные .apj, git-version.txt, firmware-version.txt
                   исходники по SHA коммита + подмодули ─► waf configure --board ─► waf copter|plane|rover
                   тулчейн gcc-arm-none-eabi-10-2020-q4-major (SHA-256 закреплён), Python 3.11 с классическим zlib
                                     │
                   сверка .apj ─► reproducible | image-identical | not-reproducible
                   при расхождении: области различий, символы ELF, разбор ROMFS
                   аттестация in-toto, предикат urn:scii:predicate:rebuild:v1
```

| Модуль | Роль |
|---|---|
| `aprepro/manifest.py`, `aprepro/plan.py` | манифест прошивок, группы и выборка плат |
| `aprepro/source.py`, `aprepro/toolchain.py`, `aprepro/build.py` | исходники по коммиту, тулчейн, сборка |
| `aprepro/apj.py`, `aprepro/compare.py`, `aprepro/diagnose.py` | разбор `.apj`, сверка, разбор расхождений по ELF и ROMFS |
| `aprepro/attest.py`, `aprepro/report.py` | аттестация пересборки, сводка |

## Запуск

### В GitHub Actions

Workflow [`rebuild`](.github/workflows/rebuild.yml) запускается вручную (Actions → rebuild → Run
workflow): каждая группа плана — отдельная задача на `ubuntu-24.04`. Результаты в JSON (без
двоичных файлов) — артефакт `results-all`, сводка — в итогах запуска. Параметр `attest` включает
бесключевую подпись аттестаций пересборки через Sigstore; подпись попадает в публичный журнал
прозрачности Rekor навсегда, поэтому по умолчанию выключена.

### Локально

Нужны Linux x86_64, Python 3.11, git, tar и bzip2, около 1,5 ГБ на исходники одного коммита со сборкой
и 0,7 ГБ на тулчейн. Сборка одной платы на двух ядрах — около 6 минут.

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r ci/requirements-build.txt && pip install -e ".[test]"
python -c "import zlib; print(zlib.ZLIB_RUNTIME_VERSION)"   # не должно быть zlib-ng
aprepro run plans/study-2026-11.json --group Copter-4.7.2 --board CubeOrange
aprepro report plans/study-2026-11.json
```

Если системный Python собран с zlib-ng (например, в Fedora с версии 40 системный zlib заменён
совместимым zlib-ng — [Changes/ZlibNGTransition](https://fedoraproject.org/wiki/Changes/ZlibNGTransition)),
возьмите интерпретатор с классическим zlib, например `uv python install 3.11`.

Другие команды: `aprepro show файл.apj` — метаданные, дайджесты и версия, зашитая в образ;
`aprepro compare официальный.apj пересобранный.apj` — сверка двух файлов; `aprepro plan` —
новый план по свежему манифесту.

### Проверка подписанной аттестации

```bash
cosign verify-blob-attestation --type urn:scii:predicate:rebuild:v1 \
  --bundle results/study-2026-11/Copter-4.7.2/CubeOrange.sigstore.json \
  --certificate-identity-regexp '^https://github.com/alexqzm/ardupilot-repro/\.github/workflows/rebuild\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  arducopter.apj    # официальный файл с firmware.ardupilot.org
```

## Ограничения

Три изделия, четыре линейки и 12 плат на группу; Sub, вертолётные варианты, Tracker, AP_Periph и
форматы кроме `.apj` не проверялись. Среда официального сервера сборки не опубликована —
реализация zlib на нём выведена из того, какой вывод совпадает. SHA-256 тулчейна закреплён по
первой загрузке. Совпадение подтверждает происхождение прошивки из коммита, а не безопасность кода.

## Связанные репозитории

- [scii-gate](https://github.com/alexqzm/scii-gate) — гейт допуска обновлений по графу доказательств; стенд ArduPilot Copter-4.5.7;
- [scii-verify](https://github.com/alexqzm/scii-verify) — квитанция допуска SCII-R v1 и её проверка на устройстве;
- [cps-supply-chain-atlas](https://github.com/alexqzm/cps-supply-chain-atlas) — атлас атак на цепочки поставок ПО КФС;
- [source-match](https://github.com/alexqzm/source-match) — соответствие выпускного архива подписанному тегу.

## In English

Are official ArduPilot firmware files on firmware.ardupilot.org bit-for-bit reproducible from the
public source? `aprepro` rebuilds them from the release commit with the pinned toolchain,
compares the `.apj` files, explains mismatches (byte regions, ELF symbols, ROMFS blobs) and emits an
in-toto rebuild attestation usable as SCII evidence s24. A pre-registered plan covers 152 builds
(Copter, Plane and Rover; the latest release of the 4.4–4.7 lines; a fixed panel of boards plus
a seeded random sample). Findings so far: the firmware depends on the zlib implementation linked
into the Python interpreter that runs the build (ROMFS compression; zlib-ng output differs from
classic zlib), and the Copter-4.6.3 tag points to a commit later than the one the official binaries
were built from.

## Лицензия

Код и документация — Apache License 2.0 ([`LICENSE`](LICENSE)). В репозитории нет кода и двоичных
файлов ArduPilot (GPLv3); `data/manifest-2026-11-06.json.gz` — снимок манифеста firmware.ardupilot.org,
сохранённый для воспроизводимости выборки. Как ссылаться — [`CITATION.cff`](CITATION.cff).
