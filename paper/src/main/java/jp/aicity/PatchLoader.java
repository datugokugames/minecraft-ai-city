package jp.aicity;

import com.google.gson.*;
import com.sk89q.worldedit.extent.clipboard.Clipboard;
import com.sk89q.worldedit.extent.clipboard.io.*;
import com.sk89q.worldedit.math.BlockVector3;
import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.GZIPInputStream;

public final class PatchLoader {
  /** Local compatibility probe used by the disposable integration harness, not an HTTP endpoint. */
  public static int[] schematicDimensions(Path file) throws IOException {
    if (Files.size(file) > 5 * 1024 * 1024) throw new IOException("Schematic exceeds file limit");
    try (InputStream gzip = new GZIPInputStream(Files.newInputStream(file))) {
      if (gzip.readNBytes(8 * 1024 * 1024 + 1).length > 8 * 1024 * 1024)
        throw new IOException("Schematic expansion exceeds limit");
    }
    ClipboardFormat format = ClipboardFormats.findByFile(file.toFile());
    if (format == null) throw new IOException("Unknown schematic format");
    try (ClipboardReader reader = format.getReader(Files.newInputStream(file))) {
      var size = reader.read().getDimensions();
      return new int[] {size.x(), size.y(), size.z()};
    }
  }

  public static GameJob load(
      BackendClient client, UUID actor, JsonObject info, Path cache, int maxBlocks, int maxVolume)
      throws Exception {
    String id = info.get("id").getAsString();
    if (!id.matches("[0-9a-f]{32}")
        || info.get("protocol").getAsInt() != 1
        || !info.get("actor").getAsString().equals(actor.toString()))
      throw new IOException("Invalid job manifest");
    int count = info.get("changed_blocks").getAsInt();
    if (count < 1 || count > maxBlocks) throw new IOException("変更ブロック数がプラグイン上限を超えています。");
    JsonArray bounds = info.getAsJsonArray("bounds");
    int x0 = bounds.get(0).getAsInt(), y0 = bounds.get(1).getAsInt(), z0 = bounds.get(2).getAsInt();
    int w = bounds.get(3).getAsInt() - x0,
        h = bounds.get(4).getAsInt() - y0,
        d = bounds.get(5).getAsInt() - z0;
    if (w < 1 || h < 1 || d < 1 || (long) w * h * d > maxVolume)
      throw new IOException("差分領域が安全上限を超えています。");
    Path folder = cache.resolve(id);
    Files.createDirectories(folder);
    Clipboard[] clips = new Clipboard[2];
    for (int index = 0; index < 2; index++) {
      String label = index == 0 ? "before" : "after";
      byte[] bytes = client.bytes("jobs/" + id + "/" + label + ".schem", actor, null);
      String sha = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
      if (!sha.equals(info.getAsJsonObject("schematic_sha256").get(label).getAsString()))
        throw new IOException("schematic SHA256 mismatch");
      try (InputStream compressed = new GZIPInputStream(new ByteArrayInputStream(bytes))) {
        if (compressed.readNBytes(8 * 1024 * 1024 + 1).length > 8 * 1024 * 1024)
          throw new IOException("schematic expansion exceeds 8MiB");
      }
      Path file = folder.resolve(label + ".schem");
      Files.write(file, bytes);
      ClipboardFormat format = ClipboardFormats.findByFile(file.toFile());
      if (format == null) throw new IOException("WorldEdit cannot identify Sponge schematic");
      try (ClipboardReader reader = format.getReader(Files.newInputStream(file))) {
        clips[index] = reader.read();
      }
      if (!clips[index].getDimensions().equals(BlockVector3.at(w, h, d)))
        throw new IOException("schematic dimensions mismatch");
    }
    List<BlockChange> changes = new ArrayList<>();
    for (int y = 0; y < h; y++)
      for (int z = 0; z < d; z++)
        for (int x = 0; x < w; x++) {
          var first = clips[0].getFullBlock(clips[0].getMinimumPoint().add(x, y, z));
          var second = clips[1].getFullBlock(clips[1].getMinimumPoint().add(x, y, z));
          if (first.hasNbtData() || second.hasNbtData())
            throw new IOException("Block entities are not supported");
          if (!first.toImmutableState().equals(second.toImmutableState()))
            changes.add(
                new BlockChange(
                    x0 + x,
                    y0 + y,
                    z0 + z,
                    first.toImmutableState().toString(),
                    second.toImmutableState().toString()));
        }
    if (changes.size() != count)
      throw new IOException("WorldEdit block diff differs from Python manifest");
    return new GameJob(info.deepCopy(), List.copyOf(changes));
  }
}
