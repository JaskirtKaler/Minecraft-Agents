package dev.minecraftagents.debug;

import java.lang.reflect.Proxy;
import java.util.HashMap;
import java.util.Map;
import org.bukkit.Difficulty;
import org.bukkit.World;
import org.bukkit.entity.Player;
import org.bukkit.event.entity.EntityExhaustionEvent;
import org.bukkit.event.entity.FoodLevelChangeEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.world.WorldLoadEvent;

/** Real event/API contracts with mutable proxies; no player world is opened. */
public final class TrainingModeTest {
    @SuppressWarnings("unchecked")
    private static <T> T proxy(Class<T> type, Map<String, Object> mutations) {
        return (T) Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[] {type}, (instance, method, args) -> {
            if (method.getName().startsWith("set")) {
                mutations.put(method.getName(), args[0]);
                return null;
            }
            if (method.getName().equals("toString")) return "training-test";
            if (method.getName().equals("hashCode")) return System.identityHashCode(instance);
            if (method.getName().equals("equals")) return instance == args[0];
            return null;
        });
    }

    public static void main(String[] args) {
        var changes = new HashMap<String, Object>();
        Player player = proxy(Player.class, changes);
        World world = proxy(World.class, changes);
        var training = new TrainingModeListener(true);
        training.initializeWorld(world);
        training.onWorldLoad(new WorldLoadEvent(world));
        assert changes.get("setDifficulty") == Difficulty.PEACEFUL;
        training.onJoin(new PlayerJoinEvent(player, (String) null));
        assert changes.get("setFoodLevel").equals(20);
        assert changes.get("setSaturation").equals(20.0f);
        assert changes.get("setExhaustion").equals(0.0f);
        assert !changes.containsKey("setGameMode") : "Must remain Survival";
        var food = new FoodLevelChangeEvent(player, 10);
        training.onFood(food);
        assert food.isCancelled();
        var exhausted = new EntityExhaustionEvent(player, EntityExhaustionEvent.ExhaustionReason.UNKNOWN, 1.0f);
        training.onExhaustion(exhausted);
        assert exhausted.isCancelled();

        changes.clear();
        var disabled = new TrainingModeListener(false);
        disabled.initializeWorld(world);
        disabled.maintainPlayer(player);
        food = new FoodLevelChangeEvent(player, 10);
        disabled.onFood(food);
        exhausted = new EntityExhaustionEvent(player, EntityExhaustionEvent.ExhaustionReason.UNKNOWN, 1.0f);
        disabled.onExhaustion(exhausted);
        assert changes.isEmpty();
        assert !food.isCancelled();
        assert !exhausted.isCancelled();
        System.out.println("Peaceful world load, join food, hunger/exhaustion cancellation, and opt-out tests passed.");
    }
}
