# ytmeta

自分の YouTube 動画のメタデータ(タイトル・説明・タグ・公開設定・子供向け申告)の操作と、動画のアップロードを行う CLI。
YouTube Data API v3 を使う。依存管理は mise + uv。

## セットアップ

### 1. ツールと依存
```
mise trust && mise install
mise run setup
```

### 2. Google Cloud
```
gcloud projects create <PROJECT_ID> --name=youtube-meta-editor
gcloud services enable youtube.googleapis.com --project=<PROJECT_ID>
```
以降はコンソールでの手作業(`gcloud` では作れない)。

1. **OAuth 同意画面**: 対象は「外部」。「対象(Audience)」ページで、**自分のアカウントをテストユーザーに追加**する
   (未追加だと `access_denied` / 「審査プロセスを完了していません」になる)。
2. **OAuth クライアント ID**: 種類は「デスクトップ アプリ」。
   「このクライアントを AI エージェントとして指定」は**チェックしない**(普通のCLIなので不要)。
3. JSON をダウンロードし、`client_secret.json` としてこのディレクトリに置く(`chmod 600` 推奨)。

### 3. ログイン
```
mise run login     # ブラウザで認可。token.json が生成される
mise run whoami    # チャンネル名が出ればOK
```
同意画面が「テスト」状態だと、リフレッシュトークンは **7日で失効**する。切れたら `mise run login` をやり直す。

## 使い方
**プロジェクトのディレクトリで実行すること**(`client_secret.json` / `token.json` / `backups/` はカレントディレクトリ基準)。
`mise run <タスク> <引数>` の形で、引数はそのまま CLI に渡される。

### 認証
| タスク | 内容 |
|---|---|
| `login` | ブラウザでOAuthログイン |
| `logout` | `token.json` を削除 |
| `whoami` | ログイン中のチャンネルを表示 |

### 参照
| タスク | 例 |
|---|---|
| `list` | `mise run list -n 20` / `--json` で1行1JSON |
| `get` | `mise run get VIDEO_ID` |
| `export` | `mise run export -o videos.json` |
| `backups` | `mise run backups` |

### 更新
| タスク | 例 |
|---|---|
| `set` | `mise run set VIDEO_ID --title "新タイトル" --tags "a,b" --description-file desc.txt` |
| `preview` | `set` の dry-run。差分だけ表示 |
| `apply` | `mise run apply videos.json` (JSON一括更新) |
| `apply-dry` | `apply` の dry-run |
| `privacy` | `mise run privacy ID1 ID2 --to unlisted [--dry-run]` (`public` / `unlisted` / `private`) |
| `kids` | `mise run kids ID1 ID2 --answer no [--dry-run]` (`yes` = 子供向け) |
| `restore` | `mise run restore backups/xxx.json` (タイトル・説明・タグのみ復元。公開設定は対象外) |

`videos.json` / `changes.json` の形式(省略したキーは変更しない):
```json
[{"id": "VIDEO_ID", "title": "...", "description": "...", "tags": ["a"]}]
```

### アップロード
| タスク | 例 |
|---|---|
| `upload` | `mise run upload FILE --title X [--description X] [--tags a,b] [--privacy unlisted] [--kids no] [--dry-run]` |

既定は `--privacy private` / `--kids no` / カテゴリ 22。

### プレイリスト
| タスク | 例 |
|---|---|
| `playlist-create` | `mise run playlist-create "タイトル" [--privacy unlisted] [--description X] [--dry-run]` (同名があれば作らない) |
| `playlist-add` | `mise run playlist-add PLAYLIST_ID ID1 ID2 ... [--dry-run]` (指定順に末尾へ追加。追加済みはスキップ) |

### その他
| タスク | 内容 |
|---|---|
| `yt` | CLI を直接実行(例: `mise run yt list`) |
| `help` | CLI のヘルプ |

典型フロー: `login` → `export -o videos.json` → JSON編集 → `apply-dry videos.json` → `apply videos.json`

## 安全策
- 更新系は現在の値を取得してから、指定した項目だけ差し替えて送る(`videos.update` は未指定の項目を消すため)。
- 更新・公開設定変更・子供向け申告の直前に、元の状態を `backups/` へ JSON 保存する。
- `--dry-run` / `preview` / `apply-dry` で、更新せず差分だけ確認できる。
- `client_secret.json` / `token.json` / `backups/` は `.gitignore` 済み。公開リポジトリにする場合も入れないこと。

## クォータ
1日 10,000 ユニット(太平洋時間 0 時にリセット)。

| 操作 | コスト |
|---|---|
| `list` / `get` / `export` | 1 |
| `set` / `apply` / `privacy` / `kids` / `restore` | 50 |
| `upload` | 1,600(1日あたり約6本まで) |
| `playlist-create` / `playlist-add` | 50(追加は1本ごと) |

引き上げには YouTube API Services の監査(Audit and Quota Extension Form)が必要。

## 既知の挙動
- **Studio の「ドラフト」表示**: Studio のアップロードウィザードを最後まで終えていない動画は、API で公開設定や子供向けを設定しても「下書き」のまま。
  API から状態は変えられない(0059 を Studio で保存すると解消、API の項目に差は無かった)。
  `upload` で上げた動画はドラフトにならなかった。
- `whoami` の動画数は公開動画のみのカウント(非公開は含まれない)。
- 監査を受けていない API プロジェクトからのアップロードは、非公開に固定されることがある。
# ytmeta
