# Known issues — Hex Casting, Reborn by Magetka (0.12.0-reborn.1)

The port is based on upstream's in-development `0.12.0-devel` code for 1.21.1. Issues inherited from upstream
are listed with their upstream issue number (https://github.com/FallingColors/HexMod/issues).

## Port / environment

- **No world migration from 1.20.1.** Items and blocks stored with 1.20.1 NBT are not converted to 1.21 data
  components (upstream #1214). Start a new world; do not load 1.20.1 Hex Casting worlds.
- **Not verified in this environment:** audio playback (no sound device in the test sandbox), long multiplayer sessions
  with several real players, spell circles (impetus + slates) in-game, Curios/JEI/EMI/Pehkui integrations at runtime,
  and compatibility with Hex Casting addons (Hexal, HexDebug, ...). Addons built for 1.20.1 will not work.
- PAUCAL 0.7.0 for NeoForge is marked *alpha* by its author and requires Architectury API.
- In dev runs Inline/Create compatibility mixins log harmless `ClassNotFoundException` warnings for Create classes
  when Create is not installed.

## Inherited from upstream (selection)

- Iris' Gambit can kick the user (#1152).
- Stale entity iotas read from iota holders can cause errors / disconnects (#1160, #1246).
- Dust and charged amethyst not dropping from clusters with some mods (#1309); silk touch on amethyst clusters (#1282).
- Copying a Patterned Slate gives a blank slate (#1099).
- Craft Cypher mishaps with staff in offhand and cypher in main hand (#1250).
- Opening the inventory refreshes entity-iota items (#1097).
- Jeweler's Hammer cannot be enchanted (#1182).
- The hex armor is not finished (#1257).
