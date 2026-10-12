package jp.aicity.test;

import com.google.gson.*;
import java.lang.reflect.Proxy;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Consumer;
import jp.aicity.*;
import org.bukkit.*;
import org.bukkit.block.Block;
import org.bukkit.command.*;
import org.bukkit.entity.Player;
import org.bukkit.event.*;
import org.bukkit.plugin.java.JavaPlugin;

/** Never install on a live server. Test-only console command and fake operator. */
public final class IntegrationHarness extends JavaPlugin implements Listener {
  private final ExecutorService io = Executors.newSingleThreadExecutor();
  private final Gson gson = new GsonBuilder().setPrettyPrinting().create();
  private final JsonObject report = new JsonObject();
  private CityPlugin city;
  private Player player;
  private World world;
  private GameJob original;
  private Map<String, String> before;
  private Block sentinel;
  private int cancelAt = -1, writes, maxWrites, tickWrites, lastTick = -1;
  private boolean crash;
  private final Set<Long> heldChunks = new HashSet<>();

  @Override
  public void onEnable() {
    city = (CityPlugin) Bukkit.getPluginManager().getPlugin("MinecraftAICity");
    world = Bukkit.getWorlds().getFirst();
    UUID actor = UUID.fromString(city.getConfig().getString("test-player-uuid"));
    player =
        (Player)
            Proxy.newProxyInstance(
                Player.class.getClassLoader(),
                new Class[] {Player.class},
                (proxy, method, args) ->
                    switch (method.getName()) {
                      case "getUniqueId" -> actor;
                      case "getName" -> "IntegrationOperator";
                      case "getWorld" -> world;
                      case "getServer" -> Bukkit.getServer();
                      case "getLocation" -> new Location(world, .5, 84, .5, 90, 0);
                      case "hasPermission", "isOnline", "isOp" -> true;
                      case "sendMessage" -> {
                        getLogger().info("PLAYER: " + Arrays.toString(args));
                        yield null;
                      }
                      case "toString" -> "IntegrationOperator";
                      case "hashCode" -> actor.hashCode();
                      case "equals" -> proxy == args[0];
                      default -> {
                        Class<?> type = method.getReturnType();
                        yield type == boolean.class
                            ? false
                            : type == int.class
                                ? 0
                                : type == double.class ? 0.0 : type == float.class ? 0f : null;
                      }
                    });
    city.getConfig().set("anchor.configured", true);
    city.getConfig().set("anchor.world-name", world.getName());
    city.getConfig().set("anchor.world-uuid", world.getUID().toString());
    city.getConfig().set("anchor.x", 0);
    city.getConfig().set("anchor.y", 80);
    city.getConfig().set("anchor.z", 0);
    city.saveConfig();
    Bukkit.getPluginManager().registerEvents(this, this);
    Objects.requireNonNull(getCommand("citytest"))
        .setExecutor(
            (sender, command, label, args) -> {
              if (!(sender instanceof ConsoleCommandSender)) return true;
              if (args.length > 0 && args[0].equals("recover")) recover();
              else start();
              return true;
            });
  }

  @Override
  public void onDisable() {
    for (long key : heldChunks) world.removePluginChunkTicket((int) (key >> 32), (int) key, this);
    io.shutdownNow();
  }

  private <T> void async(Callable<T> action, Consumer<T> callback) {
    CompletableFuture.supplyAsync(
            () -> {
              try {
                return action.call();
              } catch (Exception e) {
                throw new CompletionException(e);
              }
            },
            io)
        .whenComplete(
            (result, error) -> {
              if (!isEnabled()) return;
              Bukkit.getScheduler()
                  .runTask(
                      this,
                      () -> {
                        if (error != null) fail(error);
                        else callback.accept(result);
                      });
            });
  }

  private void fail(Throwable error) {
    getLogger().severe("CITY_INTEGRATION_FAILURE: " + error);
    report.addProperty("success", false);
    report.addProperty("error", error.toString());
    saveReport();
  }

  private void require(boolean condition, String message) {
    if (!condition) throw new IllegalStateException(message);
  }

  private void later(Runnable task) {
    Bukkit.getScheduler()
        .runTaskLater(
            this,
            () -> {
              try {
                task.run();
              } catch (Exception error) {
                fail(error);
              }
            },
            1L);
  }

  private String key(BlockChange c) {
    return c.x() + "," + c.y() + "," + c.z();
  }

  private Block block(GameJob job, BlockChange c) {
    int[] p = job.origin().world(c.x(), c.y(), c.z());
    return world.getBlockAt(p[0], p[1], p[2]);
  }

  private void load(GameJob job, Runnable done) {
    Set<Long> chunks = new HashSet<>();
    for (BlockChange c : job.changes()) {
      int[] p = job.origin().world(c.x(), c.y(), c.z());
      chunks.add(((long) (p[0] >> 4) << 32) ^ ((p[2] >> 4) & 0xffffffffL));
    }
    List<CompletableFuture<?>> waits = new ArrayList<>();
    for (long k : chunks) waits.add(world.getChunkAtAsync((int) (k >> 32), (int) k, true));
    CompletableFuture.allOf(waits.toArray(CompletableFuture[]::new))
        .whenComplete(
            (ignored, error) -> {
              if (error != null) fail(error);
              else
                Bukkit.getScheduler()
                    .runTask(
                        this,
                        () -> {
                          for (long key : chunks) {
                            world.addPluginChunkTicket((int) (key >> 32), (int) key, this);
                            heldChunks.add(key);
                          }
                          done.run();
                        });
            });
  }

  private void awaitPending(boolean undo, Consumer<GameJob> done, int count) {
    GameJob job = city.pending(player.getUniqueId());
    if (job != null && job.info().get("action").getAsString().equals(undo ? "undo" : "apply")) {
      done.accept(job);
      return;
    }
    if (count > 600) {
      fail(new IllegalStateException("計画生成タイムアウト"));
      return;
    }
    later(() -> awaitPending(undo, done, count + 1));
  }

  private void command(String... args) {
    city.onCommand(player, Objects.requireNonNull(city.getCommand("city")), "city", args);
  }

  private void plan(Consumer<GameJob> done) {
    command("plan", "中央区の空き地にコンビニを作って");
    awaitPending(false, done, 0);
  }

  private void start() {
    require(!city.isBusy(), "Plugin must be ready");
    require(Bukkit.getPluginCommand("city") != null, "city command registration");
    report.addProperty("paper", Bukkit.getVersion());
    report.addProperty("java", System.getProperty("java.version"));
    report.addProperty(
        "worldedit",
        Bukkit.getPluginManager().getPlugin("WorldEdit").getDescription().getVersion());
    command("status");
    plan(
        job -> {
          original = job;
          before = new HashMap<>();
          load(
              job,
              () ->
                  foundation(
                      job,
                      0,
                      () -> {
                        sentinel =
                            world.getBlockAt(
                                job.origin().x()
                                    + job.info().getAsJsonArray("bounds").get(3).getAsInt()
                                    + 2,
                                job.origin().y() + 3,
                                job.origin().z()
                                    + job.info().getAsJsonArray("bounds").get(2).getAsInt());
                        sentinel.setType(Material.LAPIS_BLOCK, false);
                        CompletableFuture<Void> approved = city.approveFor(player);
                        boolean blocked = false;
                        try {
                          city.approveFor(player);
                        } catch (IllegalArgumentException expected) {
                          blocked = true;
                        }
                        report.addProperty("double_approval_blocked", blocked);
                        approved.whenComplete(
                            (ignored, error) -> {
                              if (error != null) {
                                fail(error);
                                return;
                              }
                              try {
                                for (BlockChange c : job.changes())
                                  require(
                                      block(job, c)
                                          .getBlockData()
                                          .getAsString()
                                          .equals(Bukkit.createBlockData(c.after()).getAsString()),
                                      "after mismatch " + key(c));
                                report.addProperty("world_applied", true);
                                report.addProperty("changed_blocks", job.changes().size());
                                command("undo");
                                awaitPending(
                                    true,
                                    reverse ->
                                        city.approveFor(player)
                                            .whenComplete(
                                                (done, undoError) -> {
                                                  if (undoError != null) {
                                                    fail(undoError);
                                                    return;
                                                  }
                                                  try {
                                                    checkOriginal();
                                                    report.addProperty("world_undo_exact", true);
                                                    collision();
                                                  } catch (Exception e) {
                                                    fail(e);
                                                  }
                                                }),
                                    0);
                              } catch (Exception e) {
                                fail(e);
                              }
                            });
                      }));
        });
  }

  private void foundation(GameJob job, int start, Runnable done) {
    int end = Math.min(start + 256, job.changes().size());
    for (int i = start; i < end; i++) {
      BlockChange c = job.changes().get(i);
      Block b = block(job, c);
      b.setBlockData(Bukkit.createBlockData(c.before()), false);
      before.put(key(c), b.getBlockData().getAsString());
    }
    if (end == job.changes().size()) done.run();
    else later(() -> foundation(job, end, done));
  }

  private void checkOriginal() {
    for (BlockChange c : original.changes())
      require(
          block(original, c).getBlockData().getAsString().equals(before.get(key(c))),
          "restoration mismatch " + key(c));
    require(sentinel.getType() == Material.LAPIS_BLOCK, "unrelated block changed");
  }

  private void collision() {
    BlockChange c = original.changes().getFirst();
    block(original, c).setType(Material.GOLD_BLOCK, false);
    plan(
        job ->
            city.approveFor(player)
                .whenComplete(
                    (ignored, error) -> {
                      try {
                        require(error != null, "collision should fail");
                        require(
                            block(original, c).getType() == Material.GOLD_BLOCK,
                            "protected collision overwritten");
                        report.addProperty("collision_rejected", true);
                        block(original, c).setBlockData(Bukkit.createBlockData(c.before()), false);
                        failure();
                      } catch (Exception e) {
                        fail(e);
                      }
                    }));
  }

  private void failure() {
    writes = 0;
    cancelAt = 20;
    plan(
        job ->
            city.approveFor(player)
                .whenComplete(
                    (ignored, error) -> {
                      cancelAt = -1;
                      try {
                        require(error != null, "injected veto should fail");
                        checkOriginal();
                        report.addProperty("failed_write_rollback_exact", true);
                        report.addProperty("max_writes_per_tick", maxWrites);
                        require(maxWrites <= 64, "tick block cap");
                        crash();
                      } catch (Exception e) {
                        fail(e);
                      }
                    }));
  }

  @EventHandler
  public void write(CityBlockChangeEvent event) {
    int tick = Bukkit.getCurrentTick();
    if (tick != lastTick) {
      lastTick = tick;
      tickWrites = 0;
    }
    maxWrites = Math.max(maxWrites, ++tickWrites);
    if (cancelAt >= 0 && writes++ == cancelAt) event.setCancelled(true);
    if (crash && writes++ == 0) later(Bukkit::shutdown);
  }

  private void crash() {
    writes = 0;
    crash = true;
    plan(
        job ->
            async(
                () -> {
                  Files.createDirectories(getDataFolder().toPath());
                  Files.writeString(
                      getDataFolder().toPath().resolve("crash-job.json"),
                      gson.toJson(new Journal.Backup(job.info(), job.changes(), "pending")));
                  return true;
                },
                ignored -> {
                  report.addProperty("phase_one", true);
                  saveReport();
                  city.approveFor(player);
                }));
  }

  private void recover() {
    async(
        () ->
            gson.fromJson(
                Files.readString(getDataFolder().toPath().resolve("crash-job.json")),
                Journal.Backup.class),
        backup -> {
          original = backup.job();
          before = new HashMap<>();
          for (BlockChange c : original.changes())
            before.put(key(c), Bukkit.createBlockData(c.before()).getAsString());
          load(
              original,
              () -> {
                command("recover");
                awaitRecovery(0);
              });
        });
  }

  private void awaitRecovery(int count) {
    if (city.isBusy()) {
      if (count > 600) {
        fail(new IllegalStateException("recovery timeout"));
        return;
      }
      later(() -> awaitRecovery(count + 1));
      return;
    }
    try {
      for (BlockChange c : original.changes())
        require(
            block(original, c).getBlockData().getAsString().equals(before.get(key(c))),
            "restart recovery mismatch");
      report.addProperty("restart_world_recovery", true);
      report.addProperty("success", true);
      saveReport();
      getLogger().info("CITY_INTEGRATION_SUCCESS");
    } catch (Exception e) {
      fail(e);
    }
  }

  private void saveReport() {
    JsonObject copy = report.deepCopy();
    io.submit(
        () -> {
          try {
            Files.createDirectories(getDataFolder().toPath());
            Files.writeString(getDataFolder().toPath().resolve("report.json"), gson.toJson(copy));
          } catch (Exception error) {
            getLogger().severe(error.toString());
          }
        });
  }
}
