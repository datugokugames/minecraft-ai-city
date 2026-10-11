# Phase 3A：日本語で既存都市を編集する（v0.3.0）

既存 `project.json` を読み、日本語 → 検証済み編集計画 → 前後プレビュー → 明示承認 → 新しい都市と `.schem` → UndoまでCLIで実行できます。APIキーなしでも下記の限定文法で動きます。建物追加・公園追加はorganic都市、外観変更はorganic/legacy両方に対応します。

## 実際のコンビニ追加

インストールはREADMEの手順を使用します。保存済みの実OSM特徴から作った51棟の都市を編集する例です。

```bash
citygen edit plan --project examples/managed-city/project.json \
  --instruction "中央区の空き地にコンビニを作って" \
  --parser rules --seed 42 --output output/store-review
```

ここでは元都市は変わりません。`output/store-review/comparison.png` と `plan.json` を開き、対象・建築仕様・接道通路を確認してください。コンビニはブランドを模倣しない1階建ての店舗で、ガラス面・帯看板・庇・カウンターを備えます。

確認後、表示された64文字の `plan_id` を `<PLAN_ID>` の代わりに指定すると承認・反映されます。

```bash
citygen edit apply --project examples/managed-city/project.json \
  --plan output/store-review/plan.json --approve "<PLAN_ID>" \
  --output output/store-applied
```

`output/store-applied/project.json` が新しい都市です。`city.schem` は新状態の都市全体を含みます。元の `examples/managed-city/project.json` は保持されます。承認なし、異なるID、元プロジェクトの変更、計画の改変があれば適用しません。

取り消す場合は、現在のプロジェクトと、取り消したいapplyの履歴を指定します。Undoにも明示承認が必要です。

```bash
citygen edit undo --project output/store-applied/project.json \
  --history output/store-applied/history.json --approve "<PLAN_ID>" \
  --output output/store-undone
```

復元結果は `output/store-undone/project.json` と `city.schem` に保存されます。元の51棟へ戻り、この同梱例では変更前のschematicとバイト一致します。

**各操作は新しい出力先を指定してください。既存ディレクトリ・スナップショットへの上書きは拒否します。** 中断・書き込み失敗があれば元都市から別の出力名で再実行します。`complete.json` は最後に保存し、未完了の履歴はUndo対象にできません。失敗した出力を自動削除したり、元ファイルを変更したりしません。

## 日本語文法と対象指定

| 指示例 | 追加の指定 |
| --- | --- |
| `中央区の空き地にコンビニを作って` | 名前は実際に登録した区名を使用 |
| `この範囲に公園を作って` | `--bounds XMIN ZMIN XMAX ZMAX` |
| `駅の東側にマンションを建てて` | 駅が1つの都市。複数駅は確認要求 |
| `この地区に住宅を5棟追加して` | `--district district-2` など |
| `指定した建物の外観をもっと現代的にして` | `--building-id 10` など |
| `建物10の外観を和風にして` | 指示内の建物IDを使用 |
| `ここにコンビニを作って` | `--at X Z` または `--bounds ...` |
| `X=100,Z=140に住宅を建てて` | 指示内のローカル座標を使用 |

- 場所は `--city-name`、`--ward`、`--district`、`--at`、`--bounds`、`--building-id` で指定できます。区/地区は名前またはID。同名地区は区名または一意な地区IDも必要です。都市全体を対象とする場合も都市名を明示します。
- 明示した条件は交差として扱い、互いに矛盾する条件は拒否します。「ここ」「この範囲」に位置情報がなければ `clarification_required` を返します。「この地区」には地区指定が必要です。
- Xの増加が東、Zの増加が南です。Minecraftワールド座標ではなく、都市内の0〜sizeの座標です。`--at` は配置中心で最大0.5ブロック丸めます。矩形では建物・屋根の余白まで範囲内に収めます。
- 「作って」「建てて」「建設して」「追加して」、住宅/戸建て、1〜10棟を扱います。1指示は1種類の操作です。5棟分の土地が確保できなければ、1〜4棟だけを黙って追加することはありません。
- 住宅は2階、マンションは都市計画の上限内で最大4階、コンビニは1階です。建物は道路方向へ向け、既存道路への入口通路を0.5〜20ブロックで確保します。
- 「空き地」は既存建物・登録敷地・道路・鉄道・河川・接道通路に占有されていない土地です。登録済みのparking/vacantなどの敷地はこの版では置換しません。新規追加先がなければ範囲の変更を求めます。
- 地区指定の公園は10×10、座標指定は12×12です。矩形指定は指定範囲の内側2ブロックを余白にして使用します。小さすぎる・道路や建物を含む範囲は拒否します。
- 新しい入口通路は既存道路へ接続するため選択範囲外へ出る場合があります。通路もプレビューに表示し、周辺建物・敷地・河川・鉄道との衝突を検証します。
- 外観変更は1棟ずつ、modern/traditional/warmの外壁・屋根パレットと既存の雛形で行います。「現代的」「モダン」「和風」「暖かい感じ」に対応。専用配色が固定の学校・駅・工場の変更は未対応です。
- 未対応の語句、削除・移動・否定を含む操作、曖昧な対象はエラーまたは確認要求です。CLIは終了コード2と `validation.json` を返します。失敗した提案には有効な `plan.json` を作りません。

## 計画・保存ファイル

| 出力 | 内容 |
| --- | --- |
| plan時の `plan.json` | 元プロジェクトのダイジェスト、解釈元、seed、対象条件、正確な追加・変更データ、検証結果、補完条件、plan_id |
| `diff.json` | 建物追加、建物before/after、公園追加。Undo時は逆方向の差分 |
| `before.png` / `after.png` / `comparison.png` | 変更前後の2D地図、変更箇所の枠付き比較（plan時） |
| `validation.json` | 成功・承認待ち、衝突・解釈失敗・確認要求 |
| `before.project.json` | 入力プロジェクト全体のスナップショット |
| apply/undo時の `project.json` | 新状態。既存の区名・地区名・ID・出典を保持 |
| `city.json` / `city.schem` / `preview.png` / `report.json` | 共通建築エンジンの実出力 |
| `edit-plan.json` | 承認された編集計画。出力の `plan.json` は従来の都市計画であるため別名で保持 |
| `history.json` / `complete.json` | apply/undo、承認ID、前後ダイジェスト、書き込み完了記録 |

計画作成では候補の都市をメモリ内で検証し、変更後の正式なprojectやschematicは保存しません。適用時は**LLMを再呼び出さず**、保存した構造化条件とseedから配置を再計算して、承認済みの差分と一致することを確認します。別の設定・区名への変更も元プロジェクトの差異として検出します。

これはローカルの明示承認・競合検査です。ユーザー認証や電子署名ではありません。CLIに渡したprojectが「現在の都市」であり、中央サーバー上の最新状態を自動追跡するものではありません。後続Web/Paper層で認証と共有状態の排他制御を追加します。

編集は新しいディレクトリに順次保存します。複数回のUndoは、最新の編集から順に対応する履歴を指定してください。現在状態が履歴の編集結果と一致しない場合は拒否します。Undo自身のUndo（Redo）・部分Undoは未実装です。

## 任意のLLM解析

`--parser auto` はキーがあればOpenAI、なければルール。`--parser rules` は通信なし。`--parser ai` もキーなしの場合は警告付きでルールへ戻ります。設定は既存の `OPENAI_API_KEY` / `OPENAI_MODEL` 環境変数を使用します。

LLMは指示、明示選択、都市名と登録済みの区/地区情報から、限定されたEditIntent JSONを返します。配置座標の決定とブロック構築はPython側が行います。JSON Schema・Pydantic・対象検索・幾何検証の各段階を通します。明示座標・建物IDをモデルが捏造することや、既知文法の棟数・操作を変更することは拒否します。

API障害・拒否・不正JSONの際は、元の指示を限定文法で正確に扱える場合だけルールへ戻ります。ルールでも解釈できなければ停止します。AIが確認要求を返した場合も停止します。成功・拒否・タイムアウト・不正JSONなどはモックで検証しています。実際のAPI通信はこの環境にキーがないため未検証です。

## 構成・今回の制限

`edit_language.py`（解釈）、`edit_models.py`（契約）、`edit_geometry.py`（対象/土地選定）、`editing.py`（計画・承認・履歴/Undo）、`edit_cli.py`（操作窓口）、`exporting.py`（共通出力）に分けています。WebやPaperは同じPython APIを呼ぶ構成に拡張できます。

新しい道路・河川・橋・駅、都市外への拡張、既存建物の削除・移動・再開発、詳細内装、複数操作をまとめた自然言語指示、Web/Paper/Bedrockは後続作業です。全都市を再出力する方式であり、Minecraftワールドへの差分適用やチャンク単位出力はありません。ゲーム内で手編集した建物は追跡しません。schematicは通常のWorldEdit導入手順で別のテスト場所へ貼り付け、配置を確認してください。Minecraft/WorldEdit実機での歩行・貼り付けは未検証です。
