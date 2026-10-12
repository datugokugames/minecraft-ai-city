# Phase 5：オリジナル外装の設計（Python v0.5.0）

日本語・敷地・周辺環境・seedから構造化された建築仕様を作り、同じPythonジオメトリで単体schematicと都市編集を生成します。固定建物のブロックコピー/拡大は行いません。外部APIなしのローカルモードはルールと手続き生成です。外部LLMモードはOpenAI互換APIが仕様を提案し、検証後に同じ生成器へ渡します。

## カテゴリ・寸法

12カテゴリ：戸建住宅、アパート、マンション、オフィス、商業ビル、コンビニ、スーパー、学校、駅舎、工場、倉庫、公共施設。カテゴリ固有の階高、階数の基本範囲、屋根、材料、ベランダ/庇/店舗ファサード/駐車場の条件は [templates.json](../src/minecraft_city/architecture_data/templates.json) に保存しています。新規カテゴリの追加はJSONだけでなくCategory型と都市用kindの対応も必要です。既存カテゴリの条件はJSON編集で変更できます。

- **幅12〜64、奥行き14〜64、高さ7〜96ブロック**。幅/奥行きは敷地の最大寸法で、建物本体の寸法ではありません。庭・駐車場・庇・ベランダ・植栽も敷地内に含めます。
- 幅は入口/道路側に沿う方向、奥行きはそこから離れる方向。単体仕様は北側を前面（Xが幅、Zが奥行き）。都市では道路に近い四方位へ向け、東/西向きはワールドX/Zにおける幅と奥行きが入れ替わります。都市・Minecraftの原点はPhase 4と共通。
- 都市編集の位置指定は**敷地中心の都市X/Z**、矩形は建物・余白を収める許可範囲。名前指定では接道する空地を探します。指定敷地の周囲1.5ブロックも検証に使うため、20×30の範囲そのものへ20×30を指定すると余白不足で拒否する場合があります。
- 高さは地表から外装の最高ブロックまで。階高/階数/屋根を検証し、高さ指定なしなら近隣最大8棟の平均階数と用途の基本範囲から選びます。戸建・店舗・駅等の極端に高い指定は理由と代替案を返します。
- 寸法を黙って縮小しません。道路・敷地・河川・鉄道・都市境界との衝突は停止します。学校は建物外装のみで、校庭を確保する計画ではありません。駅舎も外装のみで、線路/ホームへの自動接続は未対応。

## 単体建築を生成

READMEのPython環境を導入後に実行します。出力先は新規フォルダに限定します。Minecraftへの配置は行いません。

```bash
citygen architecture --instruction "ここに20×30のコンビニを作って" --mode local --seed 42 --output output/store-exterior
citygen architecture --instruction "ここに40×50、高さ60ブロックのオフィスビルを作って" --mode local --seed 49 --output output/office-exterior
```

`--category office --width 40 --depth 50 --height 60` の引数指定も可能。日本語と引数が矛盾すればエラーにします。単体の「ここ」はファイル内の原点を意味し、実プレイヤー位置とは無関係です。

| ファイル | 内容 |
| --- | --- |
| design.json | 敷地・カテゴリ・棟の矩形位置/寸法/階数・高さ・階高・形状・壁/アクセント/屋根材・窓間隔/幅/高さ・道路向き・入口・外装オプション・seed |
| execution.json | local/llm/fallback、実通信試行の有無、モデル、トークン使用、概算コスト（不明ならnull） |
| building.schem | 敷地全体のSponge v2。内部は空洞、各階床・外装のみ |
| top.png / isometric.png | 2D上面と外装アイソメトリック。実ゲーム画面ではありません |
| verification.json | 寸法、階数、高さ、非airブロック数、生成モード |

[5カテゴリ×2サイズの比較](../examples/phase5/buildings/comparison.png)。再生成：

```bash
python scripts/generate_architecture_samples.py --output output/architecture-samples
```

## 既存都市で計画・承認・Undo

Phase 3AのCLIをそのまま利用できます。

```bash
citygen edit plan --project examples/managed-city/project.json --instruction "青葉市に30×40のマンションを建てて" --parser rules --seed 42 --output output/apartment-review
# comparison.png、architecture.png、plan.jsonを確認
citygen edit apply --project examples/managed-city/project.json --plan output/apartment-review/plan.json --approve <完全なPLAN_ID> --output output/apartment-applied
citygen edit undo --project output/apartment-applied/project.json --history output/apartment-applied/history.json --approve <同じPLAN_ID> --output output/apartment-undone
```

計画段階では元projectを変更しません。承認後は別フォルダへ新project・全都市schematic・スナップショット・履歴を保存。旧project/planは読み込み可能で、Phase 1〜4の保存例とダイジェストを保持します。Phase 5建築の外観変更も既存の建物ID指定で可能です。

サイズ付き指示、新しいカテゴリ、「周囲と調和する建物」はPhase 5へ渡します。従来のサイズなし「コンビニ」「住宅」「マンション」の指示はPhase 3Aの小型追加を維持します。Phase 5の外装を確実に選ぶ場合は寸法を指定するかWebのカテゴリを選んでください。サイズなし「駅」は旧パーサでは未対応のままですが、「駅舎」またはサイズ付きの駅指示はPhase 5へ渡します。

## WebとPaper

Webの「自由建築の外装・寸法を指定」を開き、カテゴリ、道路側幅、奥行き、高さ、モードを入力します。地図で位置/範囲を選び、「この場所にマンションを建てて」などを送信。JSON、前後地図、外装PNGを確認して承認します。カテゴリ未選択なら従来の編集/日本語内の寸法を使用します。`llm` にしてもキーなしならlocal_no_keyとして表示されます。

Paperは [PAPER.md](PAPER.md) の既存プラグイン/APIを利用します（Javaプラグインの配布名は互換の0.4.0、Pythonは0.5.0）。このブランチから再ビルドすると外装カテゴリ・寸法・高さのチャット表示も追加されます。

```text
/city plan ここに30×40のマンションを建てて
/city preview
/city approve
/city undo
/city approve
```

都市ローカル `(64.5,20.5)` 付近のサンプル都市で30×40の敷地を使用し、実Paperに4,410変更ブロックを配置して復元しました。テストは合成Playerと使い捨てワールドです。すべての場所にこのサイズが収まるわけではありません。

Webで計画した外装も `/city review <review ID>` で同じ案を使用します。PaperモードではWeb単独のapply/undoを引き続き拒否します。ゲームの設計モードはPythonの `ARCHITECTURE_DEFAULT_MODE=local|llm|auto` で選びます。Paperにキーを保存しません。

## LLM設定と実行記録

既定はローカル。外部LLMを使う場合、PCの環境変数で設定します。

```text
ARCHITECTURE_API_KEY=<秘密値>           # なければOPENAI_API_KEYを利用
ARCHITECTURE_MODEL=gpt-4.1-mini
ARCHITECTURE_API_BASE=https://api.openai.com/v1
ARCHITECTURE_DEFAULT_MODE=llm
ARCHITECTURE_INPUT_USD_PER_MILLION=<入力単価>     # 任意
ARCHITECTURE_OUTPUT_USD_PER_MILLION=<出力単価>    # 任意
```

API base/modelは管理者設定のみで、WebやLLM出力から変更しません。OpenAI互換chat/completionsとJSON schema応答を受けるHTTPS API向けです。Anthropic等の独自API形式は未対応。接続10秒/応答45秒、出力2600トークン、JSON文字列20000文字を制限。キーをファイル・計画・実行ログへ保存しません。

既知の日本語と場所はローカルの厳密な解析を優先し、外部LLMにはそのカテゴリ・明示寸法・実在の周辺情報・ルールから建築仕様を設計させます。APIへの送信はその建築情報だけです。数万のブロック列挙、コード、コマンド、ファイルパスは仕様にありません。

指定寸法/category/seed/道路向き、材料の許可リスト、棟の接続・範囲、階高、入口、階数、実生成のブロック数を検証します。拒否/不正JSON/通信失敗はローカル設計へフォールバックし、その理由の例外型を記録します。フォールバックでも成立しない敷地/高さはエラーです。LLM案はplan.jsonへ固定され、承認やUndo時に外部AIを呼びません。

使用トークンと設定単価による概算USDはexecutionへ記録。料金表は自動取得せず、実請求額ではありません。使用量を取得できない失敗はnullで、無料であることを意味しません。**この開発環境で実外部APIへの通信は行っていません。成功・不正出力・寸法変更は応答モックで検証しました。**

## 安全制限・負荷

- 単体外装は64×64×97以下、非air最大50,000。都市上限は既存512×512。軽量環境では256×256、1棟ずつ、localを推奨。
- Paperは従来どおり既定5,000変更、最大20,000、包絡体積262,144、64/tick・4ms以内。Windowsセットアップは32/tick・3ms。50,000の単体schematicがPaperで貼れるという意味ではありません。
- 変更数が超過した場合は計画全体を拒否。例：大きいオフィスは単体出力可能でも、都市/Paperの上限では拒否される場合があります。敷地または高さを減らすか、運用者がPython/Paper両方の設定を明示的に変更してください。上限を自動で緩めません。
- NBT/インベントリ/エンティティは生成しません。WorldGuard未連携時の拒否、TileState/保護材質/範囲内エンティティ拒否、直前再照合、WAL、ロールバック、Undo、API認証はPhase 4を維持します。
- JSONのブロック列挙・未知の材料・コード・任意パスは拒否。モデルがOSやMinecraftコマンドを実行する機構はありません。

## 検証・現在の制限

[再検証結果](../examples/phase5/phase4-revalidation.json) / [新建築Paper結果](../examples/phase5/paper-architecture-verification.json) / [Web結果](../examples/phase5/browser-architecture-verification.json)。Python/Java/ブラウザ/実PaperはPRのChecksで再確認できます。

```bash
pytest -q
python scripts/check_architecture_web.py --output output/architecture-browser
# Paper/MavenはPAPER.mdの手順でビルド後、使い捨てテスト
python scripts/check_paper_server.py --server-dir output/architecture-test --accept-eula --instruction "ここに30×40のマンションを建てて" --at 64 20
```

形状は矩形/L字/中庭型/段状と最大3棟の組合せで、任意曲面/斜め壁/曲面屋根を扱うCADではありません。窓は規則的なベイ生成、道路向きは四方位、地形に合わせた可変基礎は未実装。看板は無地の色帯・駅の簡易時計で、NBTによる駅名文字は未実装。家具・詳細な部屋割り・歩行経路の実クライアント検証は対象外。内部は空洞と床で、任意の `stairs` 仕様は基本階段を生成できますが実歩行は未検証です。

実Minecraftクライアント、Windows/iPhone実機、Geyser/Floodgate/Bedrock、電源断の完全復旧は引き続き未検証。次は実プレイヤーの出入口/階段/建築Undo試験、Windowsのメモリ・HDD負荷計測、実LLM通信、地域保護連携と形状の高度化を優先します。
