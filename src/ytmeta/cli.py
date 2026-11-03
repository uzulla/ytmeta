"""YouTube metadata editor CLI."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube"]
ROOT = Path.cwd()
CLIENT_SECRET = ROOT / "client_secret.json"
TOKEN = ROOT / "token.json"
BACKUP_DIR = ROOT / "backups"
# update 時に snippet へ引き継ぐフィールド(未指定だと消えるため)
SNIPPET_KEYS = ["title", "description", "tags", "categoryId",
                "defaultLanguage", "defaultAudioLanguage"]


def get_service():
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRET.exists():
                sys.exit(f"{CLIENT_SECRET} がありません。README の手順で OAuth クライアントJSONを置いてください。")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN.write_text(creds.to_json())
        TOKEN.chmod(0o600)
    return build("youtube", "v3", credentials=creds)


def fetch_video(yt, video_id: str) -> dict:
    res = yt.videos().list(part="snippet,status", id=video_id).execute()
    if not res["items"]:
        sys.exit(f"動画が見つかりません: {video_id}")
    return res["items"][0]


def iter_my_videos(yt, limit: int | None = None):
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    token, n = None, 0
    while True:
        res = yt.playlistItems().list(
            part="contentDetails", playlistId=uploads, maxResults=50, pageToken=token
        ).execute()
        ids = [i["contentDetails"]["videoId"] for i in res["items"]]
        if ids:
            vres = yt.videos().list(part="snippet,status", id=",".join(ids)).execute()
            for v in vres["items"]:
                yield v
                n += 1
                if limit and n >= limit:
                    return
        token = res.get("nextPageToken")
        if not token:
            return


def backup(video: dict) -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    p = BACKUP_DIR / f"{video['id']}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(video, ensure_ascii=False, indent=2))
    return p


def update_video(yt, video_id: str, changes: dict, dry_run: bool) -> None:
    video = fetch_video(yt, video_id)
    old = video["snippet"]
    snippet = {k: old[k] for k in SNIPPET_KEYS if k in old}
    new = {**snippet, **{k: v for k, v in changes.items() if v is not None}}
    diff = {k: (snippet.get(k), new[k]) for k in new if snippet.get(k) != new[k]}
    if not diff:
        print(f"{video_id}: 変更なし")
        return
    print(f"{video_id}:")
    for k, (a, b) in diff.items():
        print(f"  {k}:\n    - {a!r}\n    + {b!r}")
    if dry_run:
        print("  (dry-run: 更新しません)")
        return
    path = backup(video)
    yt.videos().update(part="snippet", body={"id": video_id, "snippet": new}).execute()
    print(f"  更新しました (backup: {path})")


def cmd_auth(args):
    get_service()
    print("認証OK:", TOKEN)


def cmd_list(args):
    yt = get_service()
    for v in iter_my_videos(yt, args.limit):
        if args.json:
            print(json.dumps(v, ensure_ascii=False))
        else:
            print(f"{v['id']}\t{v['status']['privacyStatus']}\t{v['snippet']['title']}")


def cmd_get(args):
    v = fetch_video(get_service(), args.video_id)
    print(json.dumps(v, ensure_ascii=False, indent=2))


def cmd_set(args):
    tags = None
    if args.tags is not None:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    desc = Path(args.description_file).read_text() if args.description_file else args.description
    update_video(get_service(), args.video_id,
                 {"title": args.title, "description": desc, "tags": tags}, args.dry_run)


def cmd_apply(args):
    """JSON: [{"id": "...", "title": "...", "description": "...", "tags": ["a"]}, ...]"""
    items = json.loads(Path(args.file).read_text())
    yt = get_service()
    for it in items:
        update_video(yt, it["id"],
                     {k: it.get(k) for k in ("title", "description", "tags")}, args.dry_run)


def cmd_privacy(args):
    yt = get_service()
    for vid in args.video_ids:
        video = fetch_video(yt, vid)
        st = video["status"]
        title = video["snippet"]["title"]
        if st["privacyStatus"] == args.to:
            print(f"{vid} ({title}): すでに {args.to}")
            continue
        print(f"{vid} ({title}): {st['privacyStatus']} -> {args.to}")
        if args.dry_run:
            print("  (dry-run: 更新しません)")
            continue
        path = backup(video)
        # status は未指定フィールドが初期化されうるため、現状を丸ごと送る
        new = {**st, "privacyStatus": args.to}
        new.pop("publishAt", None)
        yt.videos().update(part="status", body={"id": vid, "status": new}).execute()
        print(f"  更新しました (backup: {path})")


def cmd_kids(args):
    yt = get_service()
    value = args.answer == "yes"
    for vid in args.video_ids:
        video = fetch_video(yt, vid)
        st = video["status"]
        cur = st.get("selfDeclaredMadeForKids")
        print(f"{vid} ({video['snippet']['title']}): selfDeclaredMadeForKids {cur} -> {value}")
        if cur == value:
            continue
        if args.dry_run:
            print("  (dry-run: 更新しません)")
            continue
        path = backup(video)
        new = {**st, "selfDeclaredMadeForKids": value}
        new.pop("publishAt", None)
        yt.videos().update(part="status", body={"id": vid, "status": new}).execute()
        print(f"  更新しました (backup: {path})")


def cmd_upload(args):
    from googleapiclient.http import MediaFileUpload

    path = Path(args.file).expanduser()
    if not path.is_file():
        sys.exit(f"ファイルがありません: {path}")
    body = {
        "snippet": {"title": args.title or path.stem,
                    "description": args.description or "",
                    "tags": [t.strip() for t in (args.tags or "").split(",") if t.strip()],
                    "categoryId": args.category},
        "status": {"privacyStatus": args.privacy,
                   "selfDeclaredMadeForKids": args.kids == "yes"},
    }
    print(json.dumps(body, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("(dry-run: アップロードしません)")
        return
    yt = get_service()
    media = MediaFileUpload(str(path), chunksize=32 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"  {int(status.progress() * 100)}%", flush=True)
    print(f"アップロード完了: {resp['id']} ({resp['status']['privacyStatus']})")
    print(f"https://studio.youtube.com/video/{resp['id']}/edit")


def cmd_playlist_create(args):
    yt = get_service()
    existing = yt.playlists().list(part="snippet,status", mine=True, maxResults=50).execute()["items"]
    for pl in existing:
        if pl["snippet"]["title"] == args.title:
            print(f"同名のプレイリストが既にあります: {pl['id']} ({pl['status']['privacyStatus']})")
            return
    body = {"snippet": {"title": args.title, "description": args.description or ""},
            "status": {"privacyStatus": args.privacy}}
    print(json.dumps(body, ensure_ascii=False))
    if args.dry_run:
        print("(dry-run: 作成しません)")
        return
    r = yt.playlists().insert(part="snippet,status", body=body).execute()
    print(f"作成しました: {r['id']} ({r['status']['privacyStatus']})")
    print(f"https://www.youtube.com/playlist?list={r['id']}")


def cmd_playlist_add(args):
    """指定した順番で末尾に追加。すでに入っている動画はスキップ。"""
    yt = get_service()
    present, token = set(), None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=args.playlist_id,
                                    maxResults=50, pageToken=token).execute()
        present |= {i["contentDetails"]["videoId"] for i in r["items"]}
        token = r.get("nextPageToken")
        if not token:
            break
    for vid in args.video_ids:
        title = fetch_video(yt, vid)["snippet"]["title"]
        if vid in present:
            print(f"{vid}: 追加済みのためスキップ ({title})")
            continue
        print(f"{vid}: 追加 ({title})")
        if args.dry_run:
            continue
        yt.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": args.playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
    if args.dry_run:
        print("(dry-run: 追加しません)")


def cmd_whoami(args):
    if not TOKEN.exists():
        sys.exit("未ログインです: mise run login")
    yt = get_service()
    ch = yt.channels().list(part="snippet,statistics", mine=True).execute()["items"][0]
    print(f"{ch['snippet']['title']} ({ch['id']}) 動画数={ch['statistics'].get('videoCount')}")


def cmd_logout(args):
    if TOKEN.exists():
        TOKEN.unlink()
        print("token.json を削除しました")
    else:
        print("ログインしていません")


def cmd_export(args):
    """編集用JSON(apply にそのまま渡せる形式)を出力"""
    yt = get_service()
    items = [{"id": v["id"], "title": v["snippet"]["title"],
              "description": v["snippet"]["description"],
              "tags": v["snippet"].get("tags", [])}
             for v in iter_my_videos(yt, args.limit)]
    text = json.dumps(items, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text)
        print(f"{len(items)}件を {args.output} に書き出しました")
    else:
        print(text)


def cmd_backups(args):
    for f in sorted(BACKUP_DIR.glob("*.json")) if BACKUP_DIR.exists() else []:
        print(f"{f.name}\t{json.loads(f.read_text())['snippet']['title']}")


def cmd_restore(args):
    v = json.loads(Path(args.file).read_text())
    sn = v["snippet"]
    update_video(get_service(), v["id"],
                 {k: sn.get(k) for k in ("title", "description", "tags")}, args.dry_run)


def main():
    p = argparse.ArgumentParser(prog="ytmeta", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="OAuth認証").set_defaults(fn=cmd_auth)
    # alias: login

    s = sub.add_parser("list", help="自分の動画一覧")
    s.add_argument("-n", "--limit", type=int)
    s.add_argument("--json", action="store_true", help="1行1JSONで出力")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("get", help="動画1件の詳細(JSON)")
    s.add_argument("video_id")
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("set", help="タイトル/説明/タグを更新")
    s.add_argument("video_id")
    s.add_argument("--title")
    s.add_argument("--description")
    s.add_argument("--description-file", help="説明文を読み込むファイル")
    s.add_argument("--tags", help="カンマ区切り(空文字でタグ全削除)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("apply", help="JSONファイルで一括更新")
    s.add_argument("file")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("privacy", help="公開設定を変更")
    s.add_argument("video_ids", nargs="+")
    s.add_argument("--to", required=True, choices=["public", "unlisted", "private"])
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_privacy)

    s = sub.add_parser("kids", help="子供向けか否かを申告")
    s.add_argument("video_ids", nargs="+")
    s.add_argument("--answer", required=True, choices=["yes", "no"], help="yes=子供向け")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_kids)

    s = sub.add_parser("upload", help="動画をアップロード (insert: 1600クォータ)")
    s.add_argument("file")
    s.add_argument("--title")
    s.add_argument("--description")
    s.add_argument("--tags", help="カンマ区切り")
    s.add_argument("--category", default="22", help="既定 22 (People & Blogs)")
    s.add_argument("--privacy", default="private", choices=["public", "unlisted", "private"])
    s.add_argument("--kids", default="no", choices=["yes", "no"], help="子供向けか (既定 no)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_upload)

    s = sub.add_parser("playlist-create", help="プレイリストを作成")
    s.add_argument("title")
    s.add_argument("--description")
    s.add_argument("--privacy", default="private", choices=["public", "unlisted", "private"])
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_playlist_create)

    s = sub.add_parser("playlist-add", help="プレイリストに動画を追加(指定順・末尾へ)")
    s.add_argument("playlist_id")
    s.add_argument("video_ids", nargs="+")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_playlist_add)

    sub.add_parser("whoami", help="ログイン中のチャンネル表示").set_defaults(fn=cmd_whoami)
    sub.add_parser("logout", help="token.json を削除").set_defaults(fn=cmd_logout)
    sub.add_parser("backups", help="バックアップ一覧").set_defaults(fn=cmd_backups)

    s = sub.add_parser("export", help="編集用JSONを出力")
    s.add_argument("-n", "--limit", type=int)
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("restore", help="バックアップから復元")
    s.add_argument("file")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_restore)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
