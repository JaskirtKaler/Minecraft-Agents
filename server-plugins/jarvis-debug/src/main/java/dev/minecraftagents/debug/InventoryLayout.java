package dev.minecraftagents.debug;

/** The display is detached from the bot's inventory; these are Bukkit slot IDs. */
final class InventoryLayout {
    static final int SIZE = 54;

    private InventoryLayout() {}

    static int sourceSlot(int displaySlot) {
        if (displaySlot >= 0 && displaySlot < 27) return displaySlot + 9;
        if (displaySlot >= 27 && displaySlot < 36) return displaySlot - 27;
        return switch (displaySlot) {
            case 36 -> 39; // Helmet
            case 37 -> 38; // Chestplate
            case 38 -> 37; // Leggings
            case 39 -> 36; // Boots
            case 40 -> 40; // Offhand
            default -> -1; // Instructions, not player items
        };
    }
}
