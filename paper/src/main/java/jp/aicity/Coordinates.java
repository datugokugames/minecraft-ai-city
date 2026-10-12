package jp.aicity;

/** Translation-only mapping. City x increases east, z increases south. */
public record Coordinates(int x, int y, int z) {
  public int[] world(int localX, int localY, int localZ) {
    return new int[] {Math.addExact(x, localX), Math.addExact(y, localY), Math.addExact(z, localZ)};
  }

  public int[] local(double worldX, double worldY, double worldZ) {
    return new int[] {
      (int) Math.floor(worldX) - x, (int) Math.floor(worldY) - y, (int) Math.floor(worldZ) - z
    };
  }

  public static Coordinates atPlayer(
      double worldX, double worldY, double worldZ, int cityX, int cityZ) {
    return new Coordinates(
        (int) Math.floor(worldX) - cityX,
        (int) Math.floor(worldY) - 4,
        (int) Math.floor(worldZ) - cityZ);
  }
}
