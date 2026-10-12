# Phase 4実行例

`examples/managed-city/project.json` に「中央区の空き地にコンビニを作って」、seed=42で計画し、Paper 1.21.4 build232 / WorldEdit 7.3.10 / Java21上で432ブロックの差分を適用しました。

- `comparison.png` / `before.png` / `after.png`：同じ計画の2Dプレビュー。
- `plan.json` / `diff.json` / `validation.json`：建物追加案と検証。既存51棟→52棟、道路接続と既存建物を維持。
- `before.schem` / `after.schem`：都市bounds `[163,3,96,177,11,117]` の切り出し。14×8×21、変更432ブロック。**差分用の資料で、全都市として貼り付けないでください。**
- `edited-city.schem`：承認後の256×32×256全都市。Sponge v2、28,392 bytes。
- `server-verification.json`：実サーバーの建築・Undo・衝突・途中失敗復元・停止/再起動復元、使用jarのSHA256。実クライアントではなくコンソール専用テストプラグインから合成Playerで操作。
- `mobile-paper-review.png` / `mobile-paper-applied.png`：iPhone13サイズChromiumによるWeb→Paper計画受渡しと自動地図更新。ゲーム画面の画像ではありません。
- `browser-verification.json`：ブラウザ試験ではPaper完了通知を模擬。実ブロック試験は上のPaperテストで別途実行。

認証トークン、Paper設定、サーバーワールド、個人情報は同梱しません。元プロジェクトや既存の例は変更していません。再実行は `docs/PAPER.md` を参照してください。
