package at.petrak.hexcasting.gametest;

import at.petrak.hexcasting.api.HexAPI;
import at.petrak.hexcasting.api.casting.ActionRegistryEntry;
import at.petrak.hexcasting.api.casting.eval.ExecutionClientView;
import at.petrak.hexcasting.api.casting.eval.ResolvedPatternType;
import at.petrak.hexcasting.api.casting.eval.env.StaffCastEnv;
import at.petrak.hexcasting.api.casting.eval.vm.CastingVM;
import at.petrak.hexcasting.api.casting.iota.DoubleIota;
import at.petrak.hexcasting.api.casting.iota.EntityIota;
import at.petrak.hexcasting.api.casting.iota.Iota;
import at.petrak.hexcasting.api.casting.iota.ListIota;
import at.petrak.hexcasting.api.casting.iota.PatternIota;
import at.petrak.hexcasting.api.casting.iota.Vec3Iota;
import at.petrak.hexcasting.common.lib.HexBlocks;
import at.petrak.hexcasting.common.lib.HexItems;
import at.petrak.hexcasting.common.lib.hex.HexActions;
import at.petrak.hexcasting.xplat.IXplatAbstractions;
import com.mojang.authlib.GameProfile;
import io.netty.buffer.Unpooled;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.gametest.framework.GameTest;
import net.minecraft.gametest.framework.GameTestHelper;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.GameType;
import net.minecraft.world.phys.Vec3;
import net.neoforged.neoforge.common.util.FakePlayerFactory;
import net.neoforged.neoforge.gametest.GameTestHolder;
import net.neoforged.neoforge.gametest.PrefixGameTestTemplate;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;

/**
 * Server-side smoke tests for the port: registration, pattern execution, media (amethyst) consumption,
 * mishaps, item data components and network codecs. Dev-only; not shipped in the release jar.
 * Run with {@code ./gradlew runGameTestServer}.
 */
@GameTestHolder(HexAPI.MOD_ID)
@PrefixGameTestTemplate(false)
public class HexCastingGameTests {
    private static final String PLATFORM = "platform";

    private static ServerPlayer caster(GameTestHelper helper, String name) {
        var uuid = UUID.nameUUIDFromBytes(("hexcasting-gametest-" + name).getBytes(StandardCharsets.UTF_8));
        var player = FakePlayerFactory.get(helper.getLevel(), new GameProfile(uuid, "hex_gt_" + name));
        player.setGameMode(GameType.SURVIVAL);
        player.getInventory().clearContent();
        var pos = helper.absoluteVec(new Vec3(2.5, 1.0, 2.5));
        player.moveTo(pos.x, pos.y, pos.z, 0f, 0f);
        return player;
    }

    private static CastingVM vm(ServerPlayer player) {
        return CastingVM.empty(new StaffCastEnv(player, InteractionHand.MAIN_HAND));
    }

    private static PatternIota pattern(Holder<ActionRegistryEntry> action) {
        return new PatternIota(action.value().prototype());
    }

    private static Iota top(CastingVM vm) {
        var stack = vm.getImage().getStack();
        return stack.length() == 0 ? null : stack.last();
    }

    @GameTest(template = PLATFORM)
    public static void contentIsRegistered(GameTestHelper helper) {
        var items = List.of(HexItems.STAFF_OAK.get(), HexItems.CHARGED_AMETHYST.get(), HexItems.AMETHYST_DUST.get(),
            HexItems.FOCUS.get(), HexItems.SPELLBOOK.get(), HexItems.TRINKET.get(), HexItems.ARTIFACT.get(),
            HexItems.CYPHER.get(), HexItems.SCROLL_LARGE.get(), HexItems.SCRYING_LENS.get());
        for (var item : items) {
            var key = BuiltInRegistries.ITEM.getKey(item);
            helper.assertTrue(HexAPI.MOD_ID.equals(key.getNamespace()), "item not registered under hexcasting: " + key);
        }
        for (var block : List.of(HexBlocks.SLATE.get(), HexBlocks.CONJURED_BLOCK.get(),
            HexBlocks.IMPETUS_RIGHTCLICK.get(), HexBlocks.EMPTY_DIRECTRIX.get(), HexBlocks.AKASHIC_RECORD.get())) {
            var key = BuiltInRegistries.BLOCK.getKey(block);
            helper.assertTrue(HexAPI.MOD_ID.equals(key.getNamespace()), "block not registered under hexcasting: " + key);
        }
        var actions = IXplatAbstractions.INSTANCE.getActionRegistry().size();
        helper.assertTrue(actions >= 150, "expected the full action registry, found " + actions);
        helper.succeed();
    }

    @GameTest(template = PLATFORM)
    public static void castMindsReflectionAndCompass(GameTestHelper helper) {
        var player = caster(helper, "reflection");
        var vm = vm(player);
        var view = vm.queueExecuteAndWrapIotas(List.of(pattern(HexActions.GET_CASTER)), helper.getLevel());
        helper.assertTrue(view.getResolutionType() == ResolvedPatternType.EVALUATED,
            "Mind's Reflection: unexpected resolution " + view.getResolutionType());
        helper.assertTrue(top(vm) instanceof EntityIota e && player.getUUID().equals(e.getEntityId()),
            "Mind's Reflection did not push the caster: " + top(vm));

        // Entity iotas are resolved through the level, so use an entity that is actually in the world.
        var stand = helper.spawn(EntityType.ARMOR_STAND, new Vec3(2.5, 1.0, 3.5));
        vm = vm(player);
        view = vm.queueExecuteAndWrapIotas(
            List.of(pattern(HexActions.ESCAPE), new EntityIota(stand), pattern(HexActions.ENTITY_POS$FOOT)),
            helper.getLevel());
        helper.assertTrue(view.getResolutionType() == ResolvedPatternType.EVALUATED,
            "Archer's Purification: unexpected resolution " + view.getResolutionType() + ", stack " + view.getStackDescs());
        helper.assertTrue(vm.getImage().getStack().length() == 1, "expected exactly one iota on the stack");
        helper.assertTrue(top(vm) instanceof Vec3Iota v && v.getVec3().distanceTo(stand.position()) < 1e-6,
            "Archer's Purification did not return the entity's position: " + top(vm));
        helper.succeed();
    }

    @GameTest(template = PLATFORM)
    public static void conjureBlockConsumesAmethyst(GameTestHelper helper) {
        var player = caster(helper, "conjure");
        player.getInventory().add(new ItemStack(HexItems.CHARGED_AMETHYST.get(), 16));
        var rel = new BlockPos(2, 2, 4);
        var target = Vec3.atCenterOf(helper.absolutePos(rel));

        var vm = vm(player);
        var view = vm.queueExecuteAndWrapIotas(
            List.of(pattern(HexActions.ESCAPE), new Vec3Iota(target), pattern(HexActions.CONJURE_BLOCK)),
            helper.getLevel());

        helper.assertTrue(view.getResolutionType() == ResolvedPatternType.EVALUATED,
            "unexpected resolution " + view.getResolutionType());
        helper.assertBlockPresent(HexBlocks.CONJURED_BLOCK.get(), rel);
        var left = player.getInventory().countItem(HexItems.CHARGED_AMETHYST.get());
        helper.assertTrue(left < 16, "casting did not consume any media from the inventory");
        helper.succeed();
    }

    @GameTest(template = PLATFORM)
    public static void mishapIsReportedNotThrown(GameTestHelper helper) {
        var player = caster(helper, "mishap");
        var vm = vm(player);
        // Additive Distillation with an empty stack -> "not enough arguments" mishap
        var view = vm.queueExecuteAndWrapIotas(List.of(pattern(HexActions.ADD)), helper.getLevel());
        helper.assertTrue(view.getResolutionType() == ResolvedPatternType.ERRORED,
            "expected a mishap, got " + view.getResolutionType());
        helper.succeed();
    }

    @GameTest(template = PLATFORM)
    public static void focusDataSurvivesSaveAndNetwork(GameTestHelper helper) {
        var player = caster(helper, "focus");
        var registries = helper.getLevel().registryAccess();
        var hex = new ListIota(List.of(pattern(HexActions.GET_CASTER), new DoubleIota(42.0),
            new Vec3Iota(new Vec3(1, 2, 3)), new EntityIota(player)));

        var stack = new ItemStack(HexItems.FOCUS.get());
        var holder = IXplatAbstractions.INSTANCE.findDataHolder(stack);
        helper.assertTrue(holder != null && holder.writeIota(hex, false), "focus refused to store a hex");

        // Disk: data components -> NBT -> data components
        var saved = ItemStack.parse(registries, stack.save(registries)).orElseThrow();
        var fromDisk = IXplatAbstractions.INSTANCE.findDataHolder(saved).readIota();
        helper.assertTrue(fromDisk != null && Iota.tolerates(fromDisk, hex), "focus contents changed on save: " + fromDisk);

        // Network: ItemStack stream codec
        var buf = new RegistryFriendlyByteBuf(Unpooled.buffer(), registries);
        ItemStack.STREAM_CODEC.encode(buf, stack);
        var synced = ItemStack.STREAM_CODEC.decode(buf);
        var fromNet = IXplatAbstractions.INSTANCE.findDataHolder(synced).readIota();
        helper.assertTrue(fromNet != null && Iota.tolerates(fromNet, hex), "focus contents changed over network: " + fromNet);
        helper.succeed();
    }

    @GameTest(template = PLATFORM)
    public static void castResultSurvivesNetworkCodec(GameTestHelper helper) {
        var player = caster(helper, "netview");
        var vm = vm(player);
        var view = vm.queueExecuteAndWrapIotas(
            List.of(pattern(HexActions.GET_CASTER), pattern(HexActions.ENTITY_POS$FOOT)), helper.getLevel());

        var buf = new RegistryFriendlyByteBuf(Unpooled.buffer(), helper.getLevel().registryAccess());
        ExecutionClientView.getSTREAM_CODEC().encode(buf, view);
        var decoded = ExecutionClientView.getSTREAM_CODEC().decode(buf);
        helper.assertTrue(decoded.getResolutionType() == view.getResolutionType(), "resolution type changed");
        helper.assertTrue(decoded.getStackDescs().size() == view.getStackDescs().size(), "stack size changed");
        helper.assertTrue(buf.readableBytes() == 0, "codec left unread bytes");
        helper.succeed();
    }
}
