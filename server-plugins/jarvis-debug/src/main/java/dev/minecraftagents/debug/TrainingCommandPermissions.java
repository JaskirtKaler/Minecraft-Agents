package dev.minecraftagents.debug;

import java.util.Collection;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.permissions.PermissionAttachment;
import org.bukkit.plugin.Plugin;

/** Grants only time/weather commands to configured human players in training mode. */
public final class TrainingCommandPermissions implements Listener {
    private final Plugin plugin;
    private final String botUsername;
    private final Set<String> allowedPlayers;
    private final Map<UUID, PermissionAttachment> attachments = new HashMap<>();

    public TrainingCommandPermissions(Plugin plugin, String botUsername, Collection<String> allowedPlayers) {
        this.plugin = plugin;
        this.botUsername = botUsername;
        this.allowedPlayers = allowedPlayers.stream().map(name -> name.toLowerCase(Locale.ROOT))
            .collect(Collectors.toUnmodifiableSet());
    }

    public void grant(Player player) {
        if (player.getName().equalsIgnoreCase(botUsername)
            || !allowedPlayers.contains(player.getName().toLowerCase(Locale.ROOT))
            || attachments.containsKey(player.getUniqueId())) return;
        PermissionAttachment attachment = player.addAttachment(plugin);
        attachment.setPermission("minecraft.command.time", true);
        attachment.setPermission("minecraft.command.weather", true);
        attachments.put(player.getUniqueId(), attachment);
        player.updateCommands();
    }

    @EventHandler
    public void onJoin(PlayerJoinEvent event) { grant(event.getPlayer()); }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        PermissionAttachment attachment = attachments.remove(event.getPlayer().getUniqueId());
        if (attachment != null) attachment.remove();
    }

    public void close() {
        for (PermissionAttachment attachment : attachments.values()) {
            attachment.remove();
            if (attachment.getPermissible() instanceof Player player && player.isOnline()) {
                player.updateCommands();
            }
        }
        attachments.clear();
    }
}
