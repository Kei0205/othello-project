"""
set_owner_password.py
オーナー（全機能の画面を使う人）の名前とパスワードを、コマンドで設定する。

アプリの画面のパスワード設定は「アプリを動かしているPCから開いたとき」にしか出ない。
サーバーに置いて OTHELLO_AUTO_OWNER=0 で動かすときは、その画面が出ないので、これで設定する。

    python set_owner_password.py

app.py と同じ形式（PBKDF2 のソルト付きハッシュ。平文は保存しない）で owner_auth.json に書く。
設定し直すと、ログインを記憶していた端末はすべてログアウトになる。
"""

import getpass
import hashlib
import json
import os
import sys

OWNER_AUTH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "owner_auth.json")


def save_owner_password(username: str, password: str, path: str = OWNER_AUTH_PATH) -> None:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump({"username": username.strip(), "salt": salt.hex(), "hash": digest.hex(), "sessions": []}, fp, ensure_ascii=False)


def main() -> None:
    username = input("オーナー名: ").strip()
    if not username:
        sys.exit("オーナー名を入力してください。")
    password = getpass.getpass("パスワード（4文字以上）: ")
    if len(password) < 4:
        sys.exit("パスワードは4文字以上にしてください。")
    if getpass.getpass("もう一度入力: ") != password:
        sys.exit("パスワードが一致しません。")
    save_owner_password(username, password)
    print(f"保存しました → {OWNER_AUTH_PATH}")


if __name__ == "__main__":
    main()
