"""
rating.py
オセロ対局レーティング診断・Eloレーティング推移・段級位判定モジュール
"""

from __future__ import annotations
from typing import Any, Dict, List, Tuple
import re

# Egaroucid のレベル別基準レート（Lv.9 以上は 2450 で頭打ち）
_EGAROUCID_LEVEL_RATINGS = {1: 1400, 2: 1550, 3: 1700, 4: 1850, 5: 2000, 6: 2100, 7: 2200, 8: 2300}


def _parse_level(s: str):
    """エンジン名から 'Lv.N' の N を取り出す（'Lv.10' を 'Lv.1' と誤認しないよう数字全体を読む）"""
    m = re.search(r"Lv\.(\d+)", s)
    return int(m.group(1)) if m else None


def estimate_opponent_rating(engine_name: str) -> int:
    """
    対戦相手（AIの難易度または対人戦）の基準推定レーティングを返す
    """
    s = engine_name or ""
    lv = _parse_level(s)
    # 入門・超初級 (ひよこ)
    if lv == 0 or "ひよこ" in s or "超初級" in s or "入門" in s:
        return 750
    # Egaroucid CLI（「🌱 初級 AI (Egaroucid Lv.1)」も含む）
    if "Egaroucid" in s:
        if lv is None:
            return 1800
        return _EGAROUCID_LEVEL_RATINGS.get(lv, 2450)
    # Built-in AlphaBeta（カスタムLv指定時の名前）
    if "Built-in AlphaBeta" in s:
        # 既存の対局履歴のレートが変わらないよう、従来の実効値（Lv.3=中級プリセット=1350）に合わせる
        return {1: 950, 2: 1150, 3: 1350, 4: 1550}.get(lv, 1300)
    # プリセット難易度の組み込みAI
    if "ルーキー" in s or "初級 AI" in s:
        return 950
    if "初中級" in s or "アマ級位" in s:
        return 1150
    if "中級" in s or "アマ初段" in s:
        return 1350
    # 対人戦
    if "対人戦" in s:
        return 1200
    return 1200


def get_rank_title(rating: float) -> Tuple[str, str, str]:
    """
    レーティング数値から (段級位称号, バッジカラーHEX, クラス説明) を判定して返す
    """
    r = round(rating)
    if r < 800:
        return ("🐣 入門 (ひよこ級)", "#94a3b8", "ビギナークラス（ルール習得〜15級）")
    elif r < 1000:
        return ("🌱 初級 (10級〜7級)", "#10b981", "ルーキークラス（基本手筋の習得）")
    elif r < 1200:
        return ("🌿 初中級 (6級〜4級)", "#06b6d4", "アマ中堅クラス（中割りと開放度の意識）")
    elif r < 1400:
        return ("⚔️ 中級 (3級〜1級)", "#3b82f6", "アマ上級クラス（辺の攻防と定石）")
    elif r < 1600:
        return ("🥋 上級 (アマ初段〜二段)", "#8b5cf6", "有段者クラス（本格的な手筋・偶奇計算）")
    elif r < 1800:
        return ("🏆 強豪 (アマ三段〜四段)", "#f59e0b", "県大会上位クラス（終盤完全読み）")
    elif r < 2000:
        return ("💎 エキスパート (アマ五段〜六段)", "#ec4899", "全国レベル（全国大会常連）")
    else:
        return ("👑 達人・マスター (アマ七段以上)", "#e11d48", "世界トップクラス（グランドマスター）")


def diagnose_game_performance(
    user_color: int,
    black_score: int,
    white_score: int,
    engine_name: str,
    accuracy_rate: float,
    avg_eval_loss: float,
    blunder_count: int,
    interrupted: bool = False,
) -> Dict[str, Any]:
    """
    1局の勝敗・石差・好手率・平均石損・悪手数から、今局の『パフォーマンス推定レート』を精密診断する。
    """
    opp_rating = estimate_opponent_rating(engine_name)
    user_score = black_score if user_color == 1 else white_score
    opp_score = white_score if user_color == 1 else black_score
    diff = user_score - opp_score

    # 1. 勝敗＆石差ベースの評価値
    if interrupted:
        result_str = "中断"
        base_perf = opp_rating
    elif diff > 0:
        result_str = "勝利"
        # 勝ち: 相手レート + 80pt 〜 +250pt (石差に応じて加算)
        margin_bonus = min(170.0, max(20.0, diff * 3.5))
        base_perf = opp_rating + 80.0 + margin_bonus
    elif diff < 0:
        result_str = "敗北"
        # 負け: 相手レート - 250pt 〜 -50pt (惜敗なら高評価)
        margin_penalty = max(-150.0, min(-20.0, diff * 2.5))
        base_perf = opp_rating - 100.0 + margin_penalty
    else:
        result_str = "引分"
        base_perf = float(opp_rating)

    # 2. 着手精度ボーナス（好手率 ＆ 平均石損）
    # 基準好手率 70% に対する加点/減点
    acc_bonus = (accuracy_rate - 70.0) * 3.5

    # 平均石損 1.8 石に対する加点/減点
    loss_mod = max(-120.0, min(120.0, (1.8 - avg_eval_loss) * 45.0))

    # 悪手数ペナルティ
    blunder_penalty = min(80.0, blunder_count * 18.0)

    # パフォーマンスレート合算
    perf_rating = round(base_perf + acc_bonus + loss_mod - blunder_penalty)
    perf_rating = max(500, min(2800, perf_rating))

    rank_name, rank_color, rank_desc = get_rank_title(perf_rating)

    # 寸評コメントの生成
    if interrupted:
        short_comment = "途中終了局のため参考診断値です。"
    elif diff > 0 and accuracy_rate >= 80.0:
        short_comment = f"好手率 {accuracy_rate:.1f}% で格上のAI/相手を圧倒した見事な勝利局！"
    elif diff > 0:
        short_comment = "着実な石運びで見事に勝利を掴み取りました！"
    elif diff < 0 and accuracy_rate >= 75.0:
        short_comment = f"敗北したものの、好手率 {accuracy_rate:.1f}% と極めて高い精度を維持した善戦でした。"
    elif blunder_count >= 3:
        short_comment = f"悪手 {blunder_count} 回が響いたものの、序盤の構想力には光るものがありました。"
    else:
        short_comment = "平均石損を抑え、落ち着いて着手を進めた一局でした。"

    return {
        "opp_rating": opp_rating,
        "perf_rating": perf_rating,
        "rank_name": rank_name,
        "rank_color": rank_color,
        "rank_desc": rank_desc,
        "short_comment": short_comment,
        "result_str": result_str,
    }


def compute_sequential_ratings(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    時系列順（過去から最新へ）に対局レコードを受け取り、
    各対局終了時点の推定Eloレーティング推移とパフォーマンス値を計算して付与する。
    """
    if not records:
        return []

    # 時系列（古い順）に整列
    chronological = sorted(records, key=lambda r: (r.get("played_at", ""), r.get("id", 0)))
    current_rating = 1200.0  # 初期レート 1200

    results = []
    for idx, r in enumerate(chronological):
        rec_copy = dict(r)
        engine_name = rec_copy.get("engine_name", "")
        opp_rating = estimate_opponent_rating(engine_name)

        user_color = rec_copy.get("user_color", 1)
        b_score = rec_copy.get("black_score", 0)
        w_score = rec_copy.get("white_score", 0)
        res_label = rec_copy.get("result_label", "")
        acc_rate = float(rec_copy.get("accuracy_rate") or 70.0)
        avg_loss = float(rec_copy.get("avg_eval_loss") or 1.8)
        blunder_cnt = int(rec_copy.get("blunder_count") or 0)
        is_interrupted = "中断" in res_label

        # 1局のパフォーマンスレート
        diag = diagnose_game_performance(
            user_color=user_color,
            black_score=b_score,
            white_score=w_score,
            engine_name=engine_name,
            accuracy_rate=acc_rate,
            avg_eval_loss=avg_loss,
            blunder_count=blunder_cnt,
            interrupted=is_interrupted,
        )
        perf_rating = diag["perf_rating"]
        rec_copy["perf_rating"] = perf_rating

        # Elo計算
        r_before = current_rating
        rec_copy["rating_before"] = round(r_before)

        if is_interrupted:
            delta = 0.0
        else:
            if "WIN" in res_label:
                s = 1.0
            elif "DRAW" in res_label:
                s = 0.5
            else:
                s = 0.0

            # 期待勝率 E
            e = 1.0 / (1.0 + 10.0 ** ((opp_rating - current_rating) / 400.0))

            # Kファクター（初盤15局は大きめにして素早く適正レートに収束させる）
            k = 36.0 if idx < 15 else 22.0

            # 着手精度による微補正（好手率が高ければ惜敗でも下落を緩和）
            qual_bonus = (acc_rate - 70.0) * 0.08

            delta = k * (s - e) + qual_bonus

        current_rating = max(500.0, min(2800.0, current_rating + delta))
        rec_copy["rating_after"] = round(current_rating)
        rec_copy["rating_delta"] = round(delta)
        rank_name, rank_color, rank_desc = get_rank_title(current_rating)
        rec_copy["rank_name"] = rank_name
        rec_copy["rank_color"] = rank_color
        rec_copy["rank_desc"] = rank_desc

        results.append(rec_copy)

    return results
