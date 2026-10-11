# 都市・区・地区・敷地の管理（v0.2.1）

`project.json` は既存の `city.json` を丸ごと含む自己完結した管理ファイルです。都市名 → 区 → 地区 → 敷地 → 建物の参照を保存します。元の `city.json` の形式、乱数、道路や建物の配置、建築エンジンは変更しません。organicとlegacyの両方を読み込めます。

## 実行手順

```bash
# 初期状態は1区。全地区・建物・既存の駐車場や空地を登録する
citygen project init --city examples/natural-city/city.json \
  --name 青葉市 --ward-name 中央区 --output output/project-initial.json

# 明示した名前・所属・希望条件だけを変更し、別ファイルへ保存する
citygen project configure --project output/project-initial.json \
  --settings examples/management/settings.json --output output/project-configured.json

# 区別の地区・建物ID、開発済みかどうかを確認する
citygen project inspect --project output/project-configured.json

# 同じ建築器でPNGとschematicを出力する。出力先は新規ディレクトリを指定
citygen project export --project output/project-configured.json --output output/managed-city
```

`init` / `configure` は既存ファイルへの上書きを拒否し、`export` は既存ディレクトリを拒否します。設定の検証に失敗した場合、元のプロジェクトを変更しません。中断で不完全な新ファイルが残った場合は元のスナップショットから別の出力名で再実行してください。自動的な履歴管理やundoコマンドはまだありませんが、以前のJSONを再読込・再出力できます。

同梱例は2区、実体のある6地区、未開発の1地区です。`district-2` の所属を北区に移し、`north-future` を北区拡張予定地として登録します。**この操作で住宅、道路、地形が追加・削除・再配置されることはありません。** 元の建築データを同じエンジンへ渡すため、`.schem` とPNGは元都市とバイト一致します。管理名は `project.json` / `hierarchy.json` に保存し、現時点のプレビューの都市タイトル・地区ラベルやschematic内部の名称は元の都市計画を使用します。

## 設定JSON

`examples/management/settings.json` を編集できます。

| 項目 | 動作 |
| --- | --- |
| `city_name` | 日本語の都市管理名 |
| `wards[].id`, `name` | 区をIDで更新、存在しないIDなら空の区を登録 |
| `districts[].id`, `name`, `ward_id` | 地区の名前・所属を明示指定。新IDなら未開発地区 |
| `preferred_use` | 将来の用途条件: residential / commercial / industrial / park / mixed |
| `preferred_style` | 将来の外観条件: traditional / modern / mixed |

省略した区・地区・希望条件は保持します。希望条件だけは明示的な `null` で解除できます。名前は空白だけを禁止し、最大80文字。IDは英小文字で始まる英小文字・数字・`_`・`-`の最大64文字です。区名は都市内で、地区名は所属区内で一意にします。

`preferred_*` は**次回の設計に使うための保存情報**で、現在の生成器にはまだ適用しません。`inspect` は既存の `actual_use` と希望用途を分けて表示します。設定から直接 `city_district_id` や道路・建物を変更することはできません。

## データの境界と検証

- 既存地区・建物の整数IDを保持し、管理IDと対応付けます。既存地区は必ず1つの管理地区に、各建物・空地は必ず1つの敷地に対応します。建物の所属地区と敷地の地区の不一致も拒否します。
- 敷地の `polygon` は建物または既存の駐車場・空地の占有形状です。`boundary_kind: occupied_footprint` と明示しています。庭・接道通路まで含む完全な宅地分割や地籍を表すものではありません。
- 旧形式の空地に地区IDがないため、初回のみ包含関係→距離→地区IDの順に所属を決めて保存します。以後の名前・所属変更ではその敷地対応を保持します。
- 道路・河川・鉄道は都市の共有データとして1つだけ保持し、区への所属変更で切断しません。新しい区を追加しても境界を地形へ描きません。
- `city_sha256` はJSON正規化後の元都市のSHA256です。配置の意図しない変更を検出します。電子署名や改ざん防止認証ではありません。読み込み時に参照整合性と従来の接続・重複・接道検査も実行します。
- 元都市のOSM出典・ライセンス・乱数シードをそのまま保持します。

## Pythonと将来の操作画面

`create_project` / `configure_project` / `load_project` / `save_project` / `project_summary` を `minecraft_city.project` から利用できます。CLIと将来のWeb・ゲーム内操作はこのデータを共有できます。

現在は管理情報の操作のみです。区単位の**実際の拡張・再開発**、日本語の解釈、範囲選択、実ワールドへの反映は [段階的な開発方針](ROADMAP.md) の後続フェーズです。
