"""
db.py
SQLite3 データベース (`othello_history.db`) を用いた対局履歴・棋譜・評価値推移・
長期的な弱点傾向および棋力成長指標（平均石損・好手率・開放度・中割り率・危険マス頻度）の永続化モジュール
"""

from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple
import json
import os
import sqlite3

from engine import PlyAnalysis
from othello import BLACK, WHITE
from rating import compute_sequential_ratings, diagnose_game_performance

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "othello_history.db")


@contextmanager
def _connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """
    コミット＆確実なクローズを行う接続コンテキスト。
    （`with sqlite3.connect(...)` はトランザクションを閉じるだけで接続自体は閉じないため、Windowsでファイルロックが残るのを防ぐ）
    """
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(db_path: str = DB_PATH) -> None:
    """SQLiteテーブルの初期化と既存DBへの成長指標カラム自動マイグレーション"""
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS match_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                played_at TEXT NOT NULL,
                user_color INTEGER NOT NULL,
                engine_name TEXT NOT NULL,
                black_score INTEGER NOT NULL,
                white_score INTEGER NOT NULL,
                result_label TEXT NOT NULL,
                kifu TEXT NOT NULL,
                blunder_count INTEGER NOT NULL,
                eval_history_json TEXT NOT NULL,
                blunders_json TEXT NOT NULL,
                coach_summary TEXT NOT NULL,
                avg_openness REAL DEFAULT 0.0,
                nakawari_rate REAL DEFAULT 0.0,
                avg_eval_loss REAL DEFAULT 0.0,
                accuracy_rate REAL DEFAULT 0.0,
                x_move_count INTEGER DEFAULT 0,
                c_move_count INTEGER DEFAULT 0,
                weakness_tags TEXT DEFAULT '',
                antigravity_review TEXT DEFAULT '',
                dilemmas_json TEXT DEFAULT '[]'
            )
            """
        )
        # 既存テーブルがある場合のマイグレーション（不足カラムの追加）
        cur = conn.execute("PRAGMA table_info(match_history)")
        existing_cols = {row[1] for row in cur.fetchall()}
        migrations = [
            ("avg_openness", "REAL DEFAULT 0.0"),
            ("nakawari_rate", "REAL DEFAULT 0.0"),
            ("avg_eval_loss", "REAL DEFAULT 0.0"),
            ("accuracy_rate", "REAL DEFAULT 0.0"),
            ("x_move_count", "INTEGER DEFAULT 0"),
            ("c_move_count", "INTEGER DEFAULT 0"),
            ("weakness_tags", "TEXT DEFAULT ''"),
            ("antigravity_review", "TEXT DEFAULT ''"),
            ("dilemmas_json", "TEXT DEFAULT '[]'"),
            ("perf_rating", "REAL DEFAULT 0.0"),
            ("rating_after", "REAL DEFAULT 0.0"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE match_history ADD COLUMN {col_name} {col_def}")

        # 学習クエスト（bassy84.net の記事）の読了・カンペ登録
        conn.execute(
            "CREATE TABLE IF NOT EXISTS quest_progress (quest_id TEXT PRIMARY KEY, cleared_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS quest_bookmarks (quest_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
        )


        # レーティングの自動バックフィル（未計算の過去対局があれば時系列で一括補正）
        cur = conn.execute("SELECT COUNT(*) FROM match_history WHERE rating_after IS NULL OR rating_after = 0.0")
        unrated_count = cur.fetchone()[0]
        if unrated_count > 0:
            _backfill_ratings(conn)


def _backfill_ratings(conn: sqlite3.Connection) -> None:
    """過去の全対局レコードに対して時系列順にEloレーティングとパフォーマンスレートを補正保存する"""
    cur = conn.execute("SELECT * FROM match_history ORDER BY id ASC")
    col_names = [d[0] for d in cur.description]
    rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
    if not rows:
        return
    rated_rows = compute_sequential_ratings(rows)
    for r in rated_rows:
        conn.execute(
            "UPDATE match_history SET perf_rating = ?, rating_after = ? WHERE id = ?",
            (r.get("perf_rating", 1200.0), r.get("rating_after", 1200.0), r["id"]),
        )


def compute_result_label(user_color: int, black_score: int, white_score: int, interrupted: bool = False) -> str:
    # 途中終了した対局はその時点の石数で勝敗をつけない（通算勝率を歪めないため）
    if interrupted:
        return "中断 (途中終了)"
    user_score = black_score if user_color == BLACK else white_score
    opp_score = white_score if user_color == BLACK else black_score
    if user_score > opp_score:
        return "WIN (勝利)"
    elif user_score < opp_score:
        return "LOSE (敗北)"
    return "DRAW (引分)"


def compute_skill_metrics(analyses: List[PlyAnalysis]) -> Dict[str, Any]:
    """
    1局の解析データから、長期トラッキング用の棋力・弱点指標を算出する。
    - avg_openness: ユーザー着手の平均開放度（低いほど良好）
    - nakawari_rate: 中割り達成率 (%)
    - avg_eval_loss: ユーザー1手あたりの平均石損（0以上の数値、0に近いほど正確）
    - accuracy_rate: 最善手または損失1.5石以内の好手を打てた割合 (%)
    - x_move_count / c_move_count: 危険なX打ち・C打ち回数
    - weakness_tags: 自動検出された弱点タグ（カンマ区切り文字列）
    """
    user_moves = [a for a in analyses if a.is_user_turn and a.features is not None]
    if not user_moves:
        return {
            "avg_openness": 0.0,
            "nakawari_rate": 0.0,
            "avg_eval_loss": 0.0,
            "accuracy_rate": 100.0,
            "x_move_count": 0,
            "c_move_count": 0,
            "weakness_tags": "",
        }

    n = len(user_moves)
    avg_openness = round(sum(a.features.openness_total for a in user_moves if a.features) / n, 2)
    nakawari_cnt = sum(1 for a in user_moves if a.features and a.features.is_nakawari)
    nakawari_rate = round((nakawari_cnt / n) * 100.0, 1)

    losses = [max(0.0, -a.eval_diff_user) for a in user_moves]
    avg_eval_loss = round(sum(losses) / n, 2)

    accurate_cnt = sum(1 for a in user_moves if a.move_str == a.best_move_str or a.eval_diff_user >= -1.5)
    accuracy_rate = round((accurate_cnt / n) * 100.0, 1)

    x_cnt = sum(1 for a in user_moves if a.features and a.features.is_x_move)
    c_cnt = sum(1 for a in user_moves if a.features and a.features.is_c_move)

    blunders = [a for a in user_moves if a.is_blunder]
    tags: List[str] = []

    # 1. tactics.py で検出された精密な戦術タグを優先的に収集
    for b in blunders:
        for t in getattr(b, "tactical_tags", []):
            if t not in tags:
                tags.append(t)

    # 2. 統計的特徴量からの補助タグ
    if x_cnt > 0 and "危険なX打ち" not in tags and "X打ち" not in "".join(tags):
        tags.append("危険なX打ち")
    if c_cnt > 0 and "危険なC打ち" not in tags and "C打ち" not in "".join(tags):
        tags.append("危険なC打ち")
    if avg_openness >= 3.8 and "開放度過多(外返し)" not in tags and "外返し" not in "".join(tags):
        tags.append("開放度過多(外返し)")
    if any(b.features and b.features.opp_mobility_delta >= 3 for b in blunders) and "相手モビリティ増加" not in tags:
        tags.append("相手モビリティ増加")
    if nakawari_rate < 25.0 and n >= 4 and "中割り不足" not in tags and "中割り" not in "".join(tags):
        tags.append("中割り不足")
    if not tags and blunders:
        tags.append("形勢判断ミス")

    return {
        "avg_openness": avg_openness,
        "nakawari_rate": nakawari_rate,
        "avg_eval_loss": avg_eval_loss,
        "accuracy_rate": accuracy_rate,
        "x_move_count": x_cnt,
        "c_move_count": c_cnt,
        "weakness_tags": ",".join(tags),
    }


def save_match_record(
    user_color: int,
    engine_name: str,
    black_score: int,
    white_score: int,
    kifu: str,
    analyses: List[PlyAnalysis],
    coach_summary: str,
    antigravity_review: str = "",
    dilemmas_json: str = "[]",
    db_path: str = DB_PATH,
    record_id: Optional[int] = None,
    interrupted: bool = False,
) -> int:
    """
    対局結果・棋力成長指標・コーチング結果をSQLiteに保存し、レコードIDを返す。
    record_id を指定した場合は既存レコードを更新する（Antigravityレビューと対局日時は保持）。
    """
    init_db(db_path)
    played_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result_label = compute_result_label(user_color, black_score, white_score, interrupted)
    metrics = compute_skill_metrics(analyses)

    eval_history_data = [
        {
            "ply": a.ply,
            "player": a.player,
            "move_str": a.move_str,
            "best_move_str": a.best_move_str,
            "eval_before_user": a.eval_before_user,
            "eval_after_user": a.eval_after_user,
            "eval_diff_user": a.eval_diff_user,
            "is_user_turn": a.is_user_turn,
            "is_blunder": a.is_blunder,
        }
        for a in analyses
    ]

    blunders = [a for a in analyses if a.is_blunder]
    blunders_data = [
        {
            "ply": b.ply,
            "move_str": b.move_str,
            "best_move_str": b.best_move_str,
            "eval_before_user": b.eval_before_user,
            "eval_after_user": b.eval_after_user,
            "eval_diff_user": b.eval_diff_user,
            "features": b.features.to_dict() if b.features else {},
            "board_before": b.board_before,
            "coach_comment": b.coach_comment,
            "tactical_tags": getattr(b, "tactical_tags", []),
            "tactical_phase": getattr(b, "tactical_phase", ""),
            "tactical_factor": getattr(b, "tactical_factor", ""),
        }
        for b in blunders
    ]

    diag = diagnose_game_performance(
        user_color=user_color,
        black_score=black_score,
        white_score=white_score,
        engine_name=engine_name,
        accuracy_rate=metrics["accuracy_rate"],
        avg_eval_loss=metrics["avg_eval_loss"],
        blunder_count=len(blunders_data),
        interrupted=interrupted,
    )
    perf_rating = float(diag["perf_rating"])

    with _connect(db_path) as conn:
        # rating_after は保存後に _backfill_ratings で全対局を時系列に再計算して確定させる
        # （画面表示の compute_sequential_ratings と同じ計算式に揃えるため、ここでは仮の値を入れる）
        rating_after = 0.0

        values = (
            user_color,
            engine_name,
            black_score,
            white_score,
            result_label,
            kifu,
            len(blunders_data),
            json.dumps(eval_history_data, ensure_ascii=False),
            json.dumps(blunders_data, ensure_ascii=False),
            coach_summary,
            metrics["avg_openness"],
            metrics["nakawari_rate"],
            metrics["avg_eval_loss"],
            metrics["accuracy_rate"],
            metrics["x_move_count"],
            metrics["c_move_count"],
            metrics["weakness_tags"],
            dilemmas_json,
            perf_rating,
            rating_after,
        )
        # 再解析時は同じ対局のレコードを上書きし、履歴に重複レコードが増えないようにする
        if record_id is not None:
            cur = conn.execute(
                """
                UPDATE match_history SET
                    user_color = ?, engine_name = ?, black_score = ?, white_score = ?,
                    result_label = ?, kifu = ?, blunder_count = ?, eval_history_json = ?, blunders_json = ?,
                    coach_summary = ?, avg_openness = ?, nakawari_rate = ?, avg_eval_loss = ?,
                    accuracy_rate = ?, x_move_count = ?, c_move_count = ?, weakness_tags = ?,
                    dilemmas_json = ?, perf_rating = ?, rating_after = ?
                WHERE id = ?
                """,
                values + (record_id,),
            )
            if cur.rowcount > 0:
                _backfill_ratings(conn)
                return int(record_id)

        cur = conn.execute(
            """
            INSERT INTO match_history (
                user_color, engine_name, black_score, white_score,
                result_label, kifu, blunder_count, eval_history_json, blunders_json,
                coach_summary, avg_openness, nakawari_rate, avg_eval_loss,
                accuracy_rate, x_move_count, c_move_count, weakness_tags,
                dilemmas_json, perf_rating, rating_after, played_at, antigravity_review
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values + (played_at, antigravity_review),
        )
        new_id = int(cur.lastrowid)
        _backfill_ratings(conn)
        return new_id


def update_match_review(
    record_id: int,
    antigravity_review: str,
    weakness_tags: Optional[str] = None,
    db_path: str = DB_PATH,
) -> None:
    """
    特定対局レコードに Antigravity の詳細レビューや弱点タグを保存・更新する。
    Antigravityからのスクリプト呼び出し、またはStreamlit画面上の保存ボタンの両方から利用可能。
    （自動生成された coach_summary は上書きせず保持する）
    """
    init_db(db_path)
    with _connect(db_path) as conn:
        if weakness_tags is not None:
            conn.execute(
                "UPDATE match_history SET antigravity_review = ?, weakness_tags = ? WHERE id = ?",
                (antigravity_review, weakness_tags, record_id),
            )
        else:
            conn.execute(
                "UPDATE match_history SET antigravity_review = ? WHERE id = ?",
                (antigravity_review, record_id),
            )


def list_match_records(limit: int = 100, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """保存された対局履歴を新しい順に取得する"""
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM match_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    records: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["eval_history"] = json.loads(d.get("eval_history_json") or "[]")
        except (TypeError, ValueError):
            d["eval_history"] = []
        try:
            d["blunders"] = json.loads(d.get("blunders_json") or "[]")
        except (TypeError, ValueError):
            d["blunders"] = []
        try:
            d["dilemmas"] = json.loads(d.get("dilemmas_json") or "[]")
        except (TypeError, ValueError):
            d["dilemmas"] = []
        records.append(d)
    return records


def delete_match_record(record_id: int, db_path: str = DB_PATH) -> None:
    """指定IDの対局履歴を削除する"""
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM match_history WHERE id = ?", (record_id,))
        # 削除した対局より後のレーティング推移を詰め直す
        _backfill_ratings(conn)


# ---------------- 学習クエスト（bassy84.net の記事）の読了・カンペ登録 ----------------


def get_cleared_quest_ids(db_path: str = DB_PATH) -> set:
    with _connect(db_path) as conn:
        return {row[0] for row in conn.execute("SELECT quest_id FROM quest_progress")}


def set_quest_cleared(quest_id: str, cleared: bool, db_path: str = DB_PATH) -> None:
    """読了にする（cleared=True）／未読に戻す（False）"""
    with _connect(db_path) as conn:
        if cleared:
            conn.execute(
                "INSERT OR REPLACE INTO quest_progress (quest_id, cleared_at) VALUES (?, ?)",
                (quest_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
        else:
            conn.execute("DELETE FROM quest_progress WHERE quest_id = ?", (quest_id,))


def get_bookmarked_quest_ids(db_path: str = DB_PATH) -> List[str]:
    """カンペに登録した記事のID（新しい順）"""
    with _connect(db_path) as conn:
        return [row[0] for row in conn.execute("SELECT quest_id FROM quest_bookmarks ORDER BY created_at DESC")]


def set_quest_bookmark(quest_id: str, on: bool, db_path: str = DB_PATH) -> None:
    with _connect(db_path) as conn:
        if on:
            conn.execute(
                "INSERT OR REPLACE INTO quest_bookmarks (quest_id, created_at) VALUES (?, ?)",
                (quest_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
        else:
            conn.execute("DELETE FROM quest_bookmarks WHERE quest_id = ?", (quest_id,))
