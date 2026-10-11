# Minecraft AI City Generator

**現実の都市の数値特徴を混ぜ、架空の日本風都市をMinecraft Java Edition向けに生成する、実行可能なPython MVPです。** OSMの元の道路・建物座標を生成器に渡さず、道路密度、区画面積、建物密度、高さなどの統計から新しい配置を作ります。

![東京60%・京都40%の統計から生成した架空都市](examples/japan-city/preview.png)

同梱の実データ生成例は256×256ブロック、seed=2026。住宅地・商業地・公園、住宅・マンション・雑居ビル・店舗・学校、高架鉄道を含みます。これは東京や京都の地図の再現ではありません。

## 1. 環境構築

Python **3.10以上**。この環境ではPython 3.12で動作確認しました。GUI、Minecraftサーバー、GPU、APIキーは生成に不要です。

```bash
cd minecraft-ai-city
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Windows PowerShellでは仮想環境を `.venv\Scripts\Activate.ps1` で有効化してください。インストール後は `citygen` または `python -m minecraft_city` を使えます。

検証環境の固定バージョンは `requirements-lock.txt` にあります。同じ依存バージョンで再現する場合は、Python 3.12で `python -m pip install -r requirements-lock.txt`、続いて `python -m pip install -e . --no-deps` を実行してください。他のPythonバージョンでは通常の `pyproject.toml` によるインストールを推奨します。

## 2. 最短で動かす：ネットワーク不要のデモ

```bash
citygen demo --seed 42 --output output/demo
```

初回の依存導入後は、通信もAPIキーも使いません。東京風・京都風の**合成サンプル形状**を分析し、60:40で混ぜます。サンプルは実測データを装わず、JSONの `source: synthetic_sample` とプレビューに明示します。

| ファイル | 内容 |
| --- | --- |
| `profiles/*.json` | サンプル都市の分析結果（demo時） |
| `city.json` | シード、統計、計画、道路、地区、全建物、検証結果 |
| `plan.json` | 再利用・手動編集できる構造化都市計画 |
| `preview.png` | 地区・道路・建物種別を色分けした2Dマップ。数字は階数 |
| `city.schem` | WorldEdit用Sponge Schematic v2、gzip圧縮NBT |
| `report.json` | 接続・重複検査、出典、パレット数、出力サイズ |

同じ出力ディレクトリで実行すると既存の生成物を上書きします。比較する場合は別のディレクトリを指定してください。

## 3. 実際のOSMデータを分析・比較する

地域プリセットは都市全域ではなく、約0.48 km²の小範囲です。

```bash
citygen analyze --region tokyo --output output/profiles/tokyo.json
citygen analyze --region kyoto --output output/profiles/kyoto.json
citygen compare output/profiles/tokyo.json output/profiles/kyoto.json \
  --output output/comparison.json
```

任意範囲は緯度経度の `南,西,北,東` 順で指定します。

```bash
citygen analyze --bbox 35.001,135.758,35.007,135.766 \
  --name "Kyoto small area" --output output/profiles/custom.json
```

- Overpass APIに道路・建物・土地利用を**1回のリクエスト**で問い合わせます。Nominatimにはアクセスしません。
- リクエストは最大4 km²、各辺4 km、最小辺50 mに制限。タイムアウトを設定し、自動リトライや大量並列取得は行いません。
- `.cache/osm` に応答と取得時刻を保存し、同じ範囲は再取得しません。更新が必要な場合だけ対応するキャッシュを移動・削除してください。
- 通信失敗・タイムアウト・不完全な応答では、警告を出して合成サンプルに切り替えます。実データのみ許可する場合は `--no-fallback` を指定します。
- `--sample tokyo` / `--sample kyoto` は最初からオフライン分析を行います。

**2026-10-11に実取得した東京・京都のプロファイルと比較結果を `examples/osm/` に同梱しています。** 公開OSMの元応答も `examples/osm/raw/` に収録しています。同梱のキャッシュから同じ分析を再計算できます。

```bash
citygen analyze --region tokyo --cache-dir examples/osm/raw \
  --no-fallback --output output/reanalyzed-tokyo.json
```

### 指標の意味とデータ品質

UTMへ投影して距離・面積をメートル単位で計算します。道路密度は道路長/km²、交差点密度は次数3以上の点/km²、建物密度は棟/km²、被覆率は建物面積の和集合/分析面積です。

- 区画面積は道路網の閉じた面の中央値です。登記上の筆・敷地境界ではありません。閉路がなければ0を記録し、計画時に既定値を使います。
- 道路はOSMの `highway` 線形要素を含み、歩道・小道も含みます。面指定の広場は道路線から除外します。交差点は2D幾何上の交点で、立体交差・橋・トンネルを分離した交通解析ではありません。
- 直交性は道路方位の4倍角の長さ加重平均です。回転した格子も高い値になり、0〜1の規則性指標として扱います。
- 建物高さは `height`（mまたはft）、次に `building:levels × 3 m`、欠損時は9 mです。`height_tag_coverage` に実タグの割合を残します。
- 土地利用比率は分類できた住宅・商業・公園の面積に対する比率。工業を商業、森林・レクリエーション地を公園へまとめるMVPの簡略化があります。
- `landuse_tag_coverage` に分類済み領域の面積率を保存し、タグが疎なときは既定の住宅65%・商業25%・公園10%を補間します。今回の小範囲では土地利用タグが少なく、公園だけのタグから「街全体が公園」と推定しないようにしています。
- OSMの建物タグ・高さ・土地利用の欠損や、データ更新による差があります。結果は都市の正確な統計調査ではありません。

## 4. 特徴を混ぜてオリジナル都市を生成する

同梱の**実OSM分析データ**を使えば通信なしで実データ由来の都市を生成できます。

```bash
citygen generate \
  --profile examples/osm/tokyo.json \
  --profile examples/osm/kyoto.json \
  --weights 0.6 0.4 \
  --planner rules --seed 2026 --size 256 \
  --output output/japan-city
```

`--profile` の順番と `--weights` の順番を対応させます。重みは非負、合計正。`60 40` のような値も自動正規化します。省略時は均等です。サイズは128〜512に対応し、MVPの標準は256です。

生成の仕組み：

1. 指標を重み付き平均し、道路間隔、道路の歪み、建物の大きさ・密度、階数、地区配分へ変換します。
2. シードから区画幅、道路節点、商業中心、地区・建物を新しく決定します。道路網は接続された格子を基礎にし、区画幅と節点を変化させます。
3. 道路と歩道の余白を確保した区画内部へ、重ならない建物を配置します。小さな区画では歪みを抑え、全建物種別に必要な地区数を確保します。
4. 道路接続、敷地外配置、公園への建物混入、建物同士・道路・鉄道との衝突を検証します。屋根の張り出し・ベランダの1ブロック分も含みます。
5. ブロック化し、プレビューと `.schem` を保存します。

実都市を1:1スケールで再現する方式ではありません。1ブロック≒1 mを基準に、256ブロックへ収まるよう道路間隔を32〜64へ圧縮します。指定比率は地区数を丸めた目標で、最小地区数や道路面積のため厳密な面積率とは一致しません。建物被覆率も目標の傾向を反映する値であり、厳密な一致は保証しません。実際の値は `report.json` の `achieved_building_coverage` で確認できます。

### プレビュー・再生成・再現

```bash
# seedを変え、別の道路・建物配置へ
citygen generate --profile examples/osm/tokyo.json --profile examples/osm/kyoto.json \
  --weights 0.6 0.4 --planner rules --seed 2027 --output output/seed-2027

# 保存済みの都市を再描画する
citygen preview --city output/japan-city/city.json --output output/map.png --scale 4

# 保存済みの計画を固定し、同じseedで再生成する
citygen generate --profile examples/osm/tokyo.json --profile examples/osm/kyoto.json \
  --weights 0.6 0.4 --plan-file output/japan-city/plan.json --seed 2026 --output output/replay

# 保存済みの全配置から建築ファイルを再出力する
citygen build --city output/japan-city/city.json --output output/rebuild
```

同じソフトウェア・依存バージョン、プロファイル、重み、計画、サイズ、シードで同じ配置と同じ `.schem` バイト列を再現します。gzipの時刻情報も固定しています。新しいOSM取得結果や新しいAI提案は変化しうるため、完全な再現には保存済みの `city.json` を使ってください。

## 5. AIによる構造化都市計画（任意）

OpenAI APIは環境変数で設定します。キーをファイルやソースに埋め込む必要はありません。`.env` の自動読み込みはしません。

```bash
export OPENAI_API_KEY="your-key"
export OPENAI_MODEL="gpt-4.1-mini"  # 任意
citygen generate --profile examples/osm/tokyo.json --profile examples/osm/kyoto.json \
  --weights 0.6 0.4 --planner ai --seed 42 --output output/ai-city
```

PowerShellでは `$env:OPENAI_API_KEY="your-key"` を使います。

- `--planner auto`（generateの既定）: キーがあればAI、なければルール。
- `--planner rules`: 常に通信なし。`demo` も常にこのモードです。
- `--planner ai`: AIを試行し、キーなし・API障害・拒否・不正JSON・範囲外の提案なら警告付きでルールへ戻ります。

AIへ送るのは混合済み指標、seed、基準計画のみです。Structured OutputsのJSON SchemaとPydanticの二段階で、道路幅・間隔、地区比率の合計、階数、スタイル、余分なフィールドなどを検証します。任意コードや任意Minecraftブロック名は受け付けません。`traditional` / `modern` / `mixed` に応じたパレット・屋根・外装を建築器が適用します。生成元は `planning_source` に記録します。

AIのAPI応答そのものは決定的ではありません。同じseedだけで同じ提案が得られる保証はなく、`plan.json` を保存して再利用してください。この作業環境にはAPIキーがないため、実API呼び出しは未検証です。成功・拒否・不正JSON・タイムアウト・キー欠損はモック通信で検証済みです。

## 6. Minecraft Java / WorldEditへの読み込み

出力は **Sponge Schematic v2 / Minecraft 1.20.1 DataVersion 3465**。NBTは `nbtlib` を使い、パレットとunsigned VarIntのBlockDataを実装しています。Xが最速、次がZ、最後がYの順です。

1. Minecraft Java 1.20.1以上と、それに対応するWorldEdit 7系を用意します。
2. `city.schem` をサーバーの `plugins/WorldEdit/schematics/`、または導入方式に応じたWorldEditのschematicsフォルダへコピーします。
3. 建築可能な空間の北西・下端となる位置で読み込みます。

```text
//schem load sponge city
//paste
```

WorldEditの版によっては `//schem load city` でも読み込めます。`//paste -a` は空気を貼り付けないため、既存地形のある場所では建物内部に地形が残ります。通常の貼り付けは空気を含む範囲を置換するので、空のテストワールドで位置を確認してください。取り消しは `//undo` です。

地盤はローカルY=0〜3、歩行面はその上です。貼り付けの基準Yを64にすると地盤上端は67になります。都市のサイズは256×256、高さは建物の階数から自動計算します。既存ワールドの起伏へ合わせる処理はありません。

住宅には段状の瓦風屋根、各建物には窓・入口・屋内照明、マンションにはベランダ、店舗には庇、学校には別配色を付けます。公園には植栽・歩道・ベンチ、道路には歩道・交差点の横断歩道・街灯を配置します。鉄道は高架複線と支持柱・動力レールを持ちます。

**実行済みの検証はNBTの保存・読み戻し・全ブロック配列の一致までです。Minecraft/WorldEdit本体をこの環境で起動して貼り付ける検証はしていません。**

## 7. テストと構成

```bash
pytest -q
ruff check src tests
```

テストは通信不要です。既知の幾何形状による面積・密度計算、OSM multipolygonの穴・二重計上、キャッシュ、取得失敗、重みとAI JSONの検証、シード再現、異なるseed、複数サイズ、極端な計画の敷地確保、接続・衝突、NBT VarIntの128以上の値、軸順序、全都市の保存・読み戻し、CLI全体を検証します。GitHub Actions設定も同梱しています。

```text
src/minecraft_city/
  analysis.py     Overpass取得・投影・数値特徴抽出・合成サンプル
  models.py       プロファイル・計画・都市のJSON契約
  planning.py     特徴混合・ルール計画・OpenAI構造化出力
  generation.py   道路網・地区・敷地・建物配置・接続/衝突検査
  preview.py      共通幾何からのPNGプレビュー
  building.py     Minecraftブロック建築
  schematic.py    Sponge v2 NBT/VarInt出力
  cli.py          コマンドライン
tests/           自動テスト
examples/osm/    実OSM分析・比較・元データキャッシュ
examples/demo/   合成サンプルの実行結果
examples/japan-city/ 実データ60:40の実行結果
```

OSMnx/GeoPandasは必須にせず、範囲を限定したOverpass取得、Shapely、PyProj、NetworkX、NumPyで必要な処理を実装しました。出力用nbtlibを含め、依存をこの環境にインストールして実行しています。

## 8. MVPの制限・未実装

- 都市生成は**手続き型アルゴリズム＋任意のLLM計画**です。都市データから訓練した生成モデルや街路の機械学習モデルは未実装です。
- 道路は幅・区画間隔・歪みを変えた接続格子が基礎。曲線道路、地形追従、河川、地下鉄、交通シミュレーション、大規模都市の分割出力は未実装です。
- 建築は種類別パラメトリック雛形です。詳細な内装、階段・エレベーター、学校の校庭、駅・ホーム、鉄道ダイヤは未実装です。2階以上には床がありますが、屋内昇降設備はありません。
- 全敷地から歩道までの歩行経路検証、立体交差の正しい交通接続、入口と道路を結ぶ専用路は未実装です。
- プレビュー・seed変更はCLIで行います。ブラウザ/Tkのインタラクティブ編集GUI、Minecraftへの直接接続、`.mcworld`/Anvil保存は未実装です。
- プレビューは敷地と地区の平面図で、Minecraftの3Dレンダリングではありません。
- 実AI APIとゲーム本体内の読み込み検証は上記の通り未実施です。

## 9. OSMライセンス・API利用条件

コードはMIT。OSM由来の元データ・派生分析データは別ライセンスとして扱います。**© OpenStreetMap contributors**、[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/)、[OSM著作権表示](https://www.openstreetmap.org/copyright)。詳細と同梱範囲は [DATA_LICENSE.md](DATA_LICENSE.md) を参照してください。

公開・再配布する地図・都市成果物にも出典を付けてください。本実装はプロファイル、都市JSON、レポート、プレビュー、schematicメタデータに出典を保持します。OSMの元データや派生データベースを配布するときはODbLの表示・継承・提供義務に従ってください。

公開Overpassサーバーには[利用に関する案内](https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances)と[資源制限](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html)があります。小範囲・キャッシュ・逐次実行を守り、429/504等の場合は時間を置くか合成サンプルを利用してください。連続した都市全域取得には公開インスタンスへ大量アクセスせず、適切な抽出データや自前インスタンスを使う設計が必要です。
