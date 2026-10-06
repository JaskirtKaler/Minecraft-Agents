package dev.minecraftagents.debug;

import java.util.UUID;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.inventory.InventoryClickEvent;
import org.bukkit.event.inventory.InventoryCloseEvent;
import org.bukkit.event.inventory.InventoryDragEvent;
import org.bukkit.event.player.PlayerDropItemEvent;
import org.bukkit.event.player.PlayerSwapHandItemsEvent;
import org.bukkit.inventory.Inventory;
import org.bukkit.inventory.InventoryHolder;
import org.bukkit.inventory.InventoryView;

/** Cancels the whole view, including the viewer's lower inventory and outside. */
final class ReadOnlyInventoryListener implements Listener {
    static final class SnapshotHolder implements InventoryHolder {
        final UUID targetId;
        private Inventory inventory;

        SnapshotHolder(UUID targetId) { this.targetId = targetId; }

        void attach(Inventory inventory) { this.inventory = inventory; }

        @Override
        public Inventory getInventory() { return inventory; }
    }

    static SnapshotHolder holder(InventoryView view) {
        return view.getTopInventory().getHolder() instanceof SnapshotHolder snapshot ? snapshot : null;
    }

    @EventHandler(priority = EventPriority.HIGHEST)
    public void onClick(InventoryClickEvent event) {
        // Includes shift, double click, number keys, offhand keys, creative clicks,
        // item drops and clicks outside the window. Never change the cursor.
        if (holder(event.getView()) != null) event.setCancelled(true);
    }

    @EventHandler(priority = EventPriority.HIGHEST)
    public void onDrag(InventoryDragEvent event) {
        if (holder(event.getView()) != null) event.setCancelled(true);
    }

    @EventHandler(priority = EventPriority.HIGHEST)
    public void onDrop(PlayerDropItemEvent event) {
        if (holder(event.getPlayer().getOpenInventory()) != null) event.setCancelled(true);
    }

    @EventHandler(priority = EventPriority.HIGHEST)
    public void onSwap(PlayerSwapHandItemsEvent event) {
        if (holder(event.getPlayer().getOpenInventory()) != null) event.setCancelled(true);
    }

    @EventHandler
    public void onClose(InventoryCloseEvent event) {
        if (holder(event.getView()) != null) event.getInventory().clear();
    }
}
