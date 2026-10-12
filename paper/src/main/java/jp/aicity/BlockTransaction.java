package jp.aicity;

import java.util.List;

/** Main-thread-only world operations; disk and network are deliberately outside. */
public final class BlockTransaction {
  public interface Access {
    String get(BlockChange block);

    void set(BlockChange block, String state);
  }

  private final List<BlockChange> changes;
  private final Access world;
  private int attempted;

  public BlockTransaction(List<BlockChange> changes, Access world, int limit) {
    if (changes.isEmpty() || changes.size() > limit)
      throw new IllegalArgumentException("変更数が安全上限を超えています。");
    this.changes = List.copyOf(changes);
    this.world = world;
  }

  public void preflight(int start, int end) {
    for (int i = start; i < end; i++) {
      BlockChange block = changes.get(i);
      if (!world.get(block).equals(block.before()))
        throw new IllegalStateException("ワールド衝突: " + block.x() + "," + block.y() + "," + block.z());
    }
  }

  public int apply(int start, int end) {
    for (int i = start; i < end; i++) {
      BlockChange block = changes.get(i);
      attempted = i + 1; // Include a partially failed setter in rollback.
      if (!world.get(block).equals(block.before()))
        throw new IllegalStateException(
            "建築中のワールド衝突: " + block.x() + "," + block.y() + "," + block.z());
      world.set(block, block.after());
    }
    return end;
  }

  public void restore(int start, int end) {
    for (int i = end - 1; i >= start; i--) {
      BlockChange block = changes.get(i);
      String current = world.get(block);
      if (current.equals(block.before())) continue;
      if (!current.equals(block.after()))
        throw new IllegalStateException(
            "復旧時の衝突（他の変更を保護）: " + block.x() + "," + block.y() + "," + block.z());
      world.set(block, block.before());
    }
  }

  public int attempted() {
    return attempted;
  }

  public int size() {
    return changes.size();
  }
}
