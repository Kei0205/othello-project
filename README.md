# 🎮 Othello AI & Coaching System

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.35%2B-FF4B4B.svg)](https://streamlit.io/)
[![Engine](https://img.shields.io/badge/Engine-Egaroucid%20%26%20AlphaBeta-green.svg)](https://github.com/Nyanyan/Egaroucid)

高精度な探索エンジン（Egaroucid連携 ＆ 組み込みAlpha-Beta）、戦術的説明可能AI（XAI）、AIコーチング機能、レート算出、オンライン対戦を備えたオセロ対戦・解析システムです。

Webフロントエンド（Streamlit）として動作するだけでなく、対局ロジック・形勢評価・戦術解析・コーチングエンジンを**外部Webサイトや独自API（FastAPI, Express等）にモジュールとして簡単に組み込める**設計になっています。

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
├── egaroucid_console/      # Egaroucid バイナリ配置フォルダ（README.md参照）
├── .streamlit/             # Streamlit 設定（UIテーマ色等）
├── requirements.txt        # 依存Pythonライブラリ一覧
└── README.md               # 本ドキュメント
```

---

## 🚀 クイックスタート

### 1. 必要要件
- Python 3.9 以上
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

---

## 🧩 他サイト・他サービスへの組み込みガイド

本リポジトリのロジックは、Webフロントエンド（React/Vue/Next.jsなど）やバックエンドAPI（FastAPI, Flask, Node.js等）へ簡単に切り出して統合できます。

### ① オセロ対戦エンジンの呼び出し例（Python / FastAPI）

```python
from othello import create_initial_board, apply_move, get_legal_moves, coord_to_str, str_to_coord, BLACK, WHITE
from engine import OthelloAIEngine

# 1. 盤面初期化 (8x8の2次元リスト: 0=空, 1=黒, -1=白)
board = create_initial_board()

# 2. 合法手一覧を取得
legal_moves = get_legal_moves(board, BLACK)
print("黒の合法手:", [coord_to_str(m) for m in legal_moves])

# 3. 着手を反映
board, flipped = apply_move(board, str_to_coord("f5"), BLACK)

# 4. AIの着手を取得（Lv.1〜Lv.20）
engine = OthelloAIEngine(mode="cli", level=5)  # Egaroucidがなければ自動でAlphaBetaにフォールバック
best_move = engine.choose_move(board, WHITE)
print("AIの手:", coord_to_str(best_move))
```

### ② 1手の戦術解析・悪手診断の呼び出し例

```python
from tactics import analyze_tactical_patterns
from line_compare import explain_line_difference

# 着手前後の盤面と手を渡すだけで、戦術的な理由・パターンを解析
tactics = analyze_tactical_patterns(board_before, move, board_after, player)
print("戦術タイプ:", tactics.primary_factor)
print("開放度変化:", tactics.openness_diff)
print("危険マス:", tactics.is_danger_square)

# 最善手との読み筋比較
diff = explain_line_difference(
    board=board_before,
    player=player,
    actual_move=move,
    best_move=best_move,
    engine=engine
)
print("読み筋対比:", diff.summary_text)
```

### ③ フロントエンドとのデータ連携フォーマット

- **盤面表現**: `board: int[8][8]`
  - `0`: 空きマス
  - `1`: 黒石 (Black)
  - `-1`: 白石 (White)
- **座標文字**: `a1` 〜 `h8`（横: a〜h, 縦: 1〜8）
  - `(row, col)` ↔ `coord_to_str((row, col))` で相互変換

---

## 🛠️ 技術スタック

| 分類 | 技術 / ライブラリ |
| :--- | :--- |
| **言語** | Python 3.9+ |
| **フロントエンド** | Streamlit, Plotly (形勢グラフ) |
| **探索エンジン** | Egaroucid 7.8.1 (SIMD) / 独自実装 Alpha-Beta (Negamax) |
| **戦術XAI** | ビットボード特徴量抽出, 辺パターン認識 (Wing/Mountain/Block) |
| **データベース** | SQLite3 (`othello_history.db`) |
| **AI解説 (LLM)** | Google Gemini API (`gemini-2.5-flash`) |

---

## 📄 ライセンス
本プロジェクトのソースコードはオープンソース（MITライセンス）として提供されます。
Egaroucid は GPL-3.0 のもとで提供されています。
