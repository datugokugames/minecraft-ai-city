package jp.aicity;

import org.bukkit.block.Block;
import org.bukkit.event.*;

/** Other protection plugins may veto a write; rollback bypasses this event. */
public final class CityBlockChangeEvent extends Event implements Cancellable {
  private static final HandlerList HANDLERS = new HandlerList();
  private final String jobId;
  private final Block block;
  private boolean cancelled;

  public CityBlockChangeEvent(String jobId, Block block) {
    this.jobId = jobId;
    this.block = block;
  }

  public String getJobId() {
    return jobId;
  }

  public Block getBlock() {
    return block;
  }

  public boolean isCancelled() {
    return cancelled;
  }

  public void setCancelled(boolean value) {
    cancelled = value;
  }

  public HandlerList getHandlers() {
    return HANDLERS;
  }

  public static HandlerList getHandlerList() {
    return HANDLERS;
  }
}
