# iPhone向け SAPニュース自動抽出システム

## これは何か
「昨日公開されたSAP関連の記事」を自動で拾い、以下3形式に吐き出します。

- `output/sap_news.md` : iPhoneで見やすいMarkdownレポート
- `output/sap_news.csv` : Excel/スプレッドシート取込用
- `output/sap_news.json` : システム連携用

加えて、`SLACK_WEBHOOK_URL` を設定するとSlackにも通知できます。

## 想定ユースケース
- 毎朝、前日のSAPニュースだけ確認したい
- iPhoneでサッと見たい
- あとでExcelやNotionに流したい

## 取得元
- SAP News Center RSS
- Google News RSS の SAP 検索

## ローカル実行
```bash
python3 fetch_sap_news.py
```

特定日を指定する場合:
```bash
TARGET_DATE_JST=2026-03-23 python3 fetch_sap_news.py
```

## Slack通知を使う場合
```bash
SLACK_WEBHOOK_URL="https://hooks.slack.com/services/xxx/yyy/zzz" python3 fetch_sap_news.py
```

## GitHub Actionsでの自動実行
このリポジトリをGitHubに置けば、`.github/workflows/daily_sap_news.yml` が毎日JST朝に動きます。

実行後は:
- `output/` にファイル生成
- 生成物を自動コミット

そのため、iPhoneのGitHubアプリまたはSafariからそのまま確認できます。

## iPhoneで一番使いやすい運用
おすすめは次のどれかです。

### 1. GitHub Pages / GitHubアプリで見る
最小構成。無料で始めやすいです。

### 2. Slackに飛ばす
通知が来るので見逃しにくいです。

### 3. NotionやGoogle Sheetsに二次連携する
CSV/JSONを別ジョブで取り込めます。

## 補足
- 「昨日」は日本時間(JST)基準です
- RSS由来なので、同一記事が別媒体で重複する場合があります
- Google News由来の記事は媒体側URLへ正規化して保存します
