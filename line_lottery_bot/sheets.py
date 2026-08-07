"""Google Sheets への自動書き込み (gspread + google-auth).

エントリー登録・抽選結果を Google スプレッドシートへ追記する。
未設定 (認証情報 or シートID が無い) 場合は自動で無効化され、
BOT 本体の動作を妨げない (すべての公開関数は安全に no-op する)。

必要な環境変数:
  GOOGLE_SHEET_ID              対象スプレッドシートのキー (URL の /d/<ここ>/ 部分)
  GOOGLE_SERVICE_ACCOUNT_JSON  サービスアカウントの JSON 文字列 (推奨: Render 用)
    もしくは
  GOOGLE_APPLICATION_CREDENTIALS  サービスアカウント JSON ファイルへのパス

事前準備:
  1. GCP でサービスアカウントを作成し、JSON キーを発行
  2. Google Sheets API を有効化
  3. 対象スプレッドシートを、サービスアカウントのメールアドレスに
     「編集者」権限で共有する
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

# 表示用タイムスタンプは JST に統一する
_JST = timezone(timedelta(hours=9))

ENTRY_SHEET = "エントリー一覧"
RESULT_SHEET = "抽選結果"
ENTRY_HEADERS = ["応募日時", "LINE表示名", "user ID"]
RESULT_HEADERS = ["抽選日時", "当選者名", "user ID", "当落"]

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 遅延初期化用のキャッシュ
_client = None
_spreadsheet = None
_enabled: bool | None = None


def _now_jst_str() -> str:
    return datetime.now(_JST).strftime("%Y-%m-%d %H:%M:%S")


def _load_credentials():
    """サービスアカウント認証情報を環境変数から読み込む。無ければ None。"""
    from google.oauth2.service_account import Credentials

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=_SCOPES)

    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path and os.path.exists(path):
        return Credentials.from_service_account_file(path, scopes=_SCOPES)

    return None


def _init() -> bool:
    """初回のみ認証してスプレッドシートを開く。成否をキャッシュして返す。"""
    global _client, _spreadsheet, _enabled

    if _enabled is not None:
        return _enabled

    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    if not sheet_id:
        _enabled = False
        return False

    try:
        import gspread

        creds = _load_credentials()
        if creds is None:
            print("[sheets] 認証情報が未設定のため Sheets 連携を無効化します。")
            _enabled = False
            return False

        _client = gspread.authorize(creds)
        _spreadsheet = _client.open_by_key(sheet_id)
        _enabled = True
        print("[sheets] Google Sheets 連携を有効化しました。")
    except Exception as e:  # 認証失敗・権限不足・API 無効など
        print(f"[sheets] 初期化に失敗しました (連携を無効化): {e}")
        _enabled = False

    return _enabled


def is_enabled() -> bool:
    return _init()


def _get_worksheet(title: str, headers: list[str]):
    """ワークシートを取得。無ければ作成し、ヘッダー行を用意する。"""
    import gspread

    try:
        ws = _spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = _spreadsheet.add_worksheet(
            title=title, rows=1000, cols=max(10, len(headers))
        )
        ws.append_row(headers, value_input_option="USER_ENTERED")
        return ws

    # 既存シートにヘッダーが無ければ追加
    if not ws.row_values(1):
        ws.append_row(headers, value_input_option="USER_ENTERED")
    return ws


def append_entry(display_name: str, user_id: str) -> None:
    """シート1「エントリー一覧」に1行追記する。失敗しても例外を投げない。"""
    if not _init():
        return
    try:
        ws = _get_worksheet(ENTRY_SHEET, ENTRY_HEADERS)
        ws.append_row(
            [_now_jst_str(), display_name or "", user_id],
            value_input_option="USER_ENTERED",
        )
    except Exception as e:
        print(f"[sheets] append_entry 失敗: {e}")


def append_results(rows: list[tuple[str, str, str]]) -> None:
    """シート2「抽選結果」に複数行追記する。

    rows: (当選者名, user ID, 当落) のタプルのリスト。
    当落は「当選」または「落選」を想定。失敗しても例外を投げない。
    """
    if not _init():
        return
    if not rows:
        return
    try:
        ws = _get_worksheet(RESULT_SHEET, RESULT_HEADERS)
        ts = _now_jst_str()
        data = [[ts, name or "", uid, status] for (name, uid, status) in rows]
        ws.append_rows(data, value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"[sheets] append_results 失敗: {e}")
