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
import org.bukkit.event.block.BlockPlaceEvent;
import org.bukkit.inventory.ItemStack;
import org.bukkit.plugin.java.JavaPlugin;

/** Console-only fixture/oracle. This jar is NEVER installed in the real server. */
public final class PracticeOracle extends JavaPlugin implements Listener {
    private final Gson json = new Gson();
    private final List<Map<String, Object>> broken = new ArrayList<>();
    private final List<Map<String, Object>> placed = new ArrayList<>();
    private final List<Map<String, Integer>> farmCells = new ArrayList<>();
    private Map<String, Integer> chestPosition = Map.of("x", 3, "y", 64, "z", 3);
    private int unsafeBreaks;
    private String targetName = "PracticeAgent";
    private World arena;
    private Map<String, Integer> navigationGoal = Map.of();
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
        navigationGoal = Map.of();
        chestPosition = Map.of("x", 3, "y", 64, "z", 3);
        farmCells.clear();
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
        placed.clear();
        unsafeBreaks = 0;
    }

    private void setup(String scenario, Player bot) {
        var valid = List.of("held_deposit", "partial_stack", "multiple_stacks", "collect_cobble", "collect_logs",
            "collect_dirt", "batch", "missing_tool", "full_chest", "blocked_stone", "unsupported_batch", "staircase", "overhead_logs", "explore");
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
            case "overhead_logs" -> {
                // No ground-level logs can hide a broken overhead approach.
                // The bot can mine this small trunk from stable ground without
                // breaking its floor, climbing, placing scaffolds or flying.
                for (int x = 8; x <= 17; x++) set(x, 64, 4, Material.AIR);
                for (int y = 66; y <= 68; y++) set(0, y, 0, Material.OAK_LOG);
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
                farmCells.add(Map.of("x", x, "y", 63, "z", 8));
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
        var block = arena.getBlockAt(chestPosition.get("x"), chestPosition.get("y"), chestPosition.get("z")).getState();
        state.put("chest", block instanceof Chest chest ? totals(chest.getInventory().getContents()) : Map.of());
        state.put("chest_position", chestPosition);
        var p = bot.getLocation();
        state.put("position", Map.of("x", p.getX(), "y", p.getY(), "z", p.getZ()));
        state.put("health", bot.getHealth());
        state.put("on_ground", bot.isOnGround());
        if (!navigationGoal.isEmpty()) state.put("nav_goal", navigationGoal);
        state.put("broken", new ArrayList<>(broken));
        state.put("placed", new ArrayList<>(placed));
        Map<String, String> blocks = new LinkedHashMap<>();
        for (var cell : farmCells) for (int dy = 0; dy <= 1; dy++) {
            int x = cell.get("x"), y = cell.get("y") + dy, z = cell.get("z");
            blocks.put(x + "," + y + "," + z, arena.getBlockAt(x, y, z).getType().name().toLowerCase(Locale.ROOT));
        }
        state.put("blocks", blocks);
        state.put("unsafe_breaks", unsafeBreaks);
        return state;
    }

    private void setupGrounded(long seed, Player bot) {
        // Only fixture facts vary: no action plan is given to the model.
        bot.closeInventory();
        resetTerrain();
        Random layout = new Random(seed);
        int floor = 63 + Math.floorMod(seed, 3);
        for (int x = -6; x <= 24; x++) for (int z = -8; z <= 12; z++) {
            for (int y = 63; y <= 70; y++) set(x, y, z, y <= floor ? Material.STONE : Material.AIR);
        }
        chestPosition = Map.of("x", 2 + layout.nextInt(3), "y", floor + 1, "z", 2 + layout.nextInt(3));
        set(chestPosition.get("x"), floor + 1, chestPosition.get("z"), Material.CHEST);
        ((Chest) arena.getBlockAt(chestPosition.get("x"), floor + 1, chestPosition.get("z")).getState()).getInventory().clear();
        for (int x = 8; x <= 17; x++) {
            set(x, floor + 1, -layout.nextInt(2), Material.STONE);
            set(x, floor + 1, 4 + layout.nextInt(2), Material.OAK_LOG);
            set(x, floor + 1, -4 - layout.nextInt(2), Material.DIRT);
        }
        int farmX = 7 + layout.nextInt(3), farmZ = 7 + layout.nextInt(2);
        for (int i = 0; i < 5; i++) {
            int x = farmX + i;
            set(x, floor, farmZ, Material.FARMLAND);
            farmCells.add(Map.of("x", x, "y", floor, "z", farmZ));
            if (i >= 3) {
                set(x, floor + 1, farmZ, Material.WHEAT);
                var crop = (org.bukkit.block.data.Ageable) arena.getBlockAt(x, floor + 1, farmZ).getBlockData();
                crop.setAge(i == 3 ? crop.getMaximumAge() : 1);
                arena.getBlockAt(x, floor + 1, farmZ).setBlockData(crop, false);
            }
        }
        set(farmX + 2, floor, farmZ + 1, Material.WATER);
        // Avoid crop growth/desiccation changing the expected fixture during a short audit.
        arena.setGameRule(org.bukkit.GameRule.RANDOM_TICK_SPEED, 0);
        bot.getInventory().clear();
        bot.getInventory().setItem(0, new ItemStack(Material.WOODEN_PICKAXE));
        bot.getInventory().setItem(1, new ItemStack(Material.WOODEN_AXE));
        bot.getInventory().setItem(2, new ItemStack(Material.WOODEN_SHOVEL));
        bot.getInventory().setItem(3, new ItemStack(Material.OAK_LOG, 4 + layout.nextInt(3)));
        bot.getInventory().setItem(4, new ItemStack(Material.WHEAT_SEEDS, 6));
        bot.getInventory().setItem(5, new ItemStack(Material.WOODEN_HOE));
        bot.setGameMode(GameMode.SURVIVAL);
        bot.setHealth(20);
        bot.setFoodLevel(20);
        bot.setSaturation(20);
        bot.setExhaustion(0);
        bot.teleport(new Location(arena, 0.5, floor + 1, 0.5));
        bot.updateInventory();
    }

    private void setupNavigation(long seed, Player bot) {
        // One small bounded flat arena. No normal-world plugin installation.
        bot.closeInventory();
        resetTerrain();
        Random layout = new Random(seed);
        for (int x = -5; x <= 5; x++) for (int z = -5; z <= 5; z++) {
            set(x, 63, z, Material.STONE);
            boolean border = Math.abs(x) == 5 || Math.abs(z) == 5;
            for (int y = 64; y <= 70; y++) set(x, y, z, border && y <= 65 ? Material.BEDROCK : Material.AIR);
        }
        // A wall that needs a detour, varied by rotation and opening position.
        int opening = layout.nextBoolean() ? 3 : -3;
        boolean rotated = layout.nextBoolean();
        for (int n = -4; n <= 4; n++) if (n != opening) {
            set(rotated ? n : 0, 64, rotated ? 0 : n, Material.BEDROCK);
            set(rotated ? n : 0, 65, rotated ? 0 : n, Material.BEDROCK);
        }
        int side = layout.nextBoolean() ? 1 : -1;
        int lane = layout.nextInt(5) - 2;
        int sx = rotated ? lane : -3 * side, sz = rotated ? -3 * side : lane;
        int gx = rotated ? lane : 3 * side, gz = rotated ? 3 * side : lane;
        navigationGoal = Map.of("x", gx, "y", 64, "z", gz);
        bot.getInventory().clear();
        bot.setGameMode(GameMode.SURVIVAL);
        bot.setHealth(20);
        bot.setFoodLevel(20);
        bot.setSaturation(20);
        bot.setExhaustion(0);
        bot.teleport(new Location(arena, sx + 0.5, 64, sz + 0.5));
        bot.updateInventory();
    }

    private void setupRecovery(long seed, Player bot) {
        setupGrounded(seed, bot);
        Random layout = new Random(seed);
        int floor = 63 + Math.floorMod(seed, 3);
        // Supplied items and varied environment only; no solution sequence.
        farmCells.clear();
        for (int x = -6; x <= 24; x++) for (int z = -8; z <= 12; z++) {
            Material obstacle = arena.getBlockAt(x, floor + 1, z).getType();
            if (obstacle == Material.OAK_LOG || obstacle == Material.DIRT) set(x, floor + 1, z, Material.AIR);
            if (arena.getBlockAt(x, floor, z).getType() == Material.FARMLAND) {
                set(x, floor, z, Material.STONE);
                set(x, floor + 1, z, Material.AIR);
            }
        }
        for (int x = 15; x <= 22; x++) for (int z : new int[] {-6, -2, 3}) set(x, floor + 1, z, Material.OAK_LOG);
        int z = 7 + layout.nextInt(2);
        for (int i = 0; i < 6; i++) {
            int x = 7 + i * 3;
            set(x, floor, z, layout.nextBoolean() ? Material.GRASS_BLOCK : Material.DIRT);
            set(x, floor + 1, z, i == 5 ? Material.OAK_SAPLING : Material.AIR);
            farmCells.add(Map.of("x", x, "y", floor, "z", z));
        }
        bot.getInventory().setItem(4, new ItemStack(Material.OAK_SAPLING, 2));
        var chest = ((Chest) arena.getBlockAt(chestPosition.get("x"), floor + 1, chestPosition.get("z")).getState()).getInventory();
        chest.setItem(0, new ItemStack(Material.OAK_SAPLING, 7));
        bot.updateInventory();
    }

    @Override public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (!(sender instanceof ConsoleCommandSender) || args.length < 3 || !args[1].matches("[a-f0-9-]{36}")) return true;
        String token = args[1];
        Map<String, Object> reply = new LinkedHashMap<>();
        try {
            targetName = args[2];
            Player bot = getServer().getPlayerExact(targetName);
            if (bot == null) throw new IllegalStateException("Practice bot is offline");
            if (args[0].equals("setup") && args.length == 5 && args[3].equals("rl_navigation")) setupNavigation(Long.parseLong(args[4]), bot);
            else if (args[0].equals("setup") && args.length == 5 && args[3].equals("grounded")) setupGrounded(Long.parseLong(args[4]), bot);
            else if (args[0].equals("setup") && args.length == 5 && args[3].equals("recovery")) setupRecovery(Long.parseLong(args[4]), bot);
            else if (args[0].equals("setup") && args.length == 4) setup(args[3], bot);
            else if (!args[0].equals("snapshot") && !args[0].equals("check")) throw new IllegalArgumentException("Unknown command");
            reply.put("ok", true);
            var state = snapshot(bot);
            if (args[0].equals("check") && args.length == 4) {
                String data = new String(Base64.getUrlDecoder().decode(args[3]), StandardCharsets.UTF_8);
                List<Map<String, Object>> goals = json.fromJson(data, new TypeToken<List<Map<String, Object>>>(){}.getType());
                if (goals.size() > 16) throw new IllegalArgumentException("Too many goals");
                Map<String, String> blocks = new LinkedHashMap<>();
                if (state.get("blocks") instanceof Map<?, ?> existing) {
                    for (var entry : existing.entrySet()) blocks.put(entry.getKey().toString(), entry.getValue().toString());
                }
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

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPlace(BlockPlaceEvent event) {
        if (!event.getPlayer().getName().equals(targetName)) return;
        var block = event.getBlockPlaced();
        placed.add(Map.of("block", block.getType().name().toLowerCase(Locale.ROOT), "x", block.getX(), "y", block.getY(), "z", block.getZ()));
    }
}
