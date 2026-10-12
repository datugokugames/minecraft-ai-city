# Phase 4：Paperゲーム内の都市編集（v0.4.0）

既存都市に日本語で建物・公園を追加し、外観を変更します。Phase 3Aの計画・承認・UndoとPhase 3Bの保存領域を共有します。**この版は既存都市の差分編集です。ゲーム内からの都市全体の新規生成・駅追加・道路再設計・都市外拡張は未対応です。**

## 対応バージョンと必要環境

- Minecraft Java / Paper **1.21.4 build 232**、**JDK 21**、WorldEdit Bukkit **7.3.10**でビルド・実サーバー試験済み。
- Python **3.10以上**、Maven **3.9以上**。APIキーなしでは `--parser rules`。任意LLMは既存の `--parser auto` とPC側の `OPENAI_API_KEY` を使用します。
- `.schem` は従来と同じSponge v2 / DataVersion 3465（1.20.1状態名）。WorldEdit 7.3.10のClipboardReaderで読み込み、Paper 1.21.4のBlockDataへ変換した差分432ブロックの配置・復元を検証しました。他バージョン全般の互換性を保証するものではありません。
- 8GB RAM・HDD・1人用の初期設定はPaper `-Xms512M -Xmx1536M`、view-distance=4、simulation-distance=3、32ブロック/3ms/tick。Pythonは生成時に全都市配列を2組展開します。まず256×256を使用してください。実サーバー試験はLinux / Java heap 1GiBで成功。Windowsの実測メモリ・HDD速度は未検証です。

## Windowsセットアップ

リポジトリを取得したフォルダのPowerShellで実行します。既存のサーバーフォルダは上書きしません。

```powershell
# Python/JDKが未導入の場合だけ（wingetとインターネット接続が必要）
.\scripts\setup_windows.ps1 -InstallTools
# 新しいPowerShellを開き、同じリポジトリで実行
.\scripts\setup_windows.ps1
```

スクリプトはJDK 21を確認し、venv・Python依存・チェックサム検証済みMaven 3.9.9を準備、プラグインのビルド/Javaテストを実行します。Paper、WorldEdit配布jar、Minecraftサーバーjarをチェックサム検証して `output/paper-local` に保存します。MavenのWorldEdit依存jarはサーバー配布物ではないので手動コピーしないでください。

PowerShellの実行ポリシーによりブロックされる場合は、スクリプトの内容を確認したうえで、そのプロセスだけ `powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1` として実行できます。システム全体の実行ポリシー変更は不要です。Windowsスクリプトの実機実行は未検証です。

手動ビルド・任意の新規出力先を使う場合：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
mvn -f paper/pom.xml package
.\.venv\Scripts\python.exe scripts/setup_paper.py --server-dir output/my-paper --project examples/managed-city/project.json
```

jarは `paper/target/minecraft-ai-city-paper-0.4.0.jar`。通常サーバーへ入れるのはこのjarとWorldEditだけです。**integration harnessはテスト専用で、通常のサーバーへ入れないでください。**

1. [Minecraft EULA](https://aka.ms/MinecraftEULA)を確認し、同意する場合にだけ `output/paper-local/eula.txt` を `eula=true` にします。セットアップは既定で同意しません。
2. `output/paper-local/start-paper.ps1` を起動。Java 1.21.4で `localhost:25565` に接続します。通常設定はonline-mode=true、loopback限定です。
3. サーバーコンソールで `op <自分のMinecraft名>`。ゲーム内の `/city identity` でUUIDを確認し、`paper-private.json` の `players` を `["そのUUID"]` にします。空リストは全員拒否。`["*"]` はテスト以外では推奨しません。
4. 別のPowerShellで `output/paper-local/start-backend.ps1` を起動。初回は指定projectを複製し、2回目以降は同じ都市状態から再開します。
5. Pythonが表示するWeb URLを開けます。Web用URLトークンと、Paper用の固定トークンは別です。Paperの `config.yml` と `paper-private.json` の秘密値を共有・Git保存しないでください。

Linux/macOSでは同じMaven/Pythonコマンドで準備できます。起動はサーバーフォルダで `java -Xms512M -Xmx1536M -jar paper.jar --nogui`、別端末で以下を実行します。

```bash
# 初回
citygen web --project examples/managed-city/project.json --state-dir output/paper-local/backend --parser rules --paper-config output/paper-local/paper-private.json
# 2回目以降は --project を省略
citygen web --state-dir output/paper-local/backend --parser rules --paper-config output/paper-local/paper-private.json
```

## 元都市を配置して座標を合わせる

差分編集は、PythonのprojectとMinecraftワールドの対象ブロックが一致している必要があります。未導入ワールドへ差分だけ貼り付けることはできません。

1. **新規のテストワールド**で、同じprojectに対応する `city.schem`（例：`examples/managed-city/city.schem`）をWorldEditの `plugins/WorldEdit/schematics/` にコピーします。既存ワールドなら停止中にワールド全体を別ドライブ/フォルダへバックアップしてください。
2. 例として都市原点をMinecraft `(0,80,0)` とする場合、`/tp @s 0 80 0` → `//schem load city` → `//paste`。回転・鏡像・相対オフセットを加えないでください。全都市の初回貼り付けはWorldEditの手動操作で、Phase 4の差分上限の対象外です。空のワールドでのみ実施し、完了を待ちます。
3. 都市 `(0,0)` の地表に立ちます（足のY=84）。`/city anchor 0 0` を実行します。原点は `floor(プレイヤーX)-都市X, floor(足Y)-4, floor(プレイヤーZ)-都市Z`。都市の地面Y=3、足Y=4です。Xは東、Zは南。回転は0のみ。
4. `/city status` で都市名・revision・ワールド原点を確認します。最初の計画でserver UUID、world UUID、原点が固定されます。別ワールド/原点には新しい独立したバックエンド保存先を用意してください。

足元が屋根・橋・段差の上ではanchorがずれます。対象地と周辺チャンクは先に移動して読み込み、建築範囲の外へ離れてから承認してください。自動チャンク生成・同期ロードは行いません。

## コマンドと承認フロー

```text
/city plan 中央区の空き地にコンビニを作って
/city preview
/city approve
/city status
/city undo
/city preview
/city approve
```

- `/city plan ここにコンビニを作って`、`この辺に公園を作って` は現在のX/Zを選択点にします。敷地が収まらない・接道しない場合はエラーになります。
- `/city pos1`、`/city pos2` を2地点で実行すると矩形を指定できます。`/city clear` で解除します。名前指定と選択範囲がある場合は両方を満たす範囲になります。
- 計画では位置、寸法（半開区間のbounds）、変更ブロック数、計画ID、衝突・保護検査をチャット表示し、予定範囲の8頂点にパーティクルを出します。地図・差分・JSONはWebで確認できます。
- **plan/preview/undoだけではブロックも現在projectも変わりません。** `/city approve` が明示承認です。
- `/city cancel` は計画を見送り、建築中なら中断して復元します。保存確定中は完了を待ってUndoしてください。
- `/city undo` は直前の自分のPaper編集の逆差分を計画し、再び `/city approve` でワールドとPythonの両方を復元します。過去のCLI/Webのみの履歴はワールドUndoできません。
- `/city recover` は起動時の未完了バックアップを復元/照合します。復旧が完了するまで新規編集は拒否します。
- 既存生成器には駅テンプレートがありますが、**編集エンジンの日本語「駅を追加」は未対応**としてエラーにします。対応文法は [EDITING.md](EDITING.md)。

権限は `city.use`、`city.plan`、`city.approve`、`city.undo`、`city.admin`（anchor/recover）。すべて既定OPのみ。Paper API側でもUUID許可リストを確認します。HTTPはプラグインを信頼する設計で、プレイヤーの独立したOAuth認証ではありません。

## Web地図と同じ計画を使う

`--paper-config` 付きで起動すると、Webからの直接apply/undoは拒否されます。Webで計画・比較プレビューを作り、「ゲーム内の承認手順」に表示される `/city review <review ID>` をゲーム内で実行します。続いてpreview/approve。**同じplan_id・元revision**を検証し、ワールド完了後にWeb地図は約15秒以内に更新されます。WebのUndoもゲーム内操作を案内します。編集後project/schematicはWebからダウンロードできます。

iPhoneから使うにはPythonへ `--lan --host <PCのプライベートIPv4>` を追加し、プラグインのbackend-urlも同じIPに変更して再起動します。localhostか明示したRFC1918 IPv4だけを許可。HTTPなので信頼するLAN限定です。ポート開放・インターネット公開・TLS運用は未対応です。

## 安全性と復旧

1. Pythonが既存編集エンジンで敷地・接道・道路/建物/河川/鉄道の衝突を検証。before/afterをブロック状態名で比較し、変更箇所のみのSponge v2を生成します。
2. PaperのIOワーカーがHTTP・ハッシュ検証・WorldEdit読込・バックアップ書込を行います。ゲームのメインスレッドでネットワーク通信しません。
3. 承認時に所有者・計画ID・revision・ワールド・原点を再検証。対象全ブロックがbeforeと一致するか確認し、書き込み直前にも確認します。二重承認と同時編集は拒否。
4. 編集対象のブロック状態をディスクへ保存し、各バッチの書込前にWALをfsync。変更を小分けにし、チャンクを編集中だけ保持します。空気を含め**変化したブロックだけ**を処理します。
5. chest等の保護材質、すべてのTileState（NBT/インベントリ）、範囲と周囲1ブロックのエンティティ、ワールド境界を拒否。保護プラグイン向け `CityBlockChangeEvent` を各書込で発火。WorldGuard有効時は地域権限連携が未実装なので安全のため拒否します。他の保護プラグインとの互換性は未検証です。
6. 失敗/キャンセル時は、書込前の状態へ小分けに復元。別の変更が入っていれば上書きせず復旧待ちにします。Python状態はワールド成功通知後にだけ確定します。返信消失時は保存済みrevisionを照合し、確定済みの都市を誤ってロールバックしません。

バックアップは `plugins/MinecraftAICity/backups` の変更箇所JSON/WALです。**ワールド全体のバックアップではありません。** Pythonのbackendフォルダと一緒に保存してください。バックアップ・計画・履歴の自動削除はありません。容量は定期的に確認してください。

Paper連携を開始した保存先は、再開時に `--paper-config` を省略すると起動を拒否します。Web単独モードへ誤って戻してワールドとの同期を外すことはできません。

サーバーは `stop`、PythonはCtrl+Cで終了します。Python強制終了後のserver.lockはプロセスが存在しないことを確認してからだけ削除してください。復旧待ちはバックアップを消して解除しないでください。対象チャンクを読み込んでから `/city recover`。復旧時も異なるプレイヤー変更を勝手に消しません。

ワールド・Python・WALをまたぐ分散ストレージの完全な原子性はありません。テストした中断は**書込途中のPaper停止→再起動**です。電源断、ディスク故障、kill -9、他プラグインによる直接変更、コミット後の未保存チャンク喪失の自動修復は未検証です。Minecraftのワールド保存が失われた場合はバックアップ照合・管理者による復旧が必要になります。

## API（JSON / protocol 1）

全APIは `Authorization: Bearer <Paper token>` と `X-City-Player: <UUID>` が必要です。Host/Origin検証、入力16KiB、タイムアウト、直列処理、Pydantic検証を適用。プラグインの接続タイムアウト5秒、要求90秒、応答5MiB、展開schematic8MiB。任意コマンド・任意ファイルパスを受け取りません。

| メソッド・パス（`/api/paper/` 配下） | 用途 |
| --- | --- |
| GET state | 都市・区・地区・revision・Paper予約状態 |
| POST plan | 日本語、選択範囲、revision、bindingから共有編集計画 |
| POST adopt | Webのreview IDとbindingから同じ計画を取得 |
| GET jobs/{id} | 所有者の計画・変更数・進捗・状態 |
| GET jobs/{id}/before.schem / after.schem | 所有者の差分ファイルのみ |
| POST prepare | job、revision、approval（plan_id）、bindingの承認・予約 |
| POST progress | job、completed（単調増加・上限検証） |
| POST finish | job、outcome=commit/rollback。全配置成功後だけcommit |
| POST undo-plan | bindingから逆差分の計画 |
| POST cancel | 未承認jobの見送り |

既定5,000変更ブロック、包絡体積262,144。Python/Paperの両方で制限、最大でも20,000/1,048,576。大きい差分は分割実行せず計画全体を拒否します。

## テストの再実行

```bash
python -m pip install -e '.[dev,browser]'
pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
mvn -f paper/pom.xml install
mvn -f paper/integration/pom.xml package
# EULAに同意する使い捨てのローカルテスト専用。既存フォルダは拒否。
python scripts/check_paper_server.py --server-dir output/paper-test-1 --accept-eula
python -m playwright install chromium
python scripts/check_paper_web.py --output output/paper-browser-1
```

テストサーバーは127.0.0.1:25585、heap1GiB、合成オペレーターでプラグインのコマンドを呼びます。初期地形fixtureは差分に必要なブロックだけを配置します。実クライアント接続・全都市探索のテストではありません。テスト用サーバーだけoffline-modeで、公開禁止。CIへアップロードするのはプラグインjar・JUnit XML・秘密情報を除いたverification.jsonのみです。

## 未対応・未検証と次の作業

- 実Javaクライアント、Windows実機、iPhone Safari、Geyser/Floodgate/Bedrockは未検証。チャットコマンド中心でJava固有GUIは不要ですが、Bedrockの日本語入力・UUID・権限・Javaブロック表示を別途確認します。`.mcworld`出力は未実装。
- 駅・鉄道・河川・道路の対話追加、初回全都市の自動配置、複数都市/複数サーバー、回転、都市外拡張、NBT家具、完全な地域保護連携は未実装。
- 初回全都市貼り付けの負荷測定、実プレイヤーによる歩行と承認/Undo確認、強制終了・電源断相当の復旧試験を優先します。
