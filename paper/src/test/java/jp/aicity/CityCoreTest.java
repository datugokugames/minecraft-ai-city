package jp.aicity;

import static org.junit.jupiter.api.Assertions.*;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;

class CityCoreTest {
  @Test
  void japaneseCommand() {
    assertEquals(
        "ここにコンビニを作って", CommandInput.parse(new String[] {"plan", "ここにコンビニを作って"}).argument());
  }

  @Test
  void japaneseSpaces() {
    assertEquals(
        "この地区に 住宅を5棟追加して",
        CommandInput.parse(new String[] {"PLAN", "この地区に", "住宅を5棟追加して"}).argument());
  }

  @Test
  void commandEmptyAndLimits() {
    assertEquals("status", CommandInput.parse(new String[] {}).action());
    assertThrows(IllegalArgumentException.class, () -> CommandInput.parse(new String[] {"plan"}));
    assertThrows(
        IllegalArgumentException.class,
        () -> CommandInput.parse(new String[] {"plan", "x".repeat(2001)}));
  }

  @Test
  void commandControls() {
    assertThrows(
        IllegalArgumentException.class, () -> CommandInput.parse(new String[] {"plan", "a\nb"}));
  }

  @Test
  void coordinatesNegativeFloor() {
    Coordinates c = Coordinates.atPlayer(-.2, 84, 100.9, 10, 20);
    assertEquals(new Coordinates(-11, 80, 80), c);
    assertArrayEquals(new int[] {10, 4, 20}, c.local(-.2, 84, 100.9));
    assertArrayEquals(new int[] {-1, 84, 100}, c.world(10, 4, 20));
  }

  @Test
  void coordinatesRoundTrip() {
    Coordinates c = new Coordinates(128, -60, -256);
    for (int i = -256; i < 256; i++) {
      int[] p = c.world(i, 3, i + 10);
      assertArrayEquals(new int[] {i, 3, i + 10}, c.local(p[0] + .8, p[1] + .3, p[2] + .9));
    }
  }

  @Test
  void coordinatesOverflow() {
    assertThrows(
        ArithmeticException.class, () -> new Coordinates(Integer.MAX_VALUE, 0, 0).world(1, 0, 0));
  }

  static final class Fake implements BlockTransaction.Access {
    final Map<Integer, String> blocks = new HashMap<>();
    int calls, failAfter = -1;

    public String get(BlockChange c) {
      return blocks.getOrDefault(c.x(), "air");
    }

    public void set(BlockChange c, String state) {
      if (state.equals("stone") && calls++ == failAfter)
        throw new IllegalStateException("injected");
      blocks.put(c.x(), state);
    }
  }

  private List<BlockChange> blocks() {
    return List.of(
        new BlockChange(0, 4, 0, "air", "stone"),
        new BlockChange(1, 4, 0, "air", "stone"),
        new BlockChange(2, 4, 0, "air", "stone"));
  }

  @Test
  void applyAndUndoExact() {
    Fake world = new Fake();
    BlockTransaction t = new BlockTransaction(blocks(), world, 3);
    t.preflight(0, 3);
    t.apply(0, 1);
    assertEquals("stone", world.blocks.get(0));
    assertFalse(world.blocks.containsKey(1));
    t.apply(1, 3);
    t.restore(0, 3);
    assertTrue(world.blocks.values().stream().allMatch("air"::equals));
  }

  @Test
  void collisionBeforeWrites() {
    Fake world = new Fake();
    world.blocks.put(1, "chest");
    BlockTransaction t = new BlockTransaction(blocks(), world, 3);
    assertThrows(IllegalStateException.class, () -> t.preflight(0, 3));
    assertEquals(1, world.blocks.size());
  }

  @Test
  void failureRestoresAttemptedOnly() {
    Fake world = new Fake();
    world.failAfter = 1;
    BlockTransaction t = new BlockTransaction(blocks(), world, 3);
    assertThrows(IllegalStateException.class, () -> t.apply(0, 3));
    assertEquals(2, t.attempted());
    t.restore(0, t.attempted());
    assertEquals("air", world.blocks.get(0));
    assertFalse(world.blocks.containsKey(2));
  }

  @Test
  void protectNewPlayerChangesDuringRollback() {
    Fake world = new Fake();
    BlockTransaction t = new BlockTransaction(blocks(), world, 3);
    t.apply(0, 3);
    world.blocks.put(1, "gold");
    assertThrows(IllegalStateException.class, () -> t.restore(0, 3));
    assertEquals("gold", world.blocks.get(1));
  }

  @Test
  void changeLimit() {
    assertThrows(
        IllegalArgumentException.class, () -> new BlockTransaction(blocks(), new Fake(), 2));
    assertThrows(
        IllegalArgumentException.class, () -> new BlockTransaction(List.of(), new Fake(), 2));
  }

  @Test
  void localBackendOnly() {
    assertThrows(
        IllegalArgumentException.class,
        () -> new BackendClient("https://example.com", "x".repeat(32)));
    assertThrows(
        IllegalArgumentException.class,
        () -> new BackendClient("http://127.0.0.1/a", "x".repeat(32)));
    assertThrows(
        IllegalArgumentException.class, () -> new BackendClient("http://127.0.0.1:8765", ""));
    assertDoesNotThrow(() -> new BackendClient("http://localhost:8765", "x".repeat(32)));
  }

  @Test
  void durableBackupAndWriteAhead(@TempDir Path root) throws Exception {
    Journal j = new Journal(root);
    JsonObject info = new JsonObject();
    info.addProperty("id", "a".repeat(32));
    GameJob job = new GameJob(info, blocks());
    j.save(job, "reserved");
    assertEquals(1, j.unfinished().size());
    j.beforeBatch(job.id(), 1);
    j.beforeBatch(job.id(), 3);
    assertEquals(3, j.attempted(job));
    j.save(job, "committed");
    assertTrue(j.unfinished().isEmpty());
  }

  @Test
  void invalidJournalNeverExecutes(@TempDir Path root) throws Exception {
    Journal j = new Journal(root);
    JsonObject info = new JsonObject();
    info.addProperty("id", "b".repeat(32));
    GameJob job = new GameJob(info, blocks());
    j.save(job, "building");
    Files.writeString(root.resolve(job.id() + ".wal"), "999\n");
    assertThrows(java.io.IOException.class, () -> j.attempted(job));
  }
}
