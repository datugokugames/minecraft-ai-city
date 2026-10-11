# Phase 3B ブラウザ実行例

実サーバー + Playwright Chromium、iPhone 13相当390×664 CSSピクセルとPC画面で検証しました。実iPhoneのスクリーンショットではありません。

- [モバイル地図](mobile-map.png)
- [日本語指示・承認前の比較](mobile-review.png)
- [PC画面](desktop-map.png)
- [ブラウザからダウンロードしたproject](edited-project.json)
- [ブラウザからダウンロードしたschematic](edited-city.schem)
- [検証JSON](verification.json)

既存の51棟都市で「中央区の空き地にコンビニを作って」→プレビュー→承認→52棟→ダウンロード→Undo→51棟を実行しました。Undo後のschematicは元とバイト一致し、元ファイルは変更していません。

建物/地区の選択、位置/矩形、拡大縮小・2点タッチのピンチ、未承認時の保持も検証。再実行は `scripts/check_web_browser.py` と [起動ガイド](../../docs/WEB_UI.md) を参照してください。

© OpenStreetMap contributors — https://www.openstreetmap.org/copyright 。[DATA_LICENSE.md](../../DATA_LICENSE.md) 参照。
