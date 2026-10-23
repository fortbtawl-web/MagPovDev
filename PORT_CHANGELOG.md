# Port changelog — Hex Casting, Reborn by Magetka

## 0.12.0-reborn.1 (Minecraft 1.21.1, NeoForge 21.1.172+)

### What changed from Hex Casting 0.11.x (Minecraft 1.20.1)

Taken over from the official upstream `1.21` development branch (see `CHANGELOG.md`, section `[UNRELEASED]`):

- Minecraft 1.21.1: item NBT replaced by **Data Components**, networking moved to `CustomPacketPayload` +
  `StreamCodec`, data-driven registries and codecs for iotas, pigments, casting images and recipes.
- New patterns: Simulate, Coalescing Distillation; new Ancient Cypher hex; `hex_unbreakable` block tag;
  mishap for nonexistent/unloaded entities.
- Thoth's Gambit reworked; entity iotas no longer become Null when the entity unloads; Impulse cost is linear.
- Internal: TreeList-backed list iotas and stacks, typed userdata components (Ravenmind), deprecated APIs removed.

### Port-specific changes (this repository)

Build and packaging

- Converted the Architectury/Loom multi-loader build (Common + Fabric + NeoForge) into a single **NeoForge**
  project built with **ModDevGradle 2.0.148**. Fabric and the hexdoc web-book tooling were removed.
- Mod metadata (`neoforge.mods.toml`) is generated from `src/main/templates` with versions from `gradle.properties`;
  the description and credits carry the "Reborn by Magetka" attribution.
- json5 resources (language files, `sounds.json5`) are converted at build time by a small `buildSrc` filter instead
  of the PKPCPBP Gradle plugin. The produced `en_us.json` etc. are identical to upstream's output.
- Release jar name: `hexcasting-reborn-by-magetka-1.21.1.jar`; `LICENSE.txt` and `NOTICE.md` are packaged in `META-INF`.
- GitHub Actions workflow builds the mod, runs unit tests and the GameTest server, and uploads the jar.

Fixes

- **Access transformer**: the old Forge AT used SRG names (`f_110576_`, `m_269079_`...), which NeoForge 1.21.1
  (Mojang names at runtime) does not apply. Rewritten with Mojang names and merged with the former access widener.
- **Dedicated server crash**: `Iota.displayWithMaxWidth(int, Font)` exposed the client-only `Font` class in a common
  class, so reflection over iotas on a dedicated server (e.g. NeoForge's data component check when a hex is written
  to a focus in a development server) failed with
  `Attempted to load class net/minecraft/client/gui/Font for invalid dist DEDICATED_SERVER`.
  The method is now `@OnlyIn(Dist.CLIENT)`.
- **Public PAUCAL**: the upstream branch requires an unreleased PAUCAL build (`0.7.1-pre`) only for `PaucalCodecs.VEC3`.
  That codec now lives in `CodecUtils`, so the mod runs with the public PAUCAL 0.7.0 release.
- **Tags**: legacy `forge:` tags migrated to the NeoForge 1.21 `c:` convention
  (`c:dusts/amethyst`, `c:gems`, `c:rods/wooden`, `#c:bosses`; duplicate entry removed). Data regenerated with `runData`.
- **Shader**: removed the `IViewRotMat` uniform (gone from the vanilla 1.21 entity shader) from `hexcasting__grayscale`.
- `pack.mcmeta` updated to pack format 34; mixin configs no longer reference a refmap (not needed with Mojang names).

Tests

- New dev-only `gametest` source set (`./gradlew runGameTestServer`): content registration, pattern execution,
  media consumption from charged amethyst, mishap reporting, focus data components across save/network,
  `ExecutionClientView` stream codec.
- The upstream unit tests (`./gradlew test`) run unchanged.
