# Phase 3A 実行済み編集例

`convenience/` は、`../managed-city/project.json` の中央区へ、seed=42・APIなしの日本語ルールでコンビニを追加した記録です。元都市は東京60%・京都40%の実OSM統計から生成した架空の都市です。

| ファイル | 内容 |
| --- | --- |
| [比較画像](convenience/review/comparison.png) | 左が変更前、右が変更後。ピンク枠が追加する建物ID 51 |
| [計画](convenience/review/plan.json) | 「中央区の空き地にコンビニを作って」の解釈、配置、差分、検証 |
| [編集後project](convenience/applied/project.json) | 52棟の新状態。元の51棟と道路・区・地区を保持 |
| [編集後schematic](convenience/applied/city.schem) | 256×32×256、48パレット、28,392バイト |
| [履歴](convenience/applied/history.json) | 明示承認した計画IDと変更前後のダイジェスト |
| [Undo後schematic](convenience/undone/city.schem) | 元の51棟へ復元。元ファイルとバイト一致、28,289バイト |
| [検証記録](convenience/verification.json) | 保存済み計画の再計算、建物数、全棟接道、重複0、復元一致、SHA256 |

操作は [編集ガイド](../../docs/EDITING.md) を参照し、新しい出力先で実行してください。保存済みの例を上書きする必要はありません。画像は2Dプレビューであり、ゲーム内のスクリーンショットではありません。

© OpenStreetMap contributors — https://www.openstreetmap.org/copyright 。元の出典とライセンスは [DATA_LICENSE.md](../../DATA_LICENSE.md) を参照してください。
