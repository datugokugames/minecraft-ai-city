# Phase 5検証・生成例

- `phase4-revalidation.json` / `phase4-web-revalidation.json`：今回再実行したPhase 4の実PaperとWeb整合性。旧432ブロックの建築・Undo・失敗/再起動復元に成功。
- `buildings/`：住宅、マンション、コンビニ、オフィス、駅舎の各2サイズ（合計10）。JSON仕様、モード/使用量記録、Sponge v2、上面とアイソメトリック。`buildings/comparison.png` で比較。localのみ、APIなし。
- `city-edit/`：実Paper試験で使用した「ここに30×40のマンションを建てて」の共通計画、前後2D地図、外装PNG、差分、承認後project/schematic。マンション敷地は都市の `(44,6)` から40×30（道路側幅30が東西回転し、ワールドのX寸法40）。JSON内の値を正としてください。
- `paper-architecture-verification.json`：実Paper1.21.4 / WorldEdit7.3.10 / Java21、4,410変更ブロック、ワールドUndo、衝突、保護イベントによる途中失敗復元、停止・再起動復元。上限12,000で実行し、実変更数は既定5,000以下。
- `mobile-exterior-review.png` / `browser-architecture-verification.json`：ChromiumのiPhone13サイズで30×40の位置選択・外装表示・承認・ダウンロード・Undo。実iPhone画像ではありません。

試験は合成プレイヤーと使い捨てワールドを使用。ゲーム内の実クライアントやオンラインログインは検証していません。秘密トークン・サーバー設定・ワールド・プレイヤー情報を保存していません。モデルを使ったサンプルではなく、シード付きローカル手続き設計の成果です。

再生成は `python scripts/generate_architecture_samples.py --output output/new-samples`。外装の範囲と制限は [ARCHITECTURE.md](../../docs/ARCHITECTURE.md)、Paperの安全・復旧は [PAPER.md](../../docs/PAPER.md) を参照してください。
