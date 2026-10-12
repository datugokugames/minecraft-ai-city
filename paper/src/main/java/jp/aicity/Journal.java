package jp.aicity;

import com.google.gson.*;
import java.io.*;
import java.nio.*;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Durable write-ahead affected-block backup. Never runs on the tick thread. */
public final class Journal {
  private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

  public record Backup(JsonObject info, List<BlockChange> changes, String status) {
    public GameJob job() {
      return new GameJob(info, changes);
    }
  }

  private final Path root;

  public Journal(Path root) throws IOException {
    this.root = root;
    Files.createDirectories(root);
  }

  private Path file(String id, String suffix) {
    if (!id.matches("[0-9a-f]{32}")) throw new IllegalArgumentException("Invalid backup ID");
    return root.resolve(id + suffix);
  }

  public void save(GameJob job, String status) throws IOException {
    Path target = file(job.id(), ".json"), temp = file(job.id(), ".tmp");
    byte[] bytes =
        GSON.toJson(new Backup(job.info(), job.changes(), status)).getBytes(StandardCharsets.UTF_8);
    try (FileChannel channel =
        FileChannel.open(
            temp,
            StandardOpenOption.CREATE,
            StandardOpenOption.TRUNCATE_EXISTING,
            StandardOpenOption.WRITE)) {
      ByteBuffer buffer = ByteBuffer.wrap(bytes);
      while (buffer.hasRemaining()) channel.write(buffer);
      channel.force(true);
    }
    Files.move(temp, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
  }

  public void beforeBatch(String id, int end) throws IOException {
    try (FileChannel channel =
        FileChannel.open(
            file(id, ".wal"),
            StandardOpenOption.CREATE,
            StandardOpenOption.APPEND,
            StandardOpenOption.WRITE)) {
      ByteBuffer buffer = ByteBuffer.wrap((end + "\n").getBytes(StandardCharsets.US_ASCII));
      while (buffer.hasRemaining()) channel.write(buffer);
      channel.force(true);
    }
  }

  public int attempted(GameJob job) throws IOException {
    Path wal = file(job.id(), ".wal");
    int previous = 0;
    if (Files.exists(wal))
      for (String line : Files.readAllLines(wal)) {
        int end = Integer.parseInt(line);
        if (end < previous || end > job.changes().size())
          throw new IOException("Invalid recovery journal");
        previous = end;
      }
    return previous;
  }

  public List<Backup> unfinished() throws IOException {
    List<Backup> found = new ArrayList<>();
    try (var paths = Files.list(root)) {
      for (Path path :
          paths.filter(p -> p.getFileName().toString().matches("[0-9a-f]{32}\\.json")).toList()) {
        Backup b = GSON.fromJson(Files.readString(path), Backup.class);
        if (!Set.of("committed", "rolled_back").contains(b.status())) found.add(b);
      }
    }
    return found;
  }
}
