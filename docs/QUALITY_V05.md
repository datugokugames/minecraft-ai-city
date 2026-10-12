# v0.5 建築・都市品質

現在のorganic生成器は道路の接続と不規則さに配慮する一方、接道候補を道路側から順に探し、7〜11ブロック程度の小さな回転矩形を置くため、都市は疎です。駐車場/空地は予約され、交差点・河川・鉄道の余白もあるので、計画の被覆率をそのまま達成できません。屋根・窓の規則が限られ、旧市街/駅前/郊外の外装差も小さい状態でした。

## 互換性と実装範囲

- 道路生成・都市schema_version=1・既存CLI・Paper API protocol1を維持。旧データには新しい `facade` を付けず、NoneをJSONから省略し、ダイジェストと再建築を維持します。
- `architecture_parts.py` の窓・扉・庇・看板・灯り・ベランダ・屋根を、外装エンジンと新しいFacadeRecipeで共有。窓/扉は固定ブロック寸法、建物の大きさに応じてベイ数を変えます。
- 旧建物のID・敷地・入口通路・階数を維持したまま、用途/地区とseedで窓間隔、屋根、ベランダ、店舗/施設の庇・看板・外部照明を選択します。既存の地区別壁パレットも使います。
- 道路沿いの候補へ住宅/商業/工業の小型建築を追加し、最大2つのL字施設を試します。登録された駐車場/空地/公園と河川/鉄道を置換しません。空地/駐車場の内側に植栽を置き、登録用途と通路を保持します。
- 外部建築データは使用していません。パーツはこのプロジェクトで作成したコード（MIT）。元都市のOSM/合成データ出典はprojectとschematicへ維持します。

## 新規出力だけへ適用

```bash
citygen improve --project examples/managed-city/project.json --seed 42 --output output/quality-v05
# 追加上限・投影被覆率の目標を設定（達成を保証しない）
citygen improve --project examples/managed-city/project.json --seed 7 --max-additions 12 --target-coverage 0.08 --output output/quality-v05-7
# 保存された異なる道路/配置の3シードを一括比較
python scripts/compare_city_quality.py --output output/quality-comparison --seeds 42 7 19
```

`before/` と `after/` に都市JSON・2D PNG・Sponge v2を保存し、`after/project.json`、`comparison.png`、`quality-comparison.json` も生成します。既存の出力先は拒否。元のproject、ワールド、バックエンドの状態には書き込みません。改善都市をPaperで使用する場合は、新規テストワールドへ手動配置し、新しいstate-dirとanchorを用意してください。既存backendへprojectを無断置換しません。

## 品質指標と結果

`actual_roof_coverage` は本体/棟のベクトル投影面積です。Phase 5の庭・駐車場を含む予約敷地面積とは別に計測し、回転のボクセル誤差や屋根の軒は含めません。形状数、屋根数、面積/コンパクトネス/階数/屋根による形態シグネチャ数、窓パターン、地区被覆率、道路表面比、登録空地比を保存します。これらは絶対的なリアリティ得点ではありません。

| 元都市seed | 本体被覆率：旧→新 | 追加棟数 | 形状種類：旧→新 |
| --- | --- | ---: | --- |
| 42 | 4.70%→6.00% | 11 | 1→2 |
| 7 | 5.17%→6.29% | 9 | 1→2 |
| 19 | 4.91%→5.58% | 4 | 1→2 |

すべて道路・登録敷地を保持し、道路1連結、建物の重複なし、全建物の接道を検証しました。9%目標は全シード未達です。追加数/探索予算、道路・河川・鉄道・既存建築・余白の制約で終了した結果をそのまま公開しています。選別して成功例だけを載せる処理はありません。

別のmanaged-cityでは5.15%→6.39%、形態シグネチャ26→39、屋根2→3、窓パターン6を確認しました。L字の本体と庭を分けて数えるため、予約敷地の7.21%を本体被覆率と誤認しません。

[3シードJSON](../examples/quality-v05/comparison.json) / [新旧PNG](../examples/quality-v05/seed-42/comparison.png) / [改善schematic](../examples/quality-v05/seed-42/city.schem)。パーツの外装例は [Phase 5サンプル](../examples/phase5/buildings/comparison.png)。

## 検証・制限

pytestで3シードの密度/多様性改善、衝突・接道、同一seed再現、異なるseed変化、旧ID/形状/道路/敷地保持、出力先の上書き拒否、schema/schematicを確認。実Paper1.21.4 / WorldEdit7.3.10で改善全都市256×32×256のSponge v2読込に成功。さらに同じ実サーバーで30×40マンション4,410ブロックの承認、Undo、衝突、失敗復元、再起動復元を5,000変更上限で確認しました。[結果](../examples/quality-v05/paper-verification.json)。

Paper試験は使い捨てワールドと合成プレイヤーです。改善全都市の一括貼り付け・歩行、Windows8GB/HDD実機、外部LLM、Bedrockは未検証。単体パーツの種類には限りがあり、敷地の分割/道路再設計・地形対応基礎は今回扱いません。8GBでは256×256・1棟ずつの編集、Paper heap1〜1.5GiB・32〜64配置/tickを初期値にします。実際のRAM/ディスク性能測定はv0.6で行います。

v0.6以降は街区内部の敷地分割と目標被覆率、実歩行・実機負荷・地域保護・外部AIの実通信を優先します。[ロードマップ](ROADMAP.md)。
