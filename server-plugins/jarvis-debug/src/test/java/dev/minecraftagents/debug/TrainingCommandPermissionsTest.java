package dev.minecraftagents.debug;

import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.bukkit.entity.Player;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.permissions.PermissionAttachment;
import org.bukkit.plugin.Plugin;

/** Cached Bukkit API contracts: staging grants, exclusions and revocation, never a real player. */
public final class TrainingCommandPermissionsTest {
    private static final Plugin PLUGIN = (Plugin) Proxy.newProxyInstance(Plugin.class.getClassLoader(),
        new Class<?>[] {Plugin.class}, (instance, method, args) -> {
            if (method.getName().equals("isEnabled")) return true;
            if (method.getName().equals("getName")) return "JarvisDebug-test";
            if (method.getName().equals("toString")) return "JarvisDebug-test";
            if (method.getName().equals("hashCode")) return System.identityHashCode(instance);
            if (method.getName().equals("equals")) return instance == args[0];
            return null;
        });

    private static final class Human {
        final Player player;
        final List<PermissionAttachment> attachments = new ArrayList<>();
        int commandUpdates;
        int removals;
        boolean online = true;

        Human(String name) {
            UUID id = UUID.nameUUIDFromBytes(name.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            player = (Player) Proxy.newProxyInstance(Player.class.getClassLoader(), new Class<?>[] {Player.class},
                (instance, method, args) -> {
                    switch (method.getName()) {
                        case "getName": return name;
                        case "getUniqueId": return id;
                        case "isOnline": return online;
                        case "isOp": return false;
                        case "setOp": throw new AssertionError("Staging must not grant operator status.");
                        case "addAttachment":
                            var attachment = new PermissionAttachment((Plugin) args[0], (Player) instance);
                            attachments.add(attachment);
                            return attachment;
                        case "removeAttachment": removals++; return null;
                        case "recalculatePermissions": return null;
                        case "updateCommands": commandUpdates++; return null;
                        case "hashCode": return System.identityHashCode(instance);
                        case "equals": return instance == args[0];
                        case "toString": return name;
                        default: return null;
                    }
                });
        }
    }

    public static void main(String[] args) {
        var permissions = new TrainingCommandPermissions(PLUGIN, "AI_Agent", List.of("Pilot6117", "AI_Agent"));
        var human = new Human("pIlOt6117");
        permissions.onJoin(new PlayerJoinEvent(human.player, (String) null));
        assert human.attachments.size() == 1;
        assert human.attachments.get(0).getPermissions().equals(Map.of(
            "minecraft.command.time", true, "minecraft.command.weather", true, "minecraft.command.gamemode", true));
        assert human.commandUpdates == 1;
        assert !human.player.isOp();
        permissions.grant(human.player);
        assert human.attachments.size() == 1 : "Duplicate joins/grants must not stack attachments.";
        var stranger = new Human("OtherPlayer");
        var bot = new Human("ai_agent");
        permissions.grant(stranger.player);
        permissions.grant(bot.player);
        assert stranger.attachments.isEmpty() && bot.attachments.isEmpty();

        permissions.onQuit(new PlayerQuitEvent(human.player, (String) null));
        assert human.removals == 1;
        permissions.grant(human.player);
        assert human.attachments.size() == 2;
        permissions.close();
        assert human.removals == 2;
        assert human.commandUpdates == 3 : "Reload/disable must refresh the online player's command list.";
        permissions.close();
        assert human.removals == 2 : "Close is idempotent.";

        var excluded = new TrainingCommandPermissions(PLUGIN, "AI_Agent", Set.of());
        excluded.grant(human.player);
        assert human.attachments.size() == 2;
        System.out.println("Human-only gamemode/time/weather grants, no op/bot grants, duplicate joins and revocation tests passed.");
    }
}
