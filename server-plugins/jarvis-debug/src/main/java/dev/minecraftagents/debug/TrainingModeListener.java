package dev.minecraftagents.debug;

import org.bukkit.Difficulty;
import org.bukkit.World;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.EntityExhaustionEvent;
import org.bukkit.event.entity.FoodLevelChangeEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.world.WorldLoadEvent;

/** Reversible local training assistance; never changes game mode or inventory. */
public final class TrainingModeListener implements Listener {
    private final boolean enabled;

    public TrainingModeListener(boolean enabled) { this.enabled = enabled; }

    public void initializeWorld(World world) {
        if (enabled) world.setDifficulty(Difficulty.PEACEFUL);
    }

    public void maintainPlayer(Player player) {
        if (!enabled) return;
        player.setFoodLevel(20);
        player.setSaturation(20.0f);
        player.setExhaustion(0.0f);
    }

    @EventHandler
    public void onWorldLoad(WorldLoadEvent event) { initializeWorld(event.getWorld()); }

    @EventHandler
    public void onJoin(PlayerJoinEvent event) { maintainPlayer(event.getPlayer()); }

    @EventHandler(priority = EventPriority.HIGHEST)
    public void onFood(FoodLevelChangeEvent event) {
        if (enabled && event.getEntity() instanceof Player player) {
            event.setCancelled(true);
            maintainPlayer(player);
        }
    }

    @EventHandler(priority = EventPriority.HIGHEST)
    public void onExhaustion(EntityExhaustionEvent event) {
        if (enabled && event.getEntity() instanceof Player) event.setCancelled(true);
    }
}
