package jp.aicity;

import java.util.Arrays;
import java.util.Locale;

public record CommandInput(String action, String argument) {
  public static CommandInput parse(String[] args) {
    String action = args.length == 0 ? "status" : args[0].toLowerCase(Locale.ROOT);
    String argument =
        args.length < 2 ? "" : String.join(" ", Arrays.copyOfRange(args, 1, args.length));
    if (argument.length() > 2000 || argument.chars().anyMatch(Character::isISOControl))
      throw new IllegalArgumentException("指示は2000文字以内、改行なしで指定してください。");
    if (action.equals("plan") && argument.isBlank())
      throw new IllegalArgumentException("/city plan ここにコンビニを作って のように指定してください。");
    return new CommandInput(action, argument);
  }
}
