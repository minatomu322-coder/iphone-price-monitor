"""LINE 抽選管理BOT (Flask + LINE Messaging API + PostgreSQL).

対応コマンド (テキストメッセージ):
  一般ユーザー
    応募            … 抽選にエントリー (1人1回・重複不可)
    ヘルプ / help    … 使い方を表示

  管理者 (ADMIN_USER_IDS に含まれる user_id のみ)
    締め切り        … 応募受付を終了する
    再開            … 応募受付を再開する
    抽選 3          … 3名をランダム選出して結果を返信する
    当選通知        … 当選者全員へ push message を送信する
    応募一覧        … エントリー済みユーザーの一覧
    当選者一覧      … 当選者の一覧
    リセット        … 全データ (応募・当選・受付状態) を初期化

環境変数:
  CHANNEL_SECRET        LINE チャネルシークレット (必須)
  CHANNEL_ACCESS_TOKEN  LINE チャネルアクセストークン (必須)
  ADMIN_USER_IDS        管理者の LINE userId をカンマ区切りで指定 (必須)
  DATABASE_URL          PostgreSQL 接続文字列。未指定ならローカル SQLite にフォールバック
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timezone

from flask import Flask, abort, request
from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

import sheets

# --------------------------------------------------------------------------- #
# 設定
# --------------------------------------------------------------------------- #
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "")
ADMIN_USER_IDS = {
    uid.strip()
    for uid in os.environ.get("ADMIN_USER_IDS", "").split(",")
    if uid.strip()
}

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    # 起動時に気づけるよう明示的に落とす (Render のログに出る)
    raise RuntimeError(
        "CHANNEL_SECRET と CHANNEL_ACCESS_TOKEN を環境変数に設定してください。"
    )


def _normalize_db_url(url: str) -> str:
    """Render の PostgreSQL は 'postgres://' 形式で払い出されるが、
    SQLAlchemy は 'postgresql://' を要求するため変換する。"""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = _normalize_db_url(
    os.environ.get("DATABASE_URL", "sqlite:///lottery.db")
)

# --------------------------------------------------------------------------- #
# DB モデル
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Entry(Base):
    """応募者。user_id を一意にして重複応募を防ぐ。"""

    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Winner(Base):
    """当選者。"""

    __tablename__ = "winners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Setting(Base):
    """受付状態などのキーバリュー設定。"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
Base.metadata.create_all(engine)


# --------------------------------------------------------------------------- #
# 受付状態ヘルパー
# --------------------------------------------------------------------------- #
_ACCEPTING_KEY = "accepting"


def is_accepting(session: Session) -> bool:
    row = session.get(Setting, _ACCEPTING_KEY)
    if row is None:
        # 初期状態は受付中
        return True
    return row.value == "1"


def set_accepting(session: Session, accepting: bool) -> None:
    row = session.get(Setting, _ACCEPTING_KEY)
    if row is None:
        row = Setting(key=_ACCEPTING_KEY, value="1" if accepting else "0")
        session.add(row)
    else:
        row.value = "1" if accepting else "0"


# --------------------------------------------------------------------------- #
# LINE SDK
# --------------------------------------------------------------------------- #
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

app = Flask(__name__)


def _get_display_name(user_id: str) -> str:
    """プロフィール取得。友だち未追加などで失敗しても空文字で継続する。"""
    try:
        with ApiClient(configuration) as api_client:
            profile = MessagingApi(api_client).get_profile(user_id)
            return profile.display_name or ""
    except Exception:
        return ""


def _reply(reply_token: str, text: str) -> None:
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )


def _push(user_id: str, text: str) -> None:
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=user_id, messages=[TextMessage(text=text)])
        )


# --------------------------------------------------------------------------- #
# コマンド処理
# --------------------------------------------------------------------------- #
HELP_TEXT = (
    "【抽選BOT 使い方】\n"
    "・応募 … 抽選にエントリーします (お一人様1回)\n"
    "・ヘルプ … この案内を表示します\n\n"
    "抽選の実行・締め切り・通知は主催者が行います。"
)

ADMIN_HELP_TEXT = (
    "【管理者コマンド】\n"
    "・締め切り / 再開\n"
    "・抽選 N (例: 抽選 3)\n"
    "・当選通知\n"
    "・応募一覧 / 当選者一覧\n"
    "・リセット"
)


def handle_entry(session: Session, user_id: str) -> str:
    """応募登録。重複・受付終了をチェックする。"""
    if not is_accepting(session):
        return "現在、応募の受付は終了しています。ご参加ありがとうございました。"

    existing = session.scalar(select(Entry).where(Entry.user_id == user_id))
    if existing is not None:
        return "すでに応募済みです。抽選結果をお待ちください。"

    name = _get_display_name(user_id)
    session.add(Entry(user_id=user_id, display_name=name))
    session.commit()
    # Google Sheets「エントリー一覧」へ追記 (未設定なら no-op)
    sheets.append_entry(name, user_id)
    count = session.scalar(select(func.count()).select_from(Entry)) or 0
    return f"応募を受け付けました！(現在の応募者数: {count}名)\n抽選結果をお待ちください。"


def handle_close(session: Session) -> str:
    set_accepting(session, False)
    session.commit()
    return "応募の受付を締め切りました。"


def handle_reopen(session: Session) -> str:
    set_accepting(session, True)
    session.commit()
    return "応募の受付を再開しました。"


def handle_draw(session: Session, num: int) -> str:
    """N名をランダム選出して winners に保存する。"""
    if num <= 0:
        return "抽選人数は1以上を指定してください。(例: 抽選 3)"

    entries = list(session.scalars(select(Entry)).all())
    if not entries:
        return "応募者がいません。抽選を実行できません。"

    # 再抽選できるよう既存の当選者はクリアする
    session.query(Winner).delete()
    session.flush()

    num = min(num, len(entries))
    chosen = random.sample(entries, num)
    for e in chosen:
        session.add(Winner(user_id=e.user_id, display_name=e.display_name))
    session.commit()

    # Google Sheets「抽選結果」へ全応募者の当落を追記 (未設定なら no-op)
    chosen_ids = {e.user_id for e in chosen}
    sheets.append_results(
        [
            (
                e.display_name,
                e.user_id,
                "当選" if e.user_id in chosen_ids else "落選",
            )
            for e in entries
        ]
    )

    lines = [f"🎉 抽選結果 ({num}名当選) 🎉", ""]
    for i, w in enumerate(chosen, start=1):
        name = w.display_name or "(名前非公開)"
        lines.append(f"{i}. {name}")
    lines.append("")
    lines.append("「当選通知」で当選者へ個別通知を送れます。")
    return "\n".join(lines)


def handle_notify(session: Session) -> str:
    """当選者全員へ push 通知を送る。"""
    winners = list(session.scalars(select(Winner)).all())
    if not winners:
        return "当選者がいません。先に「抽選 N」を実行してください。"

    sent, failed = 0, 0
    for w in winners:
        try:
            _push(
                w.user_id,
                "🎉ご当選おめでとうございます！🎉\n"
                "抽選の結果、あなたが当選しました。詳細は追ってご連絡します。",
            )
            sent += 1
        except Exception:
            failed += 1

    msg = f"当選通知を送信しました。成功: {sent}名"
    if failed:
        msg += f" / 失敗: {failed}名 (ブロック等の可能性)"
    return msg


def handle_list_entries(session: Session) -> str:
    entries = list(session.scalars(select(Entry).order_by(Entry.created_at)).all())
    if not entries:
        return "応募者はまだいません。"
    lines = [f"【応募者一覧】 {len(entries)}名", ""]
    for i, e in enumerate(entries, start=1):
        lines.append(f"{i}. {e.display_name or '(名前非公開)'}")
    return "\n".join(lines)


def handle_list_winners(session: Session) -> str:
    winners = list(session.scalars(select(Winner).order_by(Winner.created_at)).all())
    if not winners:
        return "当選者はまだいません。"
    lines = [f"【当選者一覧】 {len(winners)}名", ""]
    for i, w in enumerate(winners, start=1):
        lines.append(f"{i}. {w.display_name or '(名前非公開)'}")
    return "\n".join(lines)


def handle_reset(session: Session) -> str:
    session.query(Winner).delete()
    session.query(Entry).delete()
    set_accepting(session, True)
    session.commit()
    return "全データを初期化しました。受付中の状態に戻しました。"


def process_command(text: str, user_id: str) -> str:
    """テキストを解釈して応答文字列を返す。"""
    text = text.strip()
    is_admin = user_id in ADMIN_USER_IDS

    with Session(engine) as session:
        # --- 一般コマンド ---
        if text == "応募":
            return handle_entry(session, user_id)
        if text in ("ヘルプ", "help", "ヘルプ表示"):
            base = HELP_TEXT
            if is_admin:
                base += "\n\n" + ADMIN_HELP_TEXT
            return base

        # --- 管理者コマンド ---
        if text in ("締め切り", "締切", "受付終了"):
            if not is_admin:
                return "この操作は主催者のみ実行できます。"
            return handle_close(session)

        if text in ("再開", "受付再開"):
            if not is_admin:
                return "この操作は主催者のみ実行できます。"
            return handle_reopen(session)

        if text.startswith("抽選"):
            if not is_admin:
                return "この操作は主催者のみ実行できます。"
            parts = text.split()
            num = 1
            if len(parts) >= 2:
                try:
                    num = int(parts[1])
                except ValueError:
                    return "人数の指定が不正です。(例: 抽選 3)"
            return handle_draw(session, num)

        if text in ("当選通知", "通知"):
            if not is_admin:
                return "この操作は主催者のみ実行できます。"
            return handle_notify(session)

        if text in ("応募一覧", "エントリー一覧"):
            if not is_admin:
                return "この操作は主催者のみ実行できます。"
            return handle_list_entries(session)

        if text in ("当選者一覧", "当選一覧"):
            if not is_admin:
                return "この操作は主催者のみ実行できます。"
            return handle_list_winners(session)

        if text in ("リセット", "初期化"):
            if not is_admin:
                return "この操作は主催者のみ実行できます。"
            return handle_reset(session)

    # 不明なメッセージ
    return "コマンドが認識できませんでした。「ヘルプ」と送ると使い方を表示します。"


# --------------------------------------------------------------------------- #
# Flask ルーティング
# --------------------------------------------------------------------------- #
@app.route("/", methods=["GET"])
def health() -> str:
    """Render のヘルスチェック / 動作確認用。"""
    return "LINE lottery bot is running."


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event: MessageEvent) -> None:
    user_id = event.source.user_id or ""
    reply_text = process_command(event.message.text, user_id)
    _reply(event.reply_token, reply_text)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
