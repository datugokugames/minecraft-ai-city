package jp.aicity;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Consumer;
import org.bukkit.*;
import org.bukkit.block.*;
import org.bukkit.block.data.BlockData;
import org.bukkit.command.*;
import org.bukkit.entity.*;
import org.bukkit.event.*;
import org.bukkit.event.block.*;
import org.bukkit.event.entity.EntityExplodeEvent;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.util.BoundingBox;

public final class CityPlugin extends JavaPlugin implements CommandExecutor, Listener {
  private final ExecutorService io = Executors.newFixedThreadPool(2);
  private final Map<UUID, GameJob> pending = new HashMap<>();
  private final Map<UUID, int[]> first = new HashMap<>(), second = new HashMap<>();
  private BackendClient backend;
  private Journal journal;
  private List<Journal.Backup> recovery = List.of();
  private boolean busy;
  private final Set<UUID> cancellations = new HashSet<>();
  private Run current;
  private int perTick, maxBlocks, maxVolume;
  private long nanos;
  private Set<Material> protectedMaterials;

  @Override
  public void onEnable() {
    saveDefaultConfig();
    if (getConfig().getString("server-id", "").isEmpty()) {
      getConfig().set("server-id", UUID.randomUUID().toString());
      saveConfig();
    }
    Objects.requireNonNull(getCommand("city")).setExecutor(this);
    getServer().getPluginManager().registerEvents(this, this);
    perTick = Math.max(1, Math.min(256, getConfig().getInt("blocks-per-tick", 64)));
    maxBlocks = Math.max(1, Math.min(20000, getConfig().getInt("max-changed-blocks", 5000)));
    maxVolume = Math.max(1, Math.min(1048576, getConfig().getInt("max-patch-volume", 262144)));
    nanos = Math.max(1, Math.min(10, getConfig().getInt("millis-per-tick", 4))) * 1000000L;
    protectedMaterials = new HashSet<>();
    for (String name : getConfig().getStringList("protected-materials"))
      protectedMaterials.add(Material.valueOf(name));
    try {
      backend =
          new BackendClient(
              getConfig().getString("backend-url"), getConfig().getString("backend-token", ""));
      journal = new Journal(getDataFolder().toPath().resolve("backups"));
      busy = true;
      async(
          () -> journal.unfinished(),
          list -> {
            recovery = list;
            busy = !list.isEmpty();
            getLogger()
                .info(
                    "Ready. WorldEdit="
                        + getServer()
                            .getPluginManager()
                            .getPlugin("WorldEdit")
                            .getDescription()
                            .getVersion()
                        + ", pending recovery="
                        + list.size());
          },
          error -> {
            busy = true;
            getLogger().severe("バックアップ確認失敗: " + error.getMessage());
          });
    } catch (Exception error) {
      backend = null;
      getLogger().warning(error.getMessage() + " /city identity とconfig.ymlを確認してください。");
    }
  }

  @Override
  public void onDisable() {
    // Durable WAL remains for /city recover after restart; no blocking world IO here.
    if (current != null) release(current);
    io.shutdownNow();
  }

  private <T> void async(Callable<T> task, Consumer<T> success, Consumer<Throwable> failure) {
    CompletableFuture.supplyAsync(
            () -> {
              try {
                return task.call();
              } catch (Exception e) {
                throw new CompletionException(e);
              }
            },
            io)
        .whenComplete(
            (value, error) -> {
              if (!isEnabled()) return;
              getServer()
                  .getScheduler()
                  .runTask(
                      this,
                      () -> {
                        if (error == null) success.accept(value);
                        else failure.accept(error.getCause() == null ? error : error.getCause());
                      });
            });
  }

  private void later(Runnable action) {
    if (isEnabled()) getServer().getScheduler().runTaskLater(this, action, 1L);
  }

  private void tell(CommandSender sender, String message) {
    if (sender != null) sender.sendMessage("[AI City] " + message);
    getLogger().info(message);
  }

  private void permitted(Player player, String permission) {
    if (!player.hasPermission(permission))
      throw new IllegalArgumentException("権限がありません: " + permission);
  }

  private void ready() {
    if (backend == null)
      throw new IllegalArgumentException("backend-url / backend-tokenを設定してサーバーを再起動してください。");
    if (busy)
      throw new IllegalArgumentException("他の建築中または復旧待ちです。/city status /city recover を確認してください。");
  }

  public BackendClient backend() {
    return backend;
  }

  public GameJob pending(UUID actor) {
    return pending.get(actor);
  }

  public boolean isBusy() {
    return busy;
  }

  public JsonObject binding(Player player) {
    if (!getConfig().getBoolean("anchor.configured"))
      throw new IllegalArgumentException("/city anchor <都市X> <都市Z> で現在位置と都市の原点を結び付けてください。");
    String world = getConfig().getString("anchor.world-uuid");
    if (!player.getWorld().getUID().toString().equals(world))
      throw new IllegalArgumentException("anchorと異なるワールドです。");
    JsonObject b = new JsonObject();
    b.addProperty("server_id", getConfig().getString("server-id"));
    b.addProperty("world_uuid", world);
    b.addProperty("world_name", getConfig().getString("anchor.world-name"));
    JsonArray origin = new JsonArray();
    for (String a : List.of("x", "y", "z")) origin.add(getConfig().getInt("anchor." + a));
    b.add("origin", origin);
    b.addProperty("rotation", 0);
    return b;
  }

  private Coordinates origin() {
    return new Coordinates(
        getConfig().getInt("anchor.x"),
        getConfig().getInt("anchor.y"),
        getConfig().getInt("anchor.z"));
  }

  public CompletableFuture<GameJob> planFor(Player player, String instruction) {
    permitted(player, "city.plan");
    ready();
    JsonObject binding = binding(player);
    Location location = player.getLocation();
    int[] local = origin().local(location.getX(), location.getY(), location.getZ());
    JsonObject selected = new JsonObject();
    int[] a = first.get(player.getUniqueId()), b = second.get(player.getUniqueId());
    if ((a == null) != (b == null))
      throw new IllegalArgumentException("pos1とpos2を両方指定するか /city clear で解除してください。");
    if (a != null) {
      JsonArray bounds = new JsonArray();
      for (int v :
          new int[] {
            Math.min(a[0], b[0]),
            Math.min(a[2], b[2]),
            Math.max(a[0], b[0]) + 1,
            Math.max(a[2], b[2]) + 1
          }) bounds.add(v);
      selected.add("bounds", bounds);
    }
    boolean range = a != null;
    UUID actor = player.getUniqueId();
    CompletableFuture<GameJob> result = new CompletableFuture<>();
    busy = true;
    async(
        () -> {
          JsonObject state = backend.call("state", actor, null);
          boolean named = instruction.contains(state.get("city_name").getAsString());
          for (String field : List.of("wards", "districts"))
            for (JsonElement item : state.getAsJsonArray(field))
              named |= instruction.contains(item.getAsJsonObject().get("name").getAsString());
          if (!range && (!named || instruction.contains("ここ") || instruction.contains("この辺"))) {
            JsonArray point = new JsonArray();
            point.add(local[0] + .5);
            point.add(local[2] + .5);
            selected.add("point", point);
          }
          JsonObject req = new JsonObject();
          req.addProperty("revision", state.get("revision").getAsString());
          req.addProperty("instruction", instruction);
          req.add("selection", selected);
          req.add("binding", binding);
          JsonObject response = backend.call("plan", actor, req);
          return PatchLoader.load(
              backend,
              actor,
              response.getAsJsonObject("job"),
              getDataFolder().toPath().resolve("cache"),
              maxBlocks,
              maxVolume);
        },
        job -> {
          busy = false;
          if (cancellations.remove(actor)) {
            JsonObject cancel = new JsonObject();
            cancel.addProperty("job", job.id());
            async(
                () -> backend.call("cancel", actor, cancel),
                ignored -> tell(player, "計画を見送りました。"),
                error -> tell(player, error.getMessage()));
            result.completeExceptionally(new IllegalStateException("計画をキャンセルしました。"));
            return;
          }
          pending.put(actor, job);
          preview(player, job);
          result.complete(job);
        },
        error -> {
          busy = false;
          cancellations.remove(actor);
          tell(player, "計画失敗: " + error.getMessage());
          result.completeExceptionally(error);
        });
    return result;
  }

  private void adopt(Player player, String review, boolean undo) {
    permitted(player, undo ? "city.undo" : "city.plan");
    ready();
    JsonObject binding = binding(player);
    UUID actor = player.getUniqueId();
    busy = true;
    async(
        () -> {
          JsonObject req = new JsonObject();
          req.addProperty("review", review);
          req.add("binding", binding);
          JsonObject info = backend.call(undo ? "undo-plan" : "adopt", actor, undo ? binding : req);
          return PatchLoader.load(
              backend,
              actor,
              info,
              getDataFolder().toPath().resolve("cache"),
              maxBlocks,
              maxVolume);
        },
        job -> {
          busy = false;
          if (cancellations.remove(actor)) {
            JsonObject cancel = new JsonObject();
            cancel.addProperty("job", job.id());
            async(
                () -> backend.call("cancel", actor, cancel),
                ignored -> tell(player, "計画を見送りました。"),
                error -> tell(player, error.getMessage()));
            return;
          }
          pending.put(actor, job);
          preview(player, job);
        },
        error -> {
          busy = false;
          cancellations.remove(actor);
          tell(player, "計画取得失敗: " + error.getMessage());
        });
  }

  private void preview(Player player, GameJob job) {
    tell(
        player,
        (job.info().get("action").getAsString().equals("undo") ? "Undo案" : "建築案")
            + ": 変更 "
            + job.changes().size()
            + " blocks / 計画ID "
            + job.planId());
    tell(
        player,
        "都市範囲 "
            + job.info().get("bounds")
            + " / 原点 "
            + job.binding().get("origin")
            + "。承認 /city approve、見送り /city cancel");
    if (job.info().has("architecture") && !job.info().get("architecture").isJsonNull()) {
      JsonObject spec = job.info().getAsJsonObject("architecture");
      tell(
          player,
          "外装 "
              + spec.get("category")
              + " / 道路側幅 "
              + spec.get("site_width")
              + " × 奥行き "
              + spec.get("site_depth")
              + " / 高さ "
              + spec.get("height")
              + " / "
              + spec.get("floors")
              + "階 / "
              + spec.get("shape"));
    }
    try {
      Run check = new Run(job, player, new CompletableFuture<>());
      particles(check);
      scan(
          check,
          0,
          () -> tell(player, "ワールド衝突検査: 合格。範囲内のプレイヤー・生物は建築前に移動してください。"),
          error -> tell(player, "ワールド衝突・保護検査: " + error.getMessage()));
    } catch (Exception error) {
      tell(player, "ワールド確認: " + error.getMessage());
    }
  }

  public CompletableFuture<Void> approveFor(Player player) {
    permitted(player, "city.approve");
    ready();
    GameJob job = pending.get(player.getUniqueId());
    if (job == null) throw new IllegalArgumentException("先に /city plan または /city undo を指定してください。");
    if (!binding(player).equals(job.binding()))
      throw new IllegalArgumentException("anchorが変わっています。再計画してください。");
    CompletableFuture<Void> future = new CompletableFuture<>();
    busy = true;
    async(
        () -> {
          journal.save(job, "reserved"); // Durable even if prepare's reply is lost.
          JsonObject req = new JsonObject();
          req.addProperty("job", job.id());
          req.addProperty("revision", job.revision());
          req.addProperty("approval", job.planId());
          req.add("binding", job.binding());
          return backend.call("prepare", job.actor(), req);
        },
        prepared -> {
          try {
            Run run = new Run(job, player, future);
            run.cancelling = cancellations.remove(job.actor());
            current = run;
            reserve(run);
            entities(run);
            permitted(player, "city.approve");
            scan(
                run,
                0,
                () ->
                    async(
                        () -> {
                          journal.save(job, "building");
                          return true;
                        },
                        ignored -> write(run, 0),
                        error -> fail(run, error)),
                error -> fail(run, error));
          } catch (Exception error) {
            recoverFailure(job, player, future, error);
          }
        },
        error -> recoverFailure(job, player, future, error));
    return future;
  }

  private final class Run {
    final GameJob job;
    final Player player;
    final World world;
    final Coordinates origin;
    final BlockTransaction transaction;
    final Set<Long> chunks = new HashSet<>();
    final CompletableFuture<Void> result;
    boolean cancelling, restoring, committing;
    int lastReported, backendReported;
    final Map<String, BlockData> palette = new HashMap<>();

    Run(GameJob job, Player player, CompletableFuture<Void> result) {
      this.job = job;
      this.player = player;
      this.result = result;
      world = Bukkit.getWorld(job.worldId());
      origin = job.origin();
      if (world == null) throw new IllegalArgumentException("対象ワールドが読み込まれていません。");
      List<BlockChange> normalized =
          job.changes().stream()
              .map(
                  c ->
                      new BlockChange(
                          c.x(),
                          c.y(),
                          c.z(),
                          data(c.before()).getAsString(),
                          data(c.after()).getAsString()))
              .toList();
      transaction =
          new BlockTransaction(
              normalized,
              new BlockTransaction.Access() {
                public String get(BlockChange c) {
                  return transactionWorld(c);
                }

                public void set(BlockChange c, String state) {
                  Block block = block(c);
                  if (!restoring) {
                    CityBlockChangeEvent event = new CityBlockChangeEvent(job.id(), block);
                    Bukkit.getPluginManager().callEvent(event);
                    if (event.isCancelled())
                      throw new IllegalStateException("保護プラグインがブロック変更を拒否しました。");
                  }
                  block.setBlockData(data(state), false);
                }
              },
              maxBlocks);
    }

    String transactionWorld(BlockChange c) {
      Block block = block(c);
      if (protectedMaterials.contains(block.getType()) || block.getState() instanceof TileState)
        throw new IllegalStateException("保護ブロック: " + block.getLocation());
      return block.getBlockData().getAsString();
    }

    BlockData data(String state) {
      return palette.computeIfAbsent(state, Bukkit::createBlockData);
    }

    Block block(BlockChange c) {
      int[] p = origin.world(c.x(), c.y(), c.z());
      if (p[1] < world.getMinHeight()
          || p[1] >= world.getMaxHeight()
          || !world.getWorldBorder().isInside(new Location(world, p[0], p[1], p[2])))
        throw new IllegalStateException("高さまたはワールド境界の外です。");
      if (!world.isChunkLoaded(p[0] >> 4, p[2] >> 4))
        throw new IllegalStateException("対象チャンクを先に読み込んでください。");
      return world.getBlockAt(p[0], p[1], p[2]);
    }

    BoundingBox bounds() {
      JsonArray a = job.info().getAsJsonArray("bounds");
      return new BoundingBox(
          origin.x() + a.get(0).getAsInt(),
          origin.y() + a.get(1).getAsInt(),
          origin.z() + a.get(2).getAsInt(),
          origin.x() + a.get(3).getAsInt(),
          origin.y() + a.get(4).getAsInt(),
          origin.z() + a.get(5).getAsInt());
    }
  }

  private void reserve(Run run) {
    if (Bukkit.getPluginManager().isPluginEnabled("WorldGuard"))
      throw new IllegalStateException("WorldGuardの地域権限連携は未対応です。安全のため建築を停止します。");
    for (BlockChange c : run.job.changes()) {
      Block b = run.block(c);
      long key = ((long) (b.getX() >> 4) << 32) ^ ((b.getZ() >> 4) & 0xffffffffL);
      if (run.chunks.add(key)) run.world.addPluginChunkTicket(b.getX() >> 4, b.getZ() >> 4, this);
    }
  }

  private void release(Run run) {
    for (long key : run.chunks)
      run.world.removePluginChunkTicket((int) (key >> 32), (int) key, this);
    run.chunks.clear();
  }

  private void entities(Run run) {
    if (!run.world.getNearbyEntities(run.bounds().expand(1, 1, 1)).isEmpty())
      throw new IllegalStateException("建築範囲と周囲1ブロックにプレイヤー・エンティティがいます。移動してください。");
  }

  private void particles(Run run) {
    BoundingBox b = run.bounds();
    for (double x : new double[] {b.getMinX(), b.getMaxX()})
      for (double z : new double[] {b.getMinZ(), b.getMaxZ()})
        for (double y : new double[] {b.getMinY(), b.getMaxY()})
          run.world.spawnParticle(Particle.END_ROD, x, y, z, 1, 0, 0, 0, 0);
  }

  private void scan(Run run, int start, Runnable complete, Consumer<Throwable> failure) {
    try {
      int i = start;
      long deadline = System.nanoTime() + nanos;
      for (int n = 0;
          i < run.transaction.size() && n < perTick && (n == 0 || System.nanoTime() < deadline);
          n++, i++) {
        BlockChange c = run.job.changes().get(i);
        String before = run.data(c.before()).getAsString();
        // WorldEdit states contain all properties, normalized by the installed Bukkit version.
        if (!run.transactionWorld(c).equals(before))
          throw new IllegalStateException("既存ワールドと都市が不一致: " + c.x() + "," + c.y() + "," + c.z());
      }
      if (i == run.transaction.size()) complete.run();
      else {
        int next = i;
        later(() -> scan(run, next, complete, failure));
      }
    } catch (Exception error) {
      failure.accept(error);
    }
  }

  private void write(Run run, int start) {
    if (run.cancelling) {
      fail(run, new IllegalStateException("建築をキャンセルしました。"));
      return;
    }
    try {
      if (run.player != null && !run.player.isOnline())
        throw new IllegalStateException("プレイヤーが退出しました。");
      if (run.player != null) permitted(run.player, "city.approve");
      entities(run);
    } catch (Exception error) {
      fail(run, error);
      return;
    }
    int end = Math.min(run.transaction.size(), start + perTick);
    async(
        () -> {
          if (start / 256 > run.backendReported / 256) {
            JsonObject progress = new JsonObject();
            progress.addProperty("job", run.job.id());
            progress.addProperty("completed", start);
            backend.call("progress", run.job.actor(), progress);
            run.backendReported = start;
          }
          journal.beforeBatch(run.job.id(), end);
          return true;
        },
        ignored -> {
          try {
            if (run.cancelling) throw new IllegalStateException("建築をキャンセルしました。");
            if (run.player != null) {
              if (!run.player.isOnline()) throw new IllegalStateException("プレイヤーが退出しました。");
              permitted(run.player, "city.approve");
            }
            entities(run);
            int i = start;
            long deadline = System.nanoTime() + nanos;
            for (int n = 0; i < end && (n == 0 || System.nanoTime() < deadline); n++, i++)
              run.transaction.apply(i, i + 1);
            int next = i;
            tellProgress(run, next);
            if (next == run.transaction.size()) commit(run);
            else later(() -> write(run, next));
          } catch (Exception error) {
            fail(run, error);
          }
        },
        error -> fail(run, error));
  }

  private void tellProgress(Run run, int completed) {
    if (completed == run.transaction.size() || completed / 256 > run.lastReported / 256) {
      run.lastReported = completed;
      tell(run.player, "建築進捗 " + completed + " / " + run.transaction.size());
    }
  }

  private void commit(Run run) {
    run.committing = true;
    async(
        () -> {
          journal.save(run.job, "world_applied");
          JsonObject p = new JsonObject();
          p.addProperty("job", run.job.id());
          p.addProperty("completed", run.transaction.size());
          backend.call("progress", run.job.actor(), p);
          JsonObject f = new JsonObject();
          f.addProperty("job", run.job.id());
          f.addProperty("outcome", "commit");
          backend.call("finish", run.job.actor(), f);
          journal.save(run.job, "committed");
          return true;
        },
        ignored -> {
          release(run);
          current = null;
          busy = false;
          pending.remove(run.job.actor());
          busy = !recovery.isEmpty();
          tell(run.player, "建築と都市状態の保存が完了しました。/city undo で取り消し案を確認できます。");
          run.result.complete(null);
        },
        error -> fail(run, error));
  }

  private void fail(Run run, Throwable error) {
    release(run);
    current = null;
    recoverFailure(run.job, run.player, run.result, error);
  }

  private void recoverFailure(
      GameJob job, Player player, CompletableFuture<Void> result, Throwable error) {
    if (current != null) {
      release(current);
      current = null;
    }
    tell(player, "建築停止・復旧を開始: " + error.getMessage());
    recoverJob(job, player, result);
  }

  private void recoverJob(GameJob job, Player player, CompletableFuture<Void> result) {
    busy = true;
    async(
        () ->
            new Object[] {
              backend.call("jobs/" + job.id(), job.actor(), null), journal.attempted(job)
            },
        values -> {
          JsonObject state = (JsonObject) values[0];
          int attempted = (Integer) values[1];
          String status = state.get("status").getAsString();
          if (attempted == 0 && !status.equals("committed")) {
            async(
                () -> {
                  if (Set.of("prepared", "running").contains(status)) {
                    JsonObject f = new JsonObject();
                    f.addProperty("job", job.id());
                    f.addProperty("outcome", "rollback");
                    backend.call("finish", job.actor(), f);
                  }
                  journal.save(job, "rolled_back");
                  return true;
                },
                ignored -> {
                  busy = !recovery.isEmpty();
                  pending.remove(job.actor());
                  result.completeExceptionally(new IllegalStateException("ワールド変更前に停止しました。"));
                  tell(player, "ワールドは変更せず、承認を取り消しました。");
                },
                error -> recoveryRequired(job, player, result, error));
            return;
          }
          try {
            Run run = new Run(job, player, result);
            current = run;
            reserve(run);
            if (status.equals("committed")) {
              verifyCommitted(run, 0);
              return;
            }
            run.restoring = true;
            restore(run, attempted, status);
          } catch (Exception error) {
            recoveryRequired(job, player, result, error);
          }
        },
        error -> recoveryRequired(job, player, result, error));
  }

  private void verifyCommitted(Run run, int start) {
    try {
      int end = Math.min(run.transaction.size(), start + perTick);
      for (int i = start; i < end; i++) {
        BlockChange c = run.job.changes().get(i);
        if (!run.transactionWorld(c).equals(run.data(c.after()).getAsString()))
          throw new IllegalStateException("確定済みワールドに不一致があります。手動確認が必要です。");
      }
      if (end < run.transaction.size()) {
        later(() -> verifyCommitted(run, end));
        return;
      }
      async(
          () -> {
            journal.save(run.job, "committed");
            return true;
          },
          ignored -> {
            release(run);
            current = null;
            busy = false;
            pending.remove(run.job.actor());
            busy = !recovery.isEmpty();
            run.result.complete(null);
            tell(run.player, "確定済み建築を照合し、復旧が完了しました。");
          },
          error -> recoveryRequired(run.job, run.player, run.result, error));
    } catch (Exception error) {
      recoveryRequired(run.job, run.player, run.result, error);
    }
  }

  private void restore(Run run, int end, String backendStatus) {
    try {
      int start = end;
      long deadline = System.nanoTime() + nanos;
      for (int n = 0; start > 0 && n < perTick && (n == 0 || System.nanoTime() < deadline); n++) {
        run.transaction.restore(start - 1, start);
        start--;
      }
      if (start > 0) {
        int next = start;
        later(() -> restore(run, next, backendStatus));
        return;
      }
      async(
          () -> {
            if (!Set.of("planned", "rolled_back").contains(backendStatus)) {
              JsonObject f = new JsonObject();
              f.addProperty("job", run.job.id());
              f.addProperty("outcome", "rollback");
              backend.call("finish", run.job.actor(), f);
            }
            journal.save(run.job, "rolled_back");
            return true;
          },
          ignored -> {
            release(run);
            current = null;
            busy = false;
            pending.remove(run.job.actor());
            busy = !recovery.isEmpty();
            run.result.completeExceptionally(new IllegalStateException("建築を取り消し、元のワールドを復元しました。"));
            tell(run.player, "元のワールドを復元しました。Python都市は変更していません。");
          },
          error -> recoveryRequired(run.job, run.player, run.result, error));
    } catch (Exception error) {
      recoveryRequired(run.job, run.player, run.result, error);
    }
  }

  private void recoveryRequired(
      GameJob job, Player player, CompletableFuture<Void> result, Throwable error) {
    if (current != null) {
      release(current);
      current = null;
    }
    busy = true;
    List<Journal.Backup> unresolved =
        new ArrayList<>(recovery.stream().filter(b -> !b.job().id().equals(job.id())).toList());
    unresolved.addFirst(new Journal.Backup(job.info(), job.changes(), "pending"));
    recovery = List.copyOf(unresolved);
    result.completeExceptionally(error);
    tell(
        player,
        "復旧待ち: " + error.getMessage() + "。変更箇所を確認し /city recover を実行してください。バックアップは保持しています。");
  }

  @Override
  public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
    try {
      CommandInput input = CommandInput.parse(args);
      if (!(sender instanceof Player player)) {
        tell(sender, "ゲーム内のプレイヤーとして使用してください。プラグイン起動済み / backend設定=" + (backend != null));
        return true;
      }
      if (input.action().equals("identity")) {
        tell(
            player,
            "プレイヤーUUID="
                + player.getUniqueId()
                + " / サーバーUUID="
                + getConfig().getString("server-id"));
        return true;
      }
      switch (input.action()) {
        case "plan" -> planFor(player, input.argument());
        case "review" -> adopt(player, input.argument(), false);
        case "preview" -> {
          GameJob job = pending.get(player.getUniqueId());
          if (job == null) throw new IllegalArgumentException("計画がありません。");
          preview(player, job);
        }
        case "approve" -> approveFor(player);
        case "undo" -> adopt(player, "", true);
        case "cancel" -> {
          if (current != null && current.job.actor().equals(player.getUniqueId())) {
            if (current.committing) tell(player, "保存確定中です。完了後に /city undo を指定してください。");
            else {
              current.cancelling = true;
              tell(player, "停止後にバックアップから復元します。");
            }
          } else if (busy) {
            cancellations.add(player.getUniqueId());
            tell(player, "処理完了前にキャンセルを要求しました。");
          } else {
            GameJob job = pending.remove(player.getUniqueId());
            if (job != null) {
              JsonObject req = new JsonObject();
              req.addProperty("job", job.id());
              async(
                  () -> backend.call("cancel", job.actor(), req),
                  ignored -> tell(player, "計画を見送りました。"),
                  error -> tell(player, error.getMessage()));
            } else tell(player, "計画がありません。");
          }
        }
        case "pos1", "pos2" -> {
          binding(player);
          Location l = player.getLocation();
          (input.action().equals("pos1") ? first : second)
              .put(player.getUniqueId(), origin().local(l.getX(), l.getY(), l.getZ()));
          tell(player, input.action() + "を選択しました。");
        }
        case "clear" -> {
          first.remove(player.getUniqueId());
          second.remove(player.getUniqueId());
          pending.remove(player.getUniqueId());
          tell(player, "選択を解除しました。");
        }
        case "anchor" -> anchor(player, input.argument());
        case "recover" -> {
          permitted(player, "city.admin");
          if (recovery.isEmpty())
            throw new IllegalArgumentException("起動時の未完了バックアップがありません。実行中なら /city cancel を使用してください。");
          Journal.Backup backup = recovery.getFirst();
          recovery = List.copyOf(recovery.subList(1, recovery.size()));
          recoverJob(backup.job(), player, new CompletableFuture<>());
        }
        case "status" -> {
          tell(
              player,
              "稼働中="
                  + busy
                  + " / 未完了バックアップ="
                  + recovery.size()
                  + " / 現在位置="
                  + player.getLocation()
                  + " / 向き yaw="
                  + player.getLocation().getYaw());
          if (backend != null)
            async(
                () -> backend.call("state", player.getUniqueId(), null),
                state ->
                    tell(
                        player,
                        "都市="
                            + state.get("city_name").getAsString()
                            + " / revision="
                            + state.get("revision").getAsString()
                            + " / Paper="
                            + state.get("paper")),
                error -> tell(player, error.getMessage()));
        }
        default ->
            tell(
                player,
                "/city"
                    + " plan|preview|approve|cancel|undo|status|anchor|pos1|pos2|clear|review|recover|identity");
      }
    } catch (Exception error) {
      tell(sender, error.getMessage());
    }
    return true;
  }

  private void anchor(Player player, String argument) {
    permitted(player, "city.admin");
    ready();
    String[] parts = argument.split(" +");
    if (parts.length != 2)
      throw new IllegalArgumentException("/city anchor <都市X> <都市Z>：足元を都市の地面Y=3に合わせてください。");
    int x = Integer.parseInt(parts[0]), z = Integer.parseInt(parts[1]);
    Coordinates proposed =
        Coordinates.atPlayer(
            player.getLocation().getX(),
            player.getLocation().getY(),
            player.getLocation().getZ(),
            x,
            z);
    World world = player.getWorld();
    JsonObject b = new JsonObject();
    b.addProperty("server_id", getConfig().getString("server-id"));
    b.addProperty("world_uuid", world.getUID().toString());
    b.addProperty("world_name", world.getName());
    JsonArray o = new JsonArray();
    o.add(proposed.x());
    o.add(proposed.y());
    o.add(proposed.z());
    b.add("origin", o);
    b.addProperty("rotation", 0);
    async(
        () -> backend.call("state", player.getUniqueId(), null),
        state -> {
          JsonElement bound = state.getAsJsonObject("paper").get("binding");
          if (!bound.isJsonNull() && !bound.equals(b)) {
            tell(player, "バックエンドに別のanchorが登録済みです。変更は保存しません。");
            return;
          }
          if (x < 0
              || z < 0
              || x >= state.get("size").getAsInt()
              || z >= state.get("size").getAsInt()) {
            tell(player, "都市座標が範囲外です。");
            return;
          }
          getConfig().set("anchor.configured", true);
          getConfig().set("anchor.world-name", world.getName());
          getConfig().set("anchor.world-uuid", world.getUID().toString());
          getConfig().set("anchor.x", proposed.x());
          getConfig().set("anchor.y", proposed.y());
          getConfig().set("anchor.z", proposed.z());
          saveConfig();
          tell(player, "anchorを保存しました: " + o);
        },
        error -> tell(player, error.getMessage()));
  }

  private boolean protectedByJob(Block block) {
    return current != null
        && current.world.equals(block.getWorld())
        && current.bounds().contains(block.getX() + .5, block.getY() + .5, block.getZ() + .5);
  }

  @EventHandler(ignoreCancelled = true)
  public void breakBlock(BlockBreakEvent event) {
    if (protectedByJob(event.getBlock())) event.setCancelled(true);
  }

  @EventHandler(ignoreCancelled = true)
  public void placeBlock(BlockPlaceEvent event) {
    if (protectedByJob(event.getBlock())) event.setCancelled(true);
  }

  @EventHandler(ignoreCancelled = true)
  public void fluid(BlockFromToEvent event) {
    if (protectedByJob(event.getBlock()) || protectedByJob(event.getToBlock()))
      event.setCancelled(true);
  }

  @EventHandler(ignoreCancelled = true)
  public void physics(BlockPhysicsEvent event) {
    if (protectedByJob(event.getBlock())) event.setCancelled(true);
  }

  @EventHandler(ignoreCancelled = true)
  public void piston(BlockPistonExtendEvent event) {
    if (protectedByJob(event.getBlock())
        || event.getBlocks().stream()
            .anyMatch(
                b -> protectedByJob(b) || protectedByJob(b.getRelative(event.getDirection()))))
      event.setCancelled(true);
  }

  @EventHandler(ignoreCancelled = true)
  public void pistonRetract(BlockPistonRetractEvent event) {
    if (protectedByJob(event.getBlock())
        || event.getBlocks().stream()
            .anyMatch(
                b -> protectedByJob(b) || protectedByJob(b.getRelative(event.getDirection()))))
      event.setCancelled(true);
  }

  @EventHandler(ignoreCancelled = true)
  public void blockExplode(BlockExplodeEvent event) {
    event.blockList().removeIf(this::protectedByJob);
  }

  @EventHandler(ignoreCancelled = true)
  public void explode(EntityExplodeEvent event) {
    event.blockList().removeIf(this::protectedByJob);
  }
}
