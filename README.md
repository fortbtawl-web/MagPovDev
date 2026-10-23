# Hex Casting — Reborn by Magetka

**Неофициальный порт Hex Casting на Minecraft 1.21.1 / NeoForge 21.1.x**
**Unofficial port of Hex Casting to Minecraft 1.21.1 / NeoForge 21.1.x**

> **Reborn by Magetka**
>
> Original mod: Hex Casting
> Original author: petrak_at (petrak@, aka gamma-delta) and the FallingColors contributors
> Port / adaptation: Magetka

Это **не** официальное обновление от автора оригинального мода. Об ошибках порта сообщайте в
[трекер этого репозитория](https://github.com/fortbtawl-web/MagPovDev/issues), а не в трекер оригинала.

This is **not** an official release by the original author. Please report bugs of this port to
[this repository's issue tracker](https://github.com/fortbtawl-web/MagPovDev/issues), not to the upstream tracker.

Hex Casting — мод о программируемой магии, вдохновлённый PSI: заклинания (хексы) создаются рисованием узоров
посохом на гексагональной сетке. Оригинал: [CurseForge](https://www.curseforge.com/minecraft/mc-mods/hexcasting) ·
[Modrinth](https://modrinth.com/mod/hex-casting) · [исходники](https://github.com/FallingColors/HexMod) ·
[документация](https://hexcasting.hexxy.media/).

---

## RU

### Основа порта

Порт основан на официальной ветке разработки [`FallingColors/HexMod@1.21`](https://github.com/FallingColors/HexMod/tree/1.21)
(коммит `7d5c157`, версия `0.12.0-devel`), где авторы оригинала уже перенесли игровую логику на 1.21.1
(Data Components, новый сетевой API, codec-и). Эта основа выбрана как самая надёжная: в ней сохранены все механики
оригинала. Сторонний порт [HexCast NEOFORGED](https://github.com/a1exrom/hex_cast_NEOFORGED) (0.11.1) был изучен,
но не использован: он основан на более старом коде и собирается только с локальными jar-файлами, которых нет в репозитории.

Что сделано в этом порте — см. [PORT_CHANGELOG.md](PORT_CHANGELOG.md). Известные проблемы — [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

### Требования (установка)

| Мод | Версия | Обязателен | Где взять |
|---|---|---|---|
| Minecraft | 1.21.1 | да | |
| NeoForge | 21.1.172 или новее | да | https://neoforged.net |
| Kotlin for Forge | 5.8.0 или новее (5.x) | да | https://modrinth.com/mod/kotlin-for-forge |
| PAUCAL | 0.7.0 (`paucal-0.7.0+neoforge-1.21.1.jar`) | да | https://modrinth.com/mod/paucal |
| Architectury API | 13.0.2 или новее | да (нужен PAUCAL) | https://modrinth.com/mod/architectury-api |
| Patchouli | 1.21.1-93 или новее | да | https://modrinth.com/mod/patchouli |
| Caelus API | 7.0.1+1.21.1 | да | https://modrinth.com/mod/caelus |
| Inline | 1.21.1-1.2.2 или новее | да | https://modrinth.com/mod/inline |
| Cloth Config API | 15.0.140+neoforge | да (нужен Inline) | https://modrinth.com/mod/cloth-config |
| Curios API | 9.5.1+1.21.1 | нет | https://modrinth.com/mod/curios |
| JEI / EMI | 19.x / 1.1.x | нет | |
| Pehkui | 3.8.x | нет | |

### Установка

1. Установите NeoForge 21.1.x для Minecraft 1.21.1 (клиент и/или сервер).
2. Положите в папку `mods` файл `hexcasting-reborn-by-magetka-1.21.1.jar` и все обязательные зависимости из таблицы.
3. Мод нужен **и на клиенте, и на сервере**.
4. Миры с Hex Casting для 1.20.1 **не поддерживаются** (см. KNOWN_ISSUES.md) — начинайте новый мир.

### Сборка

Нужен JDK 21 (если его нет, Gradle скачает его сам через toolchains).

```bash
./gradlew build            # Linux / macOS
gradlew.bat build          # Windows
```

Готовый мод: `build/libs/hexcasting-reborn-by-magetka-1.21.1.jar`.

Полезные задачи:

| Задача | Что делает |
|---|---|
| `./gradlew runClient` | запуск клиента в dev-окружении (с JEI, Curios) |
| `./gradlew runServer` | запуск выделенного сервера (`run/server`, примите EULA в `run/server/eula.txt`) |
| `./gradlew runGameTestServer` | серверные GameTest-проверки заклинаний (см. ниже) |
| `./gradlew runData` | регенерация `src/generated/resources` (модели, рецепты, теги, лут, достижения) |
| `./gradlew test` | юнит-тесты оригинала |

### Проверка

* `./gradlew build` — сборка и 8 юнит-тестов оригинала проходят.
* `./gradlew runGameTestServer` — 6 GameTest на выделенном сервере проходят: регистрация предметов/блоков/действий,
  исполнение узоров (Mind's Reflection, Archer's Purification), Conjure Block с расходом заряженного аметиста,
  обработка мишапов, сохранение хекса в фокусе (диск + сеть), сетевой codec результата каста.
* Выделенный сервер стартует без ошибок мода; клиент проверен вручную (главное меню, список модов,
  книга Hex Notebook, текстуры предметов, GUI посоха, рисование узоров мышью с исполнением на сервере).

---

## EN

This port is based on the official development branch
[`FallingColors/HexMod@1.21`](https://github.com/FallingColors/HexMod/tree/1.21) (commit `7d5c157`, `0.12.0-devel`),
where the upstream team already migrated the game logic to 1.21.1. It turns it into a NeoForge-only
[ModDevGradle](https://github.com/neoforged/ModDevGradle) project, makes it run with publicly released dependencies,
fixes port bugs found during testing and adds automated server-side GameTests.
See [PORT_CHANGELOG.md](PORT_CHANGELOG.md) and [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

**Install:** NeoForge 21.1.172+ for 1.21.1, then put `hexcasting-reborn-by-magetka-1.21.1.jar` and the required
dependencies from the table above (Kotlin for Forge, PAUCAL 0.7.0, Architectury API, Patchouli, Caelus, Inline,
Cloth Config) into `mods` on both client and server. 1.20.1 worlds are not migrated.

**Build:** JDK 21, `./gradlew build` (or `gradlew.bat build` on Windows) → `build/libs/hexcasting-reborn-by-magetka-1.21.1.jar`.

## License / Лицензия

MIT — see [LICENSE.txt](LICENSE.txt). Copyright © 2021 gamma-delta (petrak@). The original copyright and license
notices are kept unchanged; see [NOTICE.md](NOTICE.md) for attribution of this port and bundled third-party code.
