package dev.minecraftagents.debug;

import java.lang.reflect.Proxy;
import java.util.HashSet;
import java.util.Map;
import java.util.UUID;
import org.bukkit.Material;
import org.bukkit.entity.HumanEntity;
import org.bukkit.entity.Item;
import org.bukkit.entity.Player;
import org.bukkit.event.inventory.ClickType;
import org.bukkit.event.inventory.InventoryAction;
import org.bukkit.event.inventory.InventoryClickEvent;
import org.bukkit.event.inventory.InventoryCloseEvent;
import org.bukkit.event.inventory.InventoryCreativeEvent;
import org.bukkit.event.inventory.InventoryDragEvent;
import org.bukkit.event.inventory.InventoryType;
import org.bukkit.event.player.PlayerDropItemEvent;
import org.bukkit.event.player.PlayerSwapHandItemsEvent;
import org.bukkit.inventory.Inventory;
import org.bukkit.inventory.InventoryHolder;
import org.bukkit.inventory.InventoryView;
import org.bukkit.inventory.ItemStack;

/** Runs real Bukkit event classes with in-memory views, not a Minecraft world. */
public final class InventorySafetyTest {
    private static int clears;

    @SuppressWarnings("unchecked")
    private static <T> T proxy(Class<T> type, Map<String, Object> values) {
        return (T) Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[] {type}, (instance, method, args) -> {
            if (method.getName().equals("clear")) { clears++; return null; }
            if (method.getName().equals("toString")) return "test-" + type.getSimpleName();
            if (method.getName().equals("hashCode")) return System.identityHashCode(instance);
            if (method.getName().equals("equals")) return instance == args[0];
            return values.get(method.getName());
        });
    }

    private static Inventory inventory(InventoryHolder holder, int size) {
        return proxy(Inventory.class, holder == null
            ? Map.of("getSize", size, "getType", InventoryType.CHEST)
            : Map.of("getSize", size, "getType", InventoryType.CHEST, "getHolder", holder));
    }

    private static final class View extends InventoryView {
        final Inventory top;
        final Inventory bottom = inventory(null, 36);
        final Player viewer;

        View(Inventory top) {
            this.top = top;
            viewer = proxy(Player.class, Map.of("getOpenInventory", this, "getUniqueId", UUID.randomUUID()));
        }
        @Override public Inventory getTopInventory() { return top; }
        @Override public Inventory getBottomInventory() { return bottom; }
        @Override public HumanEntity getPlayer() { return viewer; }
        @Override public InventoryType getType() { return InventoryType.CHEST; }
        @Override public String getTitle() { return "test"; }
        @Override public String getOriginalTitle() { return "test"; }
        @Override public void setTitle(String title) {}
    }

    public static void main(String[] args) throws Exception {
        var sources = new HashSet<Integer>();
        for (int display = 0; display <= 40; display++) {
            int source = InventoryLayout.sourceSlot(display);
            assert source >= 0 && source <= 40;
            assert sources.add(source) : "Repeated bot slot: " + source;
        }
        assert sources.size() == 41 : "Missing storage/equipment slot";
        assert InventoryLayout.sourceSlot(0) == 9;
        assert InventoryLayout.sourceSlot(26) == 35;
        assert InventoryLayout.sourceSlot(27) == 0;
        assert InventoryLayout.sourceSlot(35) == 8;
        assert InventoryLayout.sourceSlot(36) == 39;
        assert InventoryLayout.sourceSlot(39) == 36;
        assert InventoryLayout.sourceSlot(40) == 40;
        assert InventoryLayout.sourceSlot(41) == -1;
        assert InventoryLayout.sourceSlot(-1) == -1;

        ItemStack source = new ItemStack(Material.COBBLESTONE, 12);
        var copyMethod = JarvisDebugPlugin.class.getDeclaredMethod("copy", ItemStack.class);
        copyMethod.setAccessible(true);
        ItemStack copy = (ItemStack) copyMethod.invoke(null, source);
        assert copy != source;
        assert copy.getAmount() == 12;
        copy.setAmount(1);
        assert source.getAmount() == 12 : "Display must never mutate bot items";
        assert copyMethod.invoke(null, (Object) null) == null;
        assert copyMethod.invoke(null, new ItemStack(Material.AIR)) == null;

        var holder = new ReadOnlyInventoryListener.SnapshotHolder(UUID.randomUUID());
        var view = new View(inventory(holder, InventoryLayout.SIZE));
        holder.attach(view.top);
        var listener = new ReadOnlyInventoryListener();
        int blockedClicks = 0;
        for (ClickType click : ClickType.values()) {
            for (int rawSlot : new int[] {0, 27, 36, 40, 45, 54, 80, -999}) {
                var event = new InventoryClickEvent(view, InventoryType.SlotType.CONTAINER,
                    rawSlot, click, InventoryAction.UNKNOWN, 0);
                listener.onClick(event);
                assert event.isCancelled() : "Unblocked click " + click + " slot " + rawSlot;
                blockedClicks++;
            }
        }
        var creative = new InventoryCreativeEvent(view, InventoryType.SlotType.CONTAINER, 0, source);
        listener.onClick(creative);
        assert creative.isCancelled();
        for (int rawSlot : new int[] {0, 27, 54, 80}) {
            var drag = new InventoryDragEvent(view, source, source, false, Map.of(rawSlot, source));
            listener.onDrag(drag);
            assert drag.isCancelled() : "Unblocked drag slot " + rawSlot;
        }
        var drop = new PlayerDropItemEvent(view.viewer, proxy(Item.class, Map.of()));
        listener.onDrop(drop);
        assert drop.isCancelled();
        var swap = new PlayerSwapHandItemsEvent(view.viewer, source, copy);
        listener.onSwap(swap);
        assert swap.isCancelled();

        var ordinary = new View(inventory(null, 27));
        var normalClick = new InventoryClickEvent(ordinary, InventoryType.SlotType.CONTAINER, 0,
            ClickType.LEFT, InventoryAction.PICKUP_ALL);
        listener.onClick(normalClick);
        assert !normalClick.isCancelled() : "Normal inventories should still work";
        var normalDrag = new InventoryDragEvent(ordinary, source, source, false, Map.of(0, source));
        listener.onDrag(normalDrag);
        assert !normalDrag.isCancelled();
        listener.onClose(new InventoryCloseEvent(ordinary));
        assert clears == 0;
        listener.onClose(new InventoryCloseEvent(view));
        assert clears == 1 : "Discard detached display on close";
        System.out.println("Inventory layout, detached copies, " + blockedClicks + " cancelled clicks, creative/drag/drop/swap/close tests passed.");
    }
}
