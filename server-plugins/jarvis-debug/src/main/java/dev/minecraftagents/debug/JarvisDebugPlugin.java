package dev.minecraftagents.debug;

import java.util.Locale;
import java.util.Objects;
import net.kyori.adventure.text.Component;
import org.bukkit.Material;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerInteractAtEntityEvent;
import org.bukkit.event.player.PlayerInteractEntityEvent;
import org.bukkit.inventory.EquipmentSlot;
import org.bukkit.inventory.Inventory;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.meta.ItemMeta;
import org.bukkit.plugin.java.JavaPlugin;

/** Local debugging only: inspect the configured bot, never arbitrary players. */
public final class JarvisDebugPlugin extends JavaPlugin implements Listener {
    private String botUsername;

    @Override
    public void onEnable() {
        saveDefaultConfig();
        botUsername = System.getProperty("jarvis.bot.username", getConfig().getString("bot-username", "AI_Agent"));
        getServer().getPluginManager().registerEvents(this, this);
        getServer().getPluginManager().registerEvents(new ReadOnlyInventoryListener(), this);
        Objects.requireNonNull(getCommand("jarvisinventory")).setExecutor(this);
        long interval = Math.max(2L, getConfig().getLong("refresh-ticks", 10L));
        getServer().getScheduler().runTaskTimer(this, this::refreshViews, interval, interval);
        getLogger().info("Read-only inventory inspection enabled for " + botUsername);
        boolean training = Boolean.parseBoolean(System.getProperty("jarvis.training.mode",
            Boolean.toString(getConfig().getBoolean("training-mode", true))));
        if (training) {
            var assistance = new TrainingModeListener(true);
            getServer().getPluginManager().registerEvents(assistance, this);
            getServer().getWorlds().forEach(assistance::initializeWorld);
            getServer().getOnlinePlayers().forEach(assistance::maintainPlayer);
            // Also covers respawns and food state restored by other plugins.
            getServer().getScheduler().runTaskTimer(this,
                () -> getServer().getOnlinePlayers().forEach(assistance::maintainPlayer), 20L, 100L);
            getLogger().info("Training mode: Peaceful worlds, full food; Survival mechanics unchanged.");
        }
    }

    private boolean allowed(Player viewer) {
        return viewer.hasPermission("jarvisdebug.inspect") || getConfig().getStringList("allowed-viewers")
            .stream().anyMatch(name -> name.equalsIgnoreCase(viewer.getName()));
    }

    private Player target() { return getServer().getPlayerExact(botUsername); }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (!(sender instanceof Player viewer)) {
            sender.sendMessage("Open /jarvisinventory in Minecraft. Chat 'inventory' also shows live bot items.");
        } else if (!allowed(viewer)) {
            viewer.sendMessage("Inventory inspection is limited to configured local viewers and server operators.");
        } else if (args.length != 0) {
            viewer.sendMessage("Use /jarvisinventory without arguments; only the configured bot can be inspected.");
        } else {
            open(viewer);
        }
        return true;
    }

    @EventHandler(priority = EventPriority.HIGHEST, ignoreCancelled = true)
    public void onInteract(PlayerInteractEntityEvent event) { inspectInteraction(event); }

    @EventHandler(priority = EventPriority.HIGHEST, ignoreCancelled = true)
    public void onInteractAt(PlayerInteractAtEntityEvent event) { inspectInteraction(event); }

    private void inspectInteraction(PlayerInteractEntityEvent event) {
        if (event.getHand() != EquipmentSlot.HAND || !(event.getRightClicked() instanceof Player clicked)) return;
        Player bot = target();
        if (bot == null || !clicked.getUniqueId().equals(bot.getUniqueId()) || !allowed(event.getPlayer())) return;
        event.setCancelled(true);
        Player viewer = event.getPlayer();
        getServer().getScheduler().runTask(this, () -> {
            if (viewer.isOnline() && allowed(viewer)) open(viewer);
        });
    }

    private void open(Player viewer) {
        Player bot = target();
        if (bot == null) {
            viewer.sendMessage("Jarvis is offline. This is not an empty-inventory report.");
            return;
        }
        // Never carry a real cursor item into a detached, read-only display.
        ItemStack cursor = viewer.getItemOnCursor();
        if (cursor != null && !cursor.getType().isAir()) {
            viewer.sendMessage("Put the item on your cursor away first, then use /jarvisinventory again.");
            return;
        }
        var holder = new ReadOnlyInventoryListener.SnapshotHolder(bot.getUniqueId());
        Inventory display = getServer().createInventory(holder, InventoryLayout.SIZE, Component.text("Jarvis: inventory (read-only)"));
        holder.attach(display);
        refresh(display, bot);
        viewer.openInventory(display);
        viewer.sendMessage("Read-only: storage in rows 1-3, hotbar in row 4, helmet/chest/legs/boots/offhand in row 5. Updates live.");
    }

    private static ItemStack copy(ItemStack original) {
        return original == null || original.getType().isAir() ? null : original.clone();
    }

    private static ItemStack label(String text) {
        ItemStack item = new ItemStack(Material.GRAY_STAINED_GLASS_PANE);
        ItemMeta meta = item.getItemMeta();
        meta.displayName(Component.text(text));
        item.setItemMeta(meta);
        return item;
    }

    private void refresh(Inventory display, Player bot) {
        for (int slot = 0; slot < 41; slot++) {
            display.setItem(slot, copy(bot.getInventory().getItem(InventoryLayout.sourceSlot(slot))));
        }
        ItemStack held = bot.getInventory().getItemInMainHand();
        String name = held.getType().isAir() ? "empty" : held.getType().name().toLowerCase(Locale.ROOT);
        display.setItem(41, label("Held: " + name + " (hotbar " + (bot.getInventory().getHeldItemSlot() + 1) + ")"));
        display.setItem(45, label("READ ONLY - copied items; no transfers"));
        display.setItem(46, label("Row 5: helmet, chestplate, leggings, boots, offhand"));
        display.setItem(47, label("Close this window to use your own inventory"));
    }

    private void refreshViews() {
        Player bot = target();
        for (Player viewer : getServer().getOnlinePlayers()) {
            var view = viewer.getOpenInventory();
            var holder = ReadOnlyInventoryListener.holder(view);
            if (holder == null) continue;
            if (bot == null || !bot.getUniqueId().equals(holder.targetId) || !allowed(viewer)) {
                viewer.closeInventory();
                viewer.sendMessage("Jarvis inventory view closed: bot offline or inspection access changed.");
            } else {
                refresh(view.getTopInventory(), bot);
            }
        }
    }

    @Override
    public void onDisable() {
        for (Player viewer : getServer().getOnlinePlayers()) {
            if (ReadOnlyInventoryListener.holder(viewer.getOpenInventory()) != null) viewer.closeInventory();
        }
    }
}
