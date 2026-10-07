package dev.minecraftagents.practice;

import com.google.gson.Gson;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.Base64;
import java.nio.charset.StandardCharsets;
import com.google.gson.reflect.TypeToken;
import org.bukkit.GameMode;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Chest;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.command.ConsoleCommandSender;
import org.bukkit.entity.Item;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.block.BlockBreakEvent;
import org.bukkit.inventory.ItemStack;
import org.bukkit.plugin.java.JavaPlugin;

/** Console-only fixture/oracle. This jar is NEVER installed in the real server. */
public final class PracticeOracle extends JavaPlugin implements Listener {
    private final Gson json = new Gson();
    private final List<Map<String, Object>> broken = new ArrayList<>();
    private int unsafeBreaks;
    private String targetName = "PracticeAgent";
    private World arena;
    private final Random random = new Random(Long.getLong("minecraftagents.practice.seed", 0L));

    @Override public void onEnable() {
        try {
            String nonce = System.getProperty("minecraftagents.practice", "");
            if (nonce.isBlank() || !Files.readString(Path.of("practice.guard")).trim().equals(nonce)) {
                throw new IllegalStateException("Missing matching disposable-world guard");
            }
        } catch (Exception error) {
            getLogger().severe("Refusing practice-world mutations: " + error.getMessage());
            getServer().getPluginManager().disablePlugin(this);
            return;
        }
        arena = getServer().getWorlds().get(0);
        resetTerrain();
        arena.setSpawnLocation(0, 64, 0);
        getServer().getPluginManager().registerEvents(this, this);
        getLogger().info("Practice oracle ready (guarded disposable world, console only)");
    }

    private void set(int x, int y, int z, Material material) {
        arena.getBlockAt(x, y, z).setType(material, false);
    }

    private void resetTerrain() {
        for (int x = -6; x <= 24; x++) for (int z = -8; z <= 12; z++) {
            set(x, 63, z, Material.STONE);
            for (int y = 64; y <= 70; y++) set(x, y, z, Material.AIR);
        }
        for (var entity : arena.getEntities()) if (entity instanceof Item) entity.remove();
        set(3, 64, 3, Material.CHEST);
        ((Chest) arena.getBlockAt(3, 64, 3).getState()).getInventory().clear();
        for (int x = 8; x <= 17; x++) {
            set(x, 64, 0, Material.STONE);
            set(x, 64, 4, Material.OAK_LOG);
            set(x, 64, -4, Material.DIRT);
        }
        broken.clear();
        unsafeBreaks = 0;
    }

    private void setup(String scenario, Player bot) {
        var valid = List.of("held_deposit", "partial_stack", "multiple_stacks", "collect_cobble", "collect_logs",
            "collect_dirt", "batch", "missing_tool", "full_chest", "blocked_stone", "unsupported_batch", "staircase", "explore");
        if (!valid.contains(scenario)) throw new IllegalArgumentException("Unknown fixture: " + scenario);
        bot.closeInventory();
        resetTerrain();
        bot.getInventory().clear();
        bot.setGameMode(GameMode.SURVIVAL);
        bot.setHealth(20);
        bot.setFoodLevel(20);
        bot.setSaturation(20);
        bot.teleport(new Location(arena, 0.5, 64, 0.5));
        if (!scenario.equals("missing_tool")) bot.getInventory().setItem(0, new ItemStack(Material.WOODEN_PICKAXE));
        bot.getInventory().setItem(1, new ItemStack(Material.WOODEN_AXE));
        bot.getInventory().setItem(2, new ItemStack(Material.WOODEN_SHOVEL));
        switch (scenario) {
            case "held_deposit", "full_chest" -> bot.getInventory().setItem(3, new ItemStack(Material.COBBLESTONE, 12));
            case "partial_stack" -> bot.getInventory().setItem(3, new ItemStack(Material.COBBLESTONE, 7));
            case "multiple_stacks" -> {
                bot.getInventory().setItem(3, new ItemStack(Material.COBBLESTONE, 64));
                bot.getInventory().setItem(4, new ItemStack(Material.COBBLESTONE, 6));
            }
            case "blocked_stone" -> {
                for (int x = 8; x <= 17; x++) {
                    set(x, 64, 0, Material.STONE);
                    set(x, 65, 0, Material.BEDROCK);
                    set(x, 64, -1, Material.BEDROCK);
                    set(x, 64, 1, Material.BEDROCK);
                }
                set(7, 64, 0, Material.BEDROCK);
                set(18, 64, 0, Material.BEDROCK);
            }
            case "staircase" -> {
                for (int x = -3; x <= 8; x++) for (int z = -3; z <= 3; z++)
                    for (int y = 64; y <= 68; y++) set(x, y, z, Material.STONE);
                set(0, 64, 0, Material.AIR);
                set(0, 65, 0, Material.AIR);
            }
            default -> {}
        }
        if (scenario.equals("full_chest")) {
            var chest = ((Chest) arena.getBlockAt(3, 64, 3).getState()).getInventory();
            for (int slot = 0; slot < chest.getSize(); slot++) chest.setItem(slot, new ItemStack(Material.DIRT, 64));
        }
        bot.getInventory().setHeldItemSlot(0);
        if (scenario.equals("explore")) {
            // Vary starting materials, table availability and resource locations.
            // This is an environment generator, NOT a prescribed task curriculum.
            bot.getInventory().setItem(3, new ItemStack(Material.OAK_LOG, 2 + random.nextInt(5)));
            bot.getInventory().setItem(4, new ItemStack(Material.WHEAT_SEEDS, 3 + random.nextInt(5)));
            bot.getInventory().setItem(5, new ItemStack(Material.WOODEN_HOE));
            if (random.nextBoolean()) set(4, 64, -2, Material.CRAFTING_TABLE);
            for (int x = 8; x <= 17; x++) {
                set(x, 64, 4, Material.AIR);
                set(x, 64, -4, Material.AIR);
                int z = 4 + random.nextInt(3);
                set(x, 64, z, Material.OAK_LOG);
                set(x, 64, -4 - random.nextInt(3), Material.DIRT);
            }
            for (int x = 10; x <= 14; x++) {
                set(x, 63, 8, Material.FARMLAND);
                var farmland = (org.bukkit.block.data.type.Farmland) arena.getBlockAt(x, 63, 8).getBlockData();
                farmland.setMoisture(7);
                arena.getBlockAt(x, 63, 8).setBlockData(farmland, false);
                set(x, 64, 8, Material.WHEAT);
                var crop = (org.bukkit.block.data.Ageable) arena.getBlockAt(x, 64, 8).getBlockData();
                crop.setAge(random.nextBoolean() ? crop.getMaximumAge() : random.nextInt(4));
                arena.getBlockAt(x, 64, 8).setBlockData(crop, false);
            }
            set(12, 63, 9, Material.WATER);
        }
        bot.updateInventory();
    }

    private Map<String, Integer> totals(ItemStack[] items) {
        Map<String, Integer> totals = new LinkedHashMap<>();
        for (var item : items) if (item != null && !item.getType().isAir()) {
            totals.merge(item.getType().name().toLowerCase(Locale.ROOT), item.getAmount(), Integer::sum);
        }
        return totals;
    }

    private Map<String, Object> snapshot(Player bot) {
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("inventory", totals(bot.getInventory().getContents()));
        var block = arena.getBlockAt(3, 64, 3).getState();
        state.put("chest", block instanceof Chest chest ? totals(chest.getInventory().getContents()) : Map.of());
        var p = bot.getLocation();
        state.put("position", Map.of("x", p.getX(), "y", p.getY(), "z", p.getZ()));
        state.put("health", bot.getHealth());
        state.put("broken", new ArrayList<>(broken));
        state.put("unsafe_breaks", unsafeBreaks);
        return state;
    }

    @Override public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (!(sender instanceof ConsoleCommandSender) || args.length < 3 || !args[1].matches("[a-f0-9-]{36}")) return true;
        String token = args[1];
        Map<String, Object> reply = new LinkedHashMap<>();
        try {
            targetName = args[2];
            Player bot = getServer().getPlayerExact(targetName);
            if (bot == null) throw new IllegalStateException("Practice bot is offline");
            if (args[0].equals("setup") && args.length == 4) setup(args[3], bot);
            else if (!args[0].equals("snapshot") && !args[0].equals("check")) throw new IllegalArgumentException("Unknown command");
            reply.put("ok", true);
            var state = snapshot(bot);
            if (args[0].equals("check") && args.length == 4) {
                String data = new String(Base64.getUrlDecoder().decode(args[3]), StandardCharsets.UTF_8);
                List<Map<String, Object>> goals = json.fromJson(data, new TypeToken<List<Map<String, Object>>>(){}.getType());
                if (goals.size() > 16) throw new IllegalArgumentException("Too many goals");
                Map<String, String> blocks = new LinkedHashMap<>();
                Map<String, Map<String, Integer>> containers = new LinkedHashMap<>();
                for (var goal : goals) if (goal.get("position") instanceof Map<?, ?> p) {
                    int x = ((Number)p.get("x")).intValue(), y = ((Number)p.get("y")).intValue(), z = ((Number)p.get("z")).intValue();
                    if (new Location(arena, x, y, z).distance(bot.getLocation()) > 64) throw new IllegalArgumentException("Goal outside oracle radius");
                    String key = x + "," + y + "," + z;
                    var block = arena.getBlockAt(x, y, z);
                    blocks.put(key, block.getType().name().toLowerCase(Locale.ROOT));
                    if (block.getState() instanceof org.bukkit.block.Container container) containers.put(key, totals(container.getInventory().getContents()));
                }
                state.put("blocks", blocks);
                state.put("containers", containers);
            }
            reply.put("state", state);
        } catch (Exception error) {
            reply.put("ok", false);
            reply.put("error", error.getMessage());
        }
        getLogger().info("ORACLE " + token + " " + json.toJson(reply));
        return true;
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onBreak(BlockBreakEvent event) {
        if (!event.getPlayer().getName().equals(targetName)) return;
        var block = event.getBlock();
        var feet = event.getPlayer().getLocation();
        if (block.getX() == feet.getBlockX() && block.getZ() == feet.getBlockZ() && block.getY() < feet.getBlockY()) unsafeBreaks++;
        broken.add(Map.of("block", block.getType().name().toLowerCase(Locale.ROOT), "x", block.getX(), "y", block.getY(), "z", block.getZ()));
    }
}
