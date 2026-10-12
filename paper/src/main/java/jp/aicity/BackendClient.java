package jp.aicity;

import com.google.gson.*;
import java.io.*;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.UUID;

public final class BackendClient {
  private final URI base;
  private final String token;
  private final HttpClient http =
      HttpClient.newBuilder()
          .version(HttpClient.Version.HTTP_1_1)
          .connectTimeout(Duration.ofSeconds(5))
          .build();

  public BackendClient(String url, String token) {
    base = URI.create(url.endsWith("/") ? url : url + "/");
    if (!base.getScheme().equals("http")
        || !privateHost(base.getHost())
        || base.getRawUserInfo() != null
        || !base.getPath().equals("/")
        || base.getQuery() != null
        || base.getFragment() != null)
      throw new IllegalArgumentException("バックエンドURLはlocalhostまたは明示したプライベートIPv4のHTTPに限定されます。");
    if (token.length() < 32) throw new IllegalArgumentException("backend-tokenを設定してください。");
    this.token = token;
  }

  private static boolean privateHost(String host) {
    if (java.util.Set.of("127.0.0.1", "localhost", "[::1]").contains(host)) return true;
    if (!host.matches("[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+")) return false;
    int[] octets = java.util.Arrays.stream(host.split("\\.")).mapToInt(Integer::parseInt).toArray();
    for (int n : octets) if (n < 0 || n > 255) return false;
    return octets[0] == 10
        || (octets[0] == 192 && octets[1] == 168)
        || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31);
  }

  public byte[] bytes(String path, UUID actor, JsonObject body)
      throws IOException, InterruptedException {
    if (!path.matches("[a-zA-Z0-9/.-]+") || path.contains(".."))
      throw new IllegalArgumentException("Invalid API path");
    HttpRequest.Builder request =
        HttpRequest.newBuilder(base.resolve("api/paper/" + path))
            .timeout(Duration.ofSeconds(90))
            .header("Authorization", "Bearer " + token)
            .header("X-City-Player", actor.toString());
    if (body == null) request.GET();
    else
      request
          .header("Content-Type", "application/json")
          .POST(
              HttpRequest.BodyPublishers.ofString(
                  body.toString(), java.nio.charset.StandardCharsets.UTF_8));
    for (int attempt = 0; attempt < 6; attempt++) {
      HttpResponse<InputStream> response =
          http.send(request.build(), HttpResponse.BodyHandlers.ofInputStream());
      byte[] data;
      try (InputStream input = response.body()) {
        data = input.readNBytes(5 * 1024 * 1024 + 1);
      }
      if (data.length > 5 * 1024 * 1024) throw new IOException("API response exceeds 5MiB");
      if (response.statusCode() != 200) {
        String message = "バックエンドHTTP " + response.statusCode();
        try {
          message +=
              ": "
                  + JsonParser.parseString(
                          new String(data, java.nio.charset.StandardCharsets.UTF_8))
                      .getAsJsonObject()
                      .get("error")
                      .getAsString();
        } catch (RuntimeException ignored) {
        }
        if (response.statusCode() == 409 && message.contains("他の処理を実行中") && attempt < 5) {
          Thread.sleep(200); // IO worker only. A rejected lock acquisition made no mutation.
          continue;
        }
        throw new IOException(message);
      }
      return data;
    }
    throw new IOException("Backend busy");
  }

  public JsonObject call(String path, UUID actor, JsonObject body)
      throws IOException, InterruptedException {
    return JsonParser.parseString(
            new String(bytes(path, actor, body), java.nio.charset.StandardCharsets.UTF_8))
        .getAsJsonObject();
  }
}
