package jp.aicity;

import com.google.gson.*;
import java.util.*;

public record GameJob(JsonObject info, List<BlockChange> changes) {
  public String id() {
    return info.get("id").getAsString();
  }

  public String planId() {
    return info.get("plan_id").getAsString();
  }

  public String revision() {
    return info.get("before_revision").getAsString();
  }

  public UUID actor() {
    return UUID.fromString(info.get("actor").getAsString());
  }

  public JsonObject binding() {
    return info.getAsJsonObject("binding");
  }

  public Coordinates origin() {
    JsonArray a = binding().getAsJsonArray("origin");
    return new Coordinates(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt());
  }

  public UUID worldId() {
    return UUID.fromString(binding().get("world_uuid").getAsString());
  }
}
