# 実行検証記録

## v0.2.1 統合指示後の再開

2026-10-11、Python 3.12.14。前版を保持して管理機能を追加した。

| 今回実行した検証 | 結果 |
| --- | --- |
| 変更前の `pytest -q` | 122 passed、20.56秒 |
| 追加管理機能のテスト | 50 passed、legacy/organic両形式 |
| 変更後の全 `pytest -q` | **172 passed、25.17秒** |
| `ruff format --check src tests` | 26 files already formatted |
| `ruff check src tests` | All checks passed |
| `python -m pip check` | No broken requirements found |
| OSM実応答キャッシュから東京・京都を `--no-fallback` で再分析 | source=osm、保存プロファイルと同一 |
| 6シードを再生成 | 全件成功、保存済み比較JSONと同一 |
| 管理設定前後の `.schem` / PNG / City JSON | すべてバイト一致 |
| プロジェクト出力のNBTをnbtlibで読み込み | v2、DataVersion 3465、256×32×256、45パレット |
| v0.2.1 wheelのビルド・別ディレクトリへのインストール | 成功。既存の検証済み依存を使用し、別インストール先からのimportを確認 |
| インストールしたwheelによる実プロファイル生成・管理プロジェクト出力・v0.1保存都市の再建築 | 3件とも保存済みschematicとバイト一致 |
| 前版ZIP内の53件の既存JSON/PNG/schematicとの比較 | 全件バイト一致、既存成果物を保持 |

wheel: `dist/minecraft_ai_city-0.2.1-py3-none-any.whl`、59,504バイト、SHA256 `440e8aafaa95a89de7d4d9a085f146c4f5d3f2fef862b1166bb0274d98893d87`。
ビルド依存setuptoolsは既存venvに直接入っていなかったため、通常のpipビルド分離で取得し、ビルドとeditable再インストールを完了した。実行時依存の変更はない。

管理機能テストは、日本語名、空の区/地区、所属変更、希望条件の保持/解除、JSON往復、存在しない参照、重複ID、欠損/二重敷地、形状変更、都市ダイジェスト不一致、余分な削除命令の拒否、失敗時に元データが保持されること、上書き拒否、既存エンジンとの出力一致を検証した。

現在の成果物:

- `examples/managed-city/`: seed=2026、OSM東京60%・京都40%、51棟、2区、6開発済み地区+1未開発地区、54占有敷地。全51棟の接道、道路1連結成分、重複0。
- `.schem`: 28,289バイト、313,809非空気ブロック。SHA256 `bc29a5a6fe40e94934b1dee758e7c767686bfc274c07d2ef58422c0d1fde659f`。読み戻したBlockDataは2,097,152エントリー（全IDが128未満なので各1バイト）。
- `examples/phase2-comparison/`: seed=42/2026/7/19/81/123、45〜52棟。全件で接道100%、未分類終端0、品質警告0。
- `examples/management/export-performance.json`: Linuxクラウドで管理JSON読込・検証・建築・PNG・schematic出力を計測。1.349秒、子プロセス最大RSS 112,008KiB（約109MiB）。Windows・HDD・Paperでの計測ではない。

実機Minecraft/WorldEdit、実AI API、GitHub上のCI、Windows/Paperの負荷は未検証。区単位の物理的な拡張・再開発、自然言語編集・承認・undo・Web地図・Paper/Bedrock連携は未実装。

## v0.2 自然な道路網への拡張

- 中断後の再開確認（2026-10-11、Python 3.12.14）: `pytest -q` は **122 passed**、`ruff format --check src tests` は24ファイル整形済み、`ruff check src tests` と `pip check` も成功。
- 東京・京都の両プロファイルを同梱の拡張OSM応答から再分析し、保存済みモデルと完全一致。6シード比較も一時ディレクトリへ再生成して比較JSONが完全一致。
- 新方式のseed=2026のschematicは256×32×256、45パレット、313,809非空気ブロック、28,289バイト。SHA256: `bc29a5a6fe40e94934b1dee758e7c767686bfc274c07d2ef58422c0d1fde659f`。再生成結果とバイト一致。
- `.gitignore` に仮想環境、ビルド出力、各種キャッシュ、`.env.*`、鍵・認証情報を追加。配布対象のテキストを既知のトークン・秘密鍵パターンで確認し、該当なし（網羅的な秘密検出の保証ではない）。
- v0.2.0 wheelをビルドし、別ディレクトリへインストールして実データの都市生成を実行。ソース版と同じ51棟の都市を出力し、schematicのバイト列も一致。
- 作業ディレクトリはGitリポジトリではなく、GitHub接続先も未確認。新規リポジトリ作成・コミット・pushは行わず、既存成果物を保持。
- 既存98ケースに、交差点形状・道路曲率・幅・河川/鉄道/標高タグ、新方式の12シード/サイズ組合せ、回転建物の扉、NBT読み戻し、鉄道下の通行空間、接道の破損検出、格子偏重検出、比較出力を追加。
- 東京・京都の拡張Overpassクエリを実取得。幅の推定と観測率、ele標高タグのサンプル数を保存。
- 実OSM特徴60:40、256×256、seed=42/2026/7/19/81/123の6件で45〜52棟、道路1連結、接道率100%、未分類終端0、品質警告0。全件の画像と数値は `examples/natural-comparison/`。
- seed=2026を `examples/natural-city/city.schem` として出力。
- 旧保存済み都市 `examples/japan-city/city.json` を現行コードで再建築し、以前の `.schem` とバイト一致。
- Minecraft/WorldEdit本体での貼り付けと実AI API通信は引き続き未検証。

## v0.1 初回MVPの記録

検証日: 2026-10-11 / Linux / Python 3.12.14。
使用した依存の固定バージョンは `requirements-lock.txt` を参照。

| 検証 | 結果 |
| --- | --- |
| 仮想環境から `pip install -e ".[dev]"` | 成功 |
| wheelビルド・別ディレクトリへのインストール・実行 | 成功、61棟のオフラインデモを再生成 |
| `pytest -q` | 98 passed、警告なし |
| `ruff check src tests` | All checks passed |
| `pip check` | No broken requirements found |
| 東京・京都のOverpass実取得（各1回、no-fallback） | ともに source=osm |
| 同梱の元応答から東京プロファイル再分析 | JSONモデルが保存済み結果と完全一致 |
| 合成サンプルのオフライン生成、seed=42 | 256×256、61棟、16地区 |
| 実OSM特徴60:40の生成、seed=2026 | 256×256、33棟、36地区 |
| 上記実データ生成例の検査 | 道路1連結成分、建物重複0、5建物種別 |
| 上記実データschematic | Sponge v2、256×32×256、42パレット、339,633非空気ブロック |
| NBT出力の読み戻し | 全ブロック配列、VarInt、軸順序、寸法の一致をテスト |
| CLIの保存済み計画・都市による再生成 | `.schem` のバイト列一致をテスト |
| 不正AI JSON、拒否、タイムアウト、キーなし | モックでフォールバックを検証 |
| PNGプレビュー | 読み込み検証・目視確認済み |

Minecraft/WorldEdit本体による貼り付け、実APIキーを使うOpenAI呼び出し、
GitHub Actions上の実行は未実施です。API連携コード・CI設定は同梱しています。

未実装の機能はREADMEの「MVPの制限・未実装」を参照してください。
