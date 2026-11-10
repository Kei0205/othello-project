# 🎮 Othello AI & Coaching System

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.66%2B-FF4B4B.svg)](https://streamlit.io/)
[![Engine](https://img.shields.io/badge/Engine-Egaroucid%20%26%20AlphaBeta-green.svg)](https://github.com/Nyanyan/Egaroucid)

高精度な探索エンジン（Egaroucid連携 ＆ 組み込みAlpha-Beta）、戦術的説明可能AI（XAI）、AIコーチング機能、レート算出、オンライン対戦を備えたオセロ対戦・解析システムです。

Webフロントエンド（Streamlit）として動作するだけでなく、対局ロジック・形勢評価・戦術解析・コーチングエンジンを**外部Webサイトや独自API（FastAPI, Express等）にモジュールとして簡単に組み込める**設計になっています。

---

## 📌 このリポジトリを参考にして、似たサイトを作る方へ

このリポジトリは、作者が自分用に作ったオセロのコーチングアプリです。そのまま運用するためのものというより、**似たサイトを作るときの参考（設計・コードの見本）**として公開しています。MIT ライセンスなので、コードは自由に使って・変えて大丈夫です（[LICENSE](LICENSE)）。

- **そのまま使い回しやすい部分**（Streamlit に依存しない純粋な Python）
  - `othello.py`: ルール（合法手・反転・パス・終局）と、1手ごとの特徴量（開放度・中割り・危険マスなど）
  - `engine.py`: AI の着手と形勢判断（組み込みエンジン／Egaroucid 連携）、対局全体の解析
  - `tactics.py`・`line_compare.py`: 悪手の「なぜ」を戦術用語で説明する部分
  - `rating.py`: レーティングと段級位の目安
  - 使い方は下の「[他サイト・他サービスへの組み込みガイド](#-他サイト他サービスへの組み込みガイド)」にあります。
- **画面の作り方の参考**: `app.py`（Streamlit）
  - 盤面のアニメーション（`OTHELLO_BOARD_COMPONENT`。自分の手を先に描いて、サーバーの結果と食い違わないようにする工夫あり）
  - 終局後の解析を別スレッドで回して、画面を止めない仕組み（`bg_jobs.py`）
  - 対局 → 振り返り → 学習 → 対局 のタブ構成（`nav.py`）
- **含まれていないもの**
  - 「📚 学習」タブの記事データと、その取り込み・表示のコード（外部サイトの記事を使っているため非公開）。無くてもアプリは動き、学習タブには案内だけが出ます。
  - 対局記録の DB・オーナーのパスワード・API キー（各自の環境で作られます）
  - Egaroucid の実行ファイル（[`egaroucid_console/README.md`](egaroucid_console/README.md) を参照。Egaroucid は GPL-3.0）
- **オーナー／ゲストの仕組み**（下の「オーナー画面とゲスト画面」「サーバーに置いて公開するとき」）は、作者が自宅のPCで使い、友達をゲストとして招くための作りです。似たサイトを作るときは、ログインの仕組みを自分のサイトに合わせて作り直すのがおすすめです。

---

## 🌟 主な機能

1. **オセロ対戦コア (`othello.py`)**
   - 8×8 盤面の合法手判定、反転処理、自動パス判定、終局判定
   - 座標系相互変換（`a1`〜`h8` ↔ `(row, col)`）
   - 着手特徴量の即時抽出（開放度、相手着手可能数、中割り、危険マス等）

2. **ハイブリッド探索・形勢解析エンジン (`engine.py`)**
   - **Egaroucid連携**: 世界最強クラスのオセロAI「Egaroucid for Console」と常駐サブプロセスIPCで高速通信（Lv.0〜20、ミリ秒単位の完全読み・形勢スコア算出）。
   - **組み込みAlpha-Betaエンジン**: 外部バイナリがない環境（Linux/Mac/クラウドコンテナ等）でも純粋なPythonのみで自律動作。
   - **全手解析パイプライン**: 終局後に1手目から最終手までの石差スコア推移（±64）を算出し、悪手（Blunder）や疑問手を自動抽出。

3. **戦術的説明可能AI (XAI) (`tactics.py`, `line_compare.py`)**
   - 単なる「評価値が-6落ちた」という数値ではなく、**なぜそれが悪手なのか**をオセロ戦術理論に基づいて言語化：
     - **5大戦術因子**: 開放度（相手に与える手数）、中割り（内側の石のみ取る良手）、確定石、モビリティ変化、危険マス（C打ち・X打ち）
     - **辺の形・戦術パターン**: ウイング（Wing）、山（Mountain）、ブロック（Block）、辺の罠、爆弾
     - **フェーズ適応**: 序盤・中盤・終盤で重み付けを動的変更
   - **読み筋比較（Line Compare）**: 「実戦手 vs 最善手」および「実戦手 vs 迷い手」の分岐を先読みし、数手先の優劣の理由を提示。

4. **AIコーチングシステム (`coach.py`)**
   - 悪手・緩手を自動診断し、プレイヤーのレベルに応じたアドバイス文言を生成。
   - Google Gemini API連携により、対局全体の総評や具体的な改善ポイントを自然言語でフィードバック。

5. **レーティング ＆ 成長分析 (`rating.py`)**
   - Glicko-2 / Elo に基づくプレイヤーレート自動算出。
   - 悪手率、序盤定石精度、終盤精度などの指標からプレイヤーの強さを診断。

6. **オンライン対戦ルーム (`online_rooms.py`)**
   - 招待コードによる1対1のオンライン／LANリアルタイム対戦。

7. **インタラクティブWeb UI (`app.py`, `nav.py`)**
   - 盤面クリック着手、合法手ハイライト、形勢グラフ（Plotly）、1手ごとの盤面巻き戻し・分岐検討。

---

## 📂 ディレクトリ・ファイル構成

```
.
├── app.py                  # Streamlit Webアプリケーション本体（画面描画・UIステート管理）
├── nav.py                  # タブナビゲーション・画面遷移管理
├── othello.py              # オセロ盤面管理・ルール・着手特徴量抽出
├── engine.py               # Egaroucid IPCラッパー ＆ 組み込みAlphaBeta探索エンジン
├── tactics.py              # 戦術パターン認識・5大戦術因子・XAI解説ロジック
├── line_compare.py         # 読み筋比較（実戦 vs 最善手 / 迷い手）ロジック
├── coach.py                # AIコーチングアドバイス生成・Geminiプロンプト連携
├── rating.py               # レーティング算出・パフォーマンス診断
├── online_rooms.py         # オンライン対局ルーム管理
├── db.py                   # SQLite3 永続化（対局履歴・悪手・ユーザー成績）
├── set_owner_password.py   # オーナーの名前とパスワードをコマンドで設定（サーバーに置くとき用）
├── egaroucid_console/      # Egaroucid バイナリ配置フォルダ（README.md参照）
├── .streamlit/             # Streamlit 設定（UIテーマ色等）
├── requirements.txt        # 依存Pythonライブラリ一覧
└── README.md               # 本ドキュメント
```

---

## 🚀 クイックスタート

### 1. 必要要件
- Python 3.11 以上（Streamlit 1.66 の要件）
- Windows / macOS / Linux

### 2. インストール
```bash
git clone https://github.com/Kei0205/othello-project.git
cd othello-project
pip install -r requirements.txt
```

### 3. アプリ起動
```bash
streamlit run app.py
```
ブラウザで `http://localhost:8501` が開きます。

※ Egaroucid バイナリがない場合でも、自動的に組み込み Alpha-Beta エンジンに切り替わって動作します。Egaroucid（高速SIMD版）を利用したい場合は [`egaroucid_console/README.md`](egaroucid_console/README.md) を参照してください。

### 4. オーナー画面とゲスト画面
- **オーナー画面**（全機能: 振り返り・成長・対局の削除・設定）: アプリを動かしているPCから開くと自動でこの画面になります。
- **ゲスト画面**（Wi-Fi対戦だけ）: ほかの端末から開いた人はこの画面になります。オーナーは、ゲスト画面の下の「オーナーとしてログイン」から、パスワードでオーナー画面に入れます。

---

## 🌐 このアプリをそのままサーバーに置いて公開するとき

自分のPCで使うときは何もしなくて大丈夫です。**このアプリをそのままサーバーに置いてほかの人に公開する場合は、次の2つを必ず行ってください。**

1. **自動オーナー判定を切る**（環境変数 `OTHELLO_AUTO_OWNER=0`）
   ```bash
   OTHELLO_AUTO_OWNER=0 streamlit run app.py
   ```
   「このPCから開いたら自動でオーナー」は、自宅のPCで使うための仕組みです。
   nginx などの中継サーバー（リバースプロキシ）の後ろに置くと、すべてのアクセスがサーバー自身（127.0.0.1）から来たように見えます。
   中継サーバーのヘッダー（`X-Forwarded-For` など）や、ドメイン名でのアクセスは自動でオーナーにしないようにしてあります。それでも念のため、公開するときは切ってください。
2. **オーナーのパスワードを設定する**
   ```bash
   python set_owner_password.py
   ```
   自動判定を切ると、画面からのパスワード設定は出なくなるので、サーバー上でこのコマンドを実行します。
   パスワードは平文ではなく、ソルト付きハッシュ（PBKDF2）で `owner_auth.json` に保存されます。
   `owner_auth.json` と対局記録（`othello_history.db`）は `.gitignore` 済みです。

Gemini の API キーは、環境変数 `GEMINI_API_KEY` で渡します（コードやリポジトリには書かないでください）。

---

## 🧩 他サイト・他サービスへの組み込みガイド

本リポジトリのロジックは、Webフロントエンド（React/Vue/Next.jsなど）やバックエンドAPI（FastAPI, Flask, Node.js等）へ簡単に切り出して統合できます。

### ① オセロ対戦エンジンの呼び出し例（Python）

```python
from othello import BLACK, WHITE, apply_move, coord_to_str, create_initial_board, get_legal_moves, str_to_coord
from engine import OthelloEngine

# 1. 盤面初期化 (8x8の2次元リスト: 0=空, 1=黒, -1=白)
board = create_initial_board()

# 2. 合法手一覧を取得（{(row, col): 裏返る石のリスト} の辞書）
legal_moves = get_legal_moves(board, BLACK)
print("黒の合法手:", [coord_to_str(r, c) for r, c in legal_moves])

# 3. 着手を反映（新しい盤面と、裏返った石の座標が返る）
row, col = str_to_coord("f5")
board, flipped = apply_move(board, row, col, BLACK)

# 4. AIの着手を取得
#    mode="builtin": Python だけで動く組み込みエンジン（Lv.0〜4）
#    mode="cli": Egaroucid（Lv.0〜20。実行ファイルが必要）
engine = OthelloEngine(mode="builtin", level=3)
best_move = engine.choose_move(board, WHITE)
print("AIの手:", coord_to_str(*best_move))

# 5. 形勢判断（手番側から見た予想最終石差）
ev = engine.evaluate_position(board, WHITE)
print("白から見た予想石差:", ev.score_for_player, "最善手:", coord_to_str(*ev.best_move))
```

### ② 1手の戦術解析・悪手診断の呼び出し例

```python
from tactics import analyze_tactical_patterns
from line_compare import explain_line_difference

# 着手前の盤面・実戦の手・最善手・損失（石数）・手番を渡すと、戦術的な理由と解説文が返る
result = analyze_tactical_patterns(board_before, actual_move, best_move, eval_loss=6.0, player=WHITE)
print("主な理由の観点:", result.primary_factor)
print("戦術タグ:", result.tags)
print("解説:", result.commentary)

# 実戦手と最善手の読み筋を並べて、差がついた理由を Markdown で返す（比較できない局面では空文字）
text = explain_line_difference(engine, board_before, WHITE, actual_move, best_move)
print(text)
```

### ③ フロントエンドとのデータ連携フォーマット

- **盤面表現**: `board: int[8][8]`
  - `0`: 空きマス
  - `1`: 黒石 (Black)
  - `-1`: 白石 (White)
- **座標文字**: `a1` 〜 `h8`（横: a〜h, 縦: 1〜8）
  - `coord_to_str(row, col)` → `"f5"`、`str_to_coord("f5")` → `(4, 5)` で相互変換

---

## 🛠️ 技術スタック

| 分類 | 技術 / ライブラリ |
| :--- | :--- |
| **言語** | Python 3.11+ |
| **フロントエンド** | Streamlit, Plotly (形勢グラフ) |
| **探索エンジン** | Egaroucid 7.8.1 (SIMD) / 独自実装 Alpha-Beta (Negamax) |
| **戦術XAI** | ビットボード特徴量抽出, 辺パターン認識 (Wing/Mountain/Block) |
| **データベース** | SQLite3 (`othello_history.db`) |
| **AI解説 (LLM)** | Google Gemini API (`gemini-2.5-flash`) |

---

## 📄 ライセンス
本プロジェクトのソースコードは MIT ライセンスで提供しています（[LICENSE](LICENSE)）。
Egaroucid は GPL-3.0 のもとで提供されています。Egaroucid の実行ファイルを一緒に配布する場合は、GPL-3.0 の条件に従ってください（このリポジトリには実行ファイルは含めていません）。
