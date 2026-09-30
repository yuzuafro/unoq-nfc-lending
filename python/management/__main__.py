# Phase 2: run Management on its own, e.g. `python -m management` or via the Dockerfile.
# `python -m management reset-password` resets a forgotten admin password (see README).
import argparse
import logging
import secrets
import sys

from .app import Backups, _write_secret, serve
from .config import Settings
from .db import make_engine, make_session_factory
from .events import EventHub
from .service import Service, ServiceError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def reset_password(args: argparse.Namespace) -> int:
    settings = Settings()
    engine = make_engine(settings.database_url)
    service = Service(make_session_factory(engine), EventHub())
    if settings.is_sqlite:
        dest = Backups(engine.url.database, settings.data_dir / "backups", settings.backup_generations).snapshot()
        print(f"バックアップ: {dest}")
    password = args.password or secrets.token_urlsafe(9)
    try:
        username = service.reset_password(password, args.username)
    except ServiceError as e:
        print(f"エラー: {e.message}", file=sys.stderr)
        return 1
    print(f"管理者 '{username}' のパスワードをリセットしました")
    if not args.password:
        pw_file = settings.data_dir / "admin_reset_password.txt"
        _write_secret(pw_file, password)
        print(f"一時パスワード: {pw_file}（ログイン後に［設定］で変更し、このファイルは削除してください）")
    return 0


parser = argparse.ArgumentParser(prog="python -m management")
sub = parser.add_subparsers(dest="command")
p = sub.add_parser("reset-password", help="管理者パスワードをリセットする")
p.add_argument("--username", help="対象の管理者（管理者が1人なら省略可）")
p.add_argument("--password", help="新しいパスワード（省略時はランダムに生成してファイルに保存）")
args = parser.parse_args()

if args.command == "reset-password":
    sys.exit(reset_password(args))
serve()
