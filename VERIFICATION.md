# 実行検証記録

## v0.3.1 / Phase 3B Web地図

2026-10-11。PR #1の232テスト・Python 3.10/3.12のActions成功・変更内容を確認し、ユーザーの指示に従いmainへマージ（`e1fdb88`）。そのmainから `feature/phase3b-web-ui` を作成した。

| 検証 | 結果 |
| --- | --- |
| 全pytest | **245 passed、77.78秒**（既存232 + Web13） |
| Ruff format/check | src/tests/scriptsの38ファイル、成功 |
| pip check / JavaScript構文検査 | 成功 |
| 実HTTPサーバー | 地図・選択データ・計画・承認・出力・Undo成功 |
| セッション保護 | 未認証、外部Origin、不正Host、任意パス、過大/不正JSON、同時処理、古いrevisionを拒否 |
| 履歴/再開 | 再起動後に現在都市とUndo履歴を復元。2プロセス同時起動を拒否 |
| 保存障害 | 状態ポインター更新失敗時に以前の現在状態を保持 |
| 実Chromium / iPhone 13相当 | 地図、建物/区/地区選択、座標/矩形、ズーム/ピンチ、日本語指示→計画→承認→ダウンロード→Undo成功、JS例外0 |
| PC表示 | 1440px幅で画面取得、モバイル390px幅で横はみ出しなし |
| v0.3.1 wheel | 別インストール先からCLIのサーバー起動・HTTP取得・終了・再開成功。HTML/CSS/JSをwheelへ同梱 |

ブラウザからダウンロードしたschematicは28,392バイト、SHA256 `eb8d397e4b30fb4b6b54fbf4a013e76ed15c0a57854e0944d3d7410282106523`。Phase 3Aで同じ指示を実行した出力と一致した。51→52→51棟、承認前の保持とUndo後の元schematicとのバイト一致を確認。[画面と成果物](examples/phase3b/README.md)。

最初のHTTPテストは実行サンドボックスのソケット制限により失敗したため、ネットワークを有効にして再実行し成功。ブラウザテストではテスト用文字列evalがCSPに拒否されたため、CSPを弱めずテスト側を関数形式へ修正した。クライアント切断時も保存完了済み状態を維持する。

ローカルWebKitは共有ライブラリ不足のため未起動。GitHub Actionsでは実装コミット `f4ec22d` のPython 3.10/3.12・Chromium/WebKitの全4ジョブが成功した（[記録](examples/phase3b/ci-verification.json)）。実iPhone Safari、Windows 8GB/HDD負荷、実OpenAI、Minecraft/WorldEdit本体は未検証。LAN用HTTPを前提とし、外部公開・共同編集の権限・Paper/Bedrockは未実装。

## v0.3.0 / Phase 3A 日本語編集

2026-10-11、Python 3.12.14。mainの `b377b87` を基点に作業ブランチで実装。既存生成例と履歴を維持した。

| 検証 | 結果 |
| --- | --- |
| 既存テスト | 172件を維持 |
| 編集テスト | 60件追加。ルール/AI解析、曖昧さ・捏造選択拒否、配置、衝突、承認、差分、履歴、Undo、旧JSON互換性 |
| 全 `pytest -q` | **232 passed、53.50秒** |
| `ruff format --check src tests` | 34 files already formatted |
| `ruff check src tests` | All checks passed |
| `python -m pip check` | No broken requirements found |
| 複数編集シード | 7 / 42 / 2026で住宅5棟、駅東側マンションを追加・幾何検証 |
| 未承認・古い計画・改変計画・不完全履歴 | 拒否。元プロジェクトを保持 |
| 承認済みschematic | nbtlibで寸法・全ブロック配列を読み戻し。店舗帯看板のブロック実在も検証 |
| 外観変更 | ID・位置・敷地・所属・隣接建物を保持。実schematicを出力 |
| 公園追加 | 草地・園路・接道を検証し、実schematicを出力 |
| 連続編集/複数Undo | 逆順の履歴で元状態へ復元。異なる現在状態へのUndoを拒否 |
| v0.3.0 wheel | ビルド・別インストール先からのCLI plan/apply成功。projectとschematicが同梱例と一致 |
| コミット前の除外/秘密パターン確認 | 168ファイル、仮想環境・キャッシュ・認証ファイル混入0、既知トークン/秘密鍵パターン一致0（網羅保証ではない） |
| コンビニ実行例 | `examples/phase3a/convenience/` に計画、前後PNG、承認後project/schematic、Undo、履歴を保存 |

実行例は実OSM特徴の既存管理都市へ「中央区の空き地にコンビニを作って」、編集seed=42。51→52→51棟、道路1成分、重複0、全52棟接道。変更前の51棟・道路・鉄道・河川・区/地区名を保持。Undo後のprojectが同一、schematicも元ファイルとバイト一致した。バイト数とSHA256は [検証JSON](examples/phase3a/convenience/verification.json) を参照。

限定文法は実入力、APIはモックで検証。実OpenAI通信とMinecraft/WorldEdit本体への貼り付けは未検証。Web/Paper/Bedrock、既存登録空地の置換、削除・移動・都市外拡張・Redo・チャンク差分は未実装。

以下の過去版の未実装・未検証の記述は当時の記録。

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

## Phase 4（2026-10-12）

実装・公開物：Paper 1.21.4 build232、WorldEdit 7.3.10、Java21、Python 0.4.0。

- Java `mvn install` / fixture `mvn package`：成功。JUnit **15件**、失敗0。
- 実Paperサーバー（heap1GiB / Linux）でプラグイン/WorldEditのロードとコマンド登録に成功。合成Playerから日本語plan→preview→approve、432ブロックの実ワールド変更を確認。
- 二重承認拒否、既存goldブロックとの衝突拒否、Undoで全対象状態復元、21番目の書込を保護イベントで拒否した場合の完全復元、途中停止→再起動→`recover`に成功。最大64書込/tick、対象外のsentinelと元projectを保持。
- Sponge v2の実WorldEdit読込・Bukkit状態変換・配置を検証。差分14×8×21、432変更。承認後全都市 `.schem` は256×32×256、28,392 bytes。
- Playwright Chromium / iPhone13サイズ：既存Phase 3Bの選択・ピンチ・計画・承認・ダウンロード・Undo成功。追加したPaperモードでも同じ計画IDの受け渡し、Web単独の確定防止、15秒ポーリングによる建築/Undo後の地図更新に成功。JavaScript例外0。ブラウザ試験内のワールド完了通知は模擬、実ブロック試験は別のPaper試験。
- `setup_paper.py` をLinuxで実行し、署名ではなく配布元チェックサムに照合したPaper/WorldEdit/Mojangダウンロード、新規限定生成、秘密情報を出力しない設定生成を確認。セットアップの再実行拒否・秘密保護・EULA未同意の既定値をpytestで検証。
- Ruff静的解析・フォーマット検査、pip check、git diff --check成功。

[実Paper検証JSON](examples/phase4/server-verification.json) / [ブラウザ検証JSON](examples/phase4/browser-verification.json) / [比較プレビュー](examples/phase4/comparison.png) / [Web画面](examples/phase4/mobile-paper-review.png)。実行コマンドは [docs/PAPER.md](docs/PAPER.md)。

テストは実Minecraftクライアントを起動していません。実Windows・iPhone Safari・Geyser/Floodgate/Bedrockは未検証。Paperテストは管理者権限の合成Playerと必要な差分領域だけの地形fixtureを使い、オンライン認証/全都市の歩行は未検証です。管理環境からMojang公開鍵サーバーへ直接接続できない警告は、localhost限定offline-modeの試験に影響しません。通常セットアップはonline-mode=trueです。

書込前WALと停止/再起動復元を検証していますが、kill -9・電源断・ディスク故障・他プラグインによる強制変更を含む完全な原子性は保証しません。ワールド全体のコピーはサーバー停止中に管理者が行ってください。WorldGuard有効時は地域権限未対応のため建築を拒否します。
