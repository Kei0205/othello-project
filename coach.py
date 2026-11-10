"""
coach.py
盤面特徴量（開放度・モビリティ・中割り・危険マス）と最善手データをもとに、
Google GenAI SDK (`google-genai` / `gemini-2.5-flash`) を用いてオセロ戦術論に基づく
「悪手の理由」「どう考えるべきだったか」および「対局全体の弱点・改善アドバイス」を生成するモジュール。
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import os
from collections import Counter

from google import genai
from google.genai import types

from othello import (
    BLACK,
    WHITE,
    COL_NAMES,
    ROW_NAMES,
    color_name,
    apply_move,
    coord_to_str,
    str_to_coord,
    extract_move_features,
    get_legal_moves,
    count_stones,
)
from engine import PlyAnalysis
from tactics import analyze_tactical_patterns, PHASES, FACTOR_LABELS, get_phase
from line_compare import explain_line_difference

MODEL_NAME = "gemini-2.5-flash"
REVIEW_MD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_game_review.md")
GROWTH_MD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_growth_log.md")


def format_board_ascii(board: List[List[int]]) -> str:
    """LLMプロンプト向けに8x8盤面をわかりやすいテキスト表現へ変換"""
    lines = ["  a b c d e f g h"]
    for r in range(8):
        row_chars = []
        for c in range(8):
            val = board[r][c]
            if val == BLACK:
                row_chars.append("●")
            elif val == WHITE:
                row_chars.append("○")
            else:
                row_chars.append("・")
        lines.append(f"{ROW_NAMES[r]} " + " ".join(row_chars))
    return "\n".join(lines)


@dataclass
class CandidateEval:
    move: Tuple[int, int]
    move_str: str
    eval_user: float
    openness: int
    opp_mobility: int
    is_nakawari: bool
    is_danger: bool
    danger_label: str
    tactical_tags: List[str] = field(default_factory=list)


@dataclass
class DilemmaAnalysis:
    ply: int
    player: int
    board_before: List[List[int]]
    actual_move: Tuple[int, int]
    actual_move_str: str
    actual_eval: CandidateEval
    alternative_evals: List[CandidateEval]
    best_move: Tuple[int, int]
    best_move_str: str
    best_eval_user: float
    verdict: str  # "CORRECT", "BETTER_ALT", "EVEN", "THIRD_BEST"
    verdict_title: str  # e.g. "🎉 大正解！実戦の判断が見事的中しました！"
    summary_text: str
    eval_diff_vs_best_alt: float
    coach_comment: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ply": self.ply,
            "player": self.player,
            "actual_move_str": self.actual_move_str,
            "actual_eval": asdict(self.actual_eval),
            "alternative_evals": [asdict(a) for a in self.alternative_evals],
            "best_move_str": self.best_move_str,
            "best_eval_user": self.best_eval_user,
            "verdict": self.verdict,
            "verdict_title": self.verdict_title,
            "summary_text": self.summary_text,
            "eval_diff_vs_best_alt": self.eval_diff_vs_best_alt,
            "coach_comment": self.coach_comment,
            "board_before": self.board_before,
        }


def _fallback_dilemma_explanation(d: DilemmaAnalysis, has_line: bool = False) -> str:
    lines = []
    lines.append(f"**【判定結果】{d.verdict_title}**\n")
    lines.append(d.summary_text)
    lines.append("")

    act = d.actual_eval
    lines.append("##### 📊 盤面比較データ")
    lines.append(
        f"- **実戦手 `{act.move_str}`**: 予測石差 `{act.eval_user:+.1f}` 石 ／ 開放度 `{act.openness}` ／ 直後の相手手数 `{act.opp_mobility}` 箇所 ／ {'⭕ 中割り' if act.is_nakawari else '❌ 非中割り'}"
    )
    if act.tactical_tags:
        lines.append(f"  - 特徴: {', '.join(f'`#{t}`' for t in act.tactical_tags)}")

    for alt in d.alternative_evals:
        lines.append(
            f"- **迷った手 `{alt.move_str}`**: 予測石差 `{alt.eval_user:+.1f}` 石 ／ 開放度 `{alt.openness}` ／ 直後の相手手数 `{alt.opp_mobility}` 箇所 ／ {'⭕ 中割り' if alt.is_nakawari else '❌ 非中割り'}"
        )
        if alt.tactical_tags:
            lines.append(f"  - 特徴: {', '.join(f'`#{t}`' for t in alt.tactical_tags)}")

    if d.actual_move_str != d.best_move_str and all(
        alt.move_str != d.best_move_str for alt in d.alternative_evals
    ):
        lines.append(f"- **AI最善手 `{d.best_move_str}`**: 予測石差 `{d.best_eval_user:+.1f}` 石")

    lines.append("")
    lines.append("##### 💡 戦術解説")
    best_alt = max(d.alternative_evals, key=lambda a: a.eval_user)
    see_line = "（下の読み筋比較を参照）" if has_line else ""
    diff = abs(d.eval_diff_vs_best_alt)
    if d.verdict == "CORRECT":
        lines.append(f"実戦で選んだ `{act.move_str}` は、迷った `{best_alt.move_str}` より {diff:.1f}石 有利でした。")
        lines.extend(_candidate_comparison_lines(act, best_alt, see_line))
        lines.append("迷いを断ち切って素晴らしい判断ができました！")
    elif d.verdict == "BETTER_ALT":
        lines.append(f"迷っていた `{best_alt.move_str}` のほうが、実戦手 `{act.move_str}` より {diff:.1f}石 有利でした。")
        lines.extend(_candidate_comparison_lines(best_alt, act, see_line))
        lines.append("この好手を直感で有力候補に挙げられていたのは素晴らしい戦術眼です！")
    elif d.verdict == "THIRD_BEST":
        lines.append(
            f"実戦手 `{act.move_str}`（{act.eval_user:+.1f}石）も迷った `{best_alt.move_str}`（{best_alt.eval_user:+.1f}石）も、"
            f"最善手 `{d.best_move_str}`（{d.best_eval_user:+.1f}石）より3石以上悪い手でした。"
            f"迷った2手以外にも候補がないか、打つ前に一度盤面全体を見渡しましょう。"
        )
    else:  # EVEN
        lines.append(
            f"実戦手 `{act.move_str}` と迷った `{best_alt.move_str}` の形勢差はわずか {diff:.1f} 石で、どちらを選んでも甲乙つけがたい好判断でした。ハイレベルな選択肢を見極められています！"
        )

    return "\n".join(lines)


def _candidate_comparison_lines(better: CandidateEval, worse: CandidateEval, see_line: str) -> List[str]:
    """
    良い方の手が「どの数字で勝ち、どの数字で負けているか」を実際の値どおりに並べる。
    （以前は開放度が悪い方を「相手の手数を増やさず（開放度: 2 vs 1）」と良く書いてしまうことがあった）
    """
    pros: List[str] = []
    cons: List[str] = []
    b, w = f"`{better.move_str}`", f"`{worse.move_str}`"
    if better.opp_mobility < worse.opp_mobility:
        pros.append(f"直後の相手の打てる場所が少ない（{b} {better.opp_mobility}箇所 ／ {w} {worse.opp_mobility}箇所）")
    elif better.opp_mobility > worse.opp_mobility:
        cons.append(f"直後の相手の打てる場所は {w} の方が少ない（{b} {better.opp_mobility}箇所 ／ {w} {worse.opp_mobility}箇所）")
    if better.openness < worse.openness:
        pros.append(f"開放度が低い（{b} {better.openness} ／ {w} {worse.openness}）")
    elif better.openness > worse.openness:
        cons.append(f"開放度は {w} の方が低い（{b} {better.openness} ／ {w} {worse.openness}）")
    if better.is_nakawari and not worse.is_nakawari:
        pros.append(f"{b} は中割りになっている")
    elif worse.is_nakawari and not better.is_nakawari:
        cons.append(f"中割りになっているのは {w} の方")
    if worse.is_danger and not better.is_danger:
        pros.append(f"{w} は空き隅の隣（{worse.danger_label}）だが、{b} は安全なマス")
    elif better.is_danger and not worse.is_danger:
        cons.append(f"{b} の方が空き隅の隣（{better.danger_label}）への着手")

    out: List[str] = []
    if cons and pros:
        out.append("- **譲歩と本質比較（たしかに〜ですが、実は〜）**:")
        cons_str = "、".join(cons)
        pros_str = "、".join(pros)
        out.append(f"  - **たしかに** {w} は「{cons_str}」という魅力があり、ここで迷われたのは非常に自然で鋭い戦術眼です。")
        out.append(f"  - **しかし実は** {b} の方が「{pros_str}」という決定的な利点を持っています。")
        out.append(
            f"  - 目先の1手だけの形にとらわれず、数手先の展開（隅・パス・手止まり）まで見通すと {b} が優勢を維持できます{see_line}。"
        )
    else:
        if pros:
            out.append(f"- {b} が勝っている点: " + "、".join(pros) + "。")
        if cons:
            out.append(f"- {w} の方が良く見える点: " + "、".join(cons) + "。")
            out.append(
                f"それでも {b} の方が良いのは、石数の差が目先の数字ではなく、その後の進行（隅・パス・各エリアの最後の一手）で決まるからです{see_line}。"
            )
        elif see_line:
            out.append("具体的な進行の違いは下の読み筋比較で確認できます。")
    return out


def generate_dilemma_explanation(
    dilemma_analysis: DilemmaAnalysis,
    api_key: str = "",
    line_text: str = "",
) -> Tuple[str, bool]:
    """
    着手で迷った手と実戦手の比較・正解判定について Gemini (`gemini-2.5-flash`) で解説を生成する。
    line_text（読み筋比較）がある場合は、それを根拠に説明させる（解説本文の後ろへの追記は呼び出し側で行う）。
    """
    key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    has_line = bool(line_text)
    if not key:
        return _fallback_dilemma_explanation(dilemma_analysis, has_line), False

    d = dilemma_analysis
    board_ascii = format_board_ascii(d.board_before)
    alts_info = "\n".join(
        [
            f"  - 迷った手: {a.move_str} (予測石差: {a.eval_user:+.1f}石, 開放度: {a.openness}, 相手手数: {a.opp_mobility}箇所, 中割り: {'あり' if a.is_nakawari else 'なし'}, 危険マス: {a.danger_label})"
            for a in d.alternative_evals
        ]
    )

    prompt = f"""あなたは親切で的確なオセロ（リバーシ）専属AIコーチです。
プレイヤーが対局中の第{d.ply}手目で、実戦手「{d.actual_move_str}」と、別の候補手「{', '.join(a.move_str for a in d.alternative_evals)}」の間で迷い、
最終的に実戦手「{d.actual_move_str}」を選択しました。

【盤面情報】
- 手数: {d.ply}手目（ユーザー手番: {color_name(d.player)}）
- 着手前の盤面（●=黒, ○=白, ・=空）:
{board_ascii}

【評価値・特徴量の比較】
- 判定結果: {d.verdict_title}
- プレイヤーの実戦手: {d.actual_move_str} (予測石差: {d.actual_eval.eval_user:+.1f}石, 開放度: {d.actual_eval.openness}, 相手手数: {d.actual_eval.opp_mobility}箇所, 中割り: {'あり' if d.actual_eval.is_nakawari else 'なし'}, 危険マス: {d.actual_eval.danger_label})
- 迷った候補手:
{alts_info}
- AIエンジン最善手: {d.best_move_str} (予測石差: {d.best_eval_user:+.1f}石)
- 要約: {d.summary_text}

【読み筋比較（エンジンの最善進行）】
{line_text or '（この局面では比較なし）'}

【コーチングの指示】
1. 冒頭で「あなたの判断は大正解でした！」「迷った手のほうが実は好手でした！」など、プレイヤーの選択に対するジャッジを温かく明確に伝えてください。
2. なぜその差がついたのか、オセロ戦術原則（開放度・中割り・相手の手数(モビリティ)の制限・危険マス・辺の確保など）に基づいてわかりやすく具体的に説明してください。
3. 迷った手についても「その手に注目できた直感の良さ」を肯定しつつ、次回へのアドバイスを添えてください。
4. 200〜350文字程度で簡潔かつ親しみやすい日本語で回答してください。
5. 読み筋比較がある場合は、その実際の進行（隅を誰がいつ取るか・パス・各エリアの最後の一手・最終石数）を根拠にしてください。「X打ち・C打ちだから危険」といった一般論や、実際には劣っている数値（例: 開放度が高い方を「良い」と書く）を理由にしないでください。
"""
    try:
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
            ),
        )
        if response and response.text:
            return response.text.strip(), True
    except Exception:
        pass

    return _fallback_dilemma_explanation(d, has_line), False


def build_dilemma_line_comparison(d: DilemmaAnalysis, user_color: int, engine: Any) -> str:
    """
    実戦手と、迷った手のうち最も評価の高い手を読み筋で比較する（良い方を「最善」側に置く）。
    評価値はユーザー視点で保存されているため、着手側の視点に直して渡す。
    """
    if not d.alternative_evals:
        return ""
    alt = max(d.alternative_evals, key=lambda a: a.eval_user)
    sign = 1.0 if d.player == user_color else -1.0
    act_eval, alt_eval = d.actual_eval.eval_user * sign, alt.eval_user * sign
    if act_eval >= alt_eval:
        return explain_line_difference(
            engine, d.board_before, d.player, alt.move, d.actual_eval.move,
            eval_actual_user=alt_eval, eval_best_user=act_eval,
            label_actual="迷い", label_best="実戦",
        )
    return explain_line_difference(
        engine, d.board_before, d.player, d.actual_eval.move, alt.move,
        eval_actual_user=act_eval, eval_best_user=alt_eval,
        label_actual="実戦", label_best="迷い",
    )


def analyze_dilemma_record(
    dilemma_dict: Dict[str, Any],
    user_color: int,
    engine: Any,
    api_key: str = "",
) -> DilemmaAnalysis:
    """迷った手の記録データ（盤面・実戦手・候補手）をエンジンで解析し、判定とコーチング解説を生成する"""
    board_before = dilemma_dict["board_before"]
    ply = dilemma_dict["ply"]
    player = dilemma_dict["player"]
    actual_move = tuple(dilemma_dict["actual_move"])
    actual_move_str = dilemma_dict["actual_move_str"]
    hesitated_moves_str = dilemma_dict.get("hesitated_moves", [])

    # 着手前局面のエンジン最善手と評価値
    best_eval = engine.evaluate_position(board_before, player)
    best_move = best_eval.best_move or actual_move
    best_move_str = coord_to_str(*best_move)
    best_score_user = best_eval.score_for_black if user_color == BLACK else -best_eval.score_for_black

    def evaluate_candidate(mv: Tuple[int, int]) -> CandidateEval:
        mv_str = coord_to_str(*mv)
        if mv == best_move:
            eval_user = best_score_user
        else:
            board_next, _ = apply_move(board_before, mv[0], mv[1], player)
            opp_eval = engine.evaluate_position(board_next, -player)
            eval_user = opp_eval.score_for_black if user_color == BLACK else -opp_eval.score_for_black

        f = extract_move_features(board_before, player, mv, best_move)
        openness = f.openness_total if f else 0
        opp_mob = f.opp_mobility_after if f else 0
        is_nakawari = f.is_nakawari if f else False
        is_danger = (f.is_x_move or f.is_c_move) if f else False
        danger_label = "X打ち" if (f and f.is_x_move) else ("C打ち" if (f and f.is_c_move) else "安全")

        eval_loss = max(0.0, best_score_user - eval_user)
        tact_res = analyze_tactical_patterns(board_before, mv, best_move, eval_loss, player)
        tags = tact_res.tags if tact_res else []

        return CandidateEval(
            move=mv,
            move_str=mv_str,
            eval_user=round(eval_user, 1),
            openness=openness,
            opp_mobility=opp_mob,
            is_nakawari=is_nakawari,
            is_danger=is_danger,
            danger_label=danger_label,
            tactical_tags=tags,
        )

    actual_eval = evaluate_candidate(actual_move)
    alternative_evals: List[CandidateEval] = []
    for m_str in hesitated_moves_str:
        coord = str_to_coord(m_str)
        if coord and coord != actual_move:
            alternative_evals.append(evaluate_candidate(coord))

    if not alternative_evals:
        if best_move != actual_move:
            alternative_evals.append(evaluate_candidate(best_move))
        else:
            legals = [rc for rc in get_legal_moves(board_before, player).keys() if rc != actual_move]
            if legals:
                alternative_evals.append(evaluate_candidate(legals[0]))
            else:
                alternative_evals.append(actual_eval)

    best_alt = max(alternative_evals, key=lambda a: a.eval_user)
    diff = actual_eval.eval_user - best_alt.eval_user
    is_actual_best = (actual_move == best_move)
    is_alt_best = (best_alt.move == best_move)

    if is_actual_best or diff >= 1.5:
        verdict = "CORRECT"
        verdict_title = "🎉 大正解！実戦の判断が見事的中しました！"
        summary_text = f"実戦手「{actual_move_str}」は、迷った手「{best_alt.move_str}」より {abs(diff):.1f}石 有利でした。自信を持って打てた素晴らしい判断です！"
    elif is_alt_best or diff <= -1.5:
        verdict = "BETTER_ALT"
        verdict_title = f"💡 惜しい！迷った「{best_alt.move_str}」のほうが好手でした"
        summary_text = f"実は迷っていた「{best_alt.move_str}」のほうが、実戦手より {abs(diff):.1f}石 有利でした。有力な候補に気づけていた直感は素晴らしいです！"
    elif (not is_actual_best and not is_alt_best) and (
        best_score_user - actual_eval.eval_user >= 3.0
        and best_score_user - best_alt.eval_user >= 3.0
    ):
        verdict = "THIRD_BEST"
        verdict_title = f"⚠️ 実は…どちらの手も罠で、最善手は「{best_move_str}」でした"
        summary_text = f"実戦手「{actual_move_str}」も迷った「{best_alt.move_str}」も最善より3石以上損をしており、実は第三の手「{best_move_str}」が最善（+{best_score_user - actual_eval.eval_user:.1f}石）でした。"
    else:
        verdict = "EVEN"
        verdict_title = "🤝 互角の好判断！どちらも有力な手でした"
        summary_text = f"実戦手「{actual_move_str}」と迷った「{best_alt.move_str}」はほぼ同等（差 {abs(diff):.1f}石）です。ハイレベルな選択肢の迷いでした。"

    dilemma_analysis = DilemmaAnalysis(
        ply=ply,
        player=player,
        board_before=board_before,
        actual_move=actual_move,
        actual_move_str=actual_move_str,
        actual_eval=actual_eval,
        alternative_evals=alternative_evals,
        best_move=best_move,
        best_move_str=best_move_str,
        best_eval_user=round(best_score_user, 1),
        verdict=verdict,
        verdict_title=verdict_title,
        summary_text=summary_text,
        eval_diff_vs_best_alt=round(diff, 1),
    )

    line_text = build_dilemma_line_comparison(dilemma_analysis, user_color, engine)
    comment, _ = generate_dilemma_explanation(dilemma_analysis, api_key=api_key, line_text=line_text)
    dilemma_analysis.coach_comment = comment + ("\n\n" + line_text if line_text else "")
    return dilemma_analysis


def analyze_all_dilemmas(
    dilemmas_list: List[Dict[str, Any]],
    user_color: int,
    engine: Any,
    api_key: str = "",
) -> List[DilemmaAnalysis]:
    """対局中に蓄積された迷い手リストを一括解析する"""
    return [
        analyze_dilemma_record(d, user_color, engine, api_key)
        for d in dilemmas_list
    ]


def _fallback_blunder_explanation(blunder: PlyAnalysis) -> str:
    """
    APIキー未設定時または通信エラー時にも、24パターンのオセロ戦術推論エンジン（tactics.py）による
    プロレベルの悪手理由と最善手意図の解説を生成する
    """
    if getattr(blunder, "tactical_comment", ""):
        return blunder.tactical_comment

    # 未計算の場合はその場で推論を実行
    if blunder.board_before and blunder.move:
        eval_loss = max(0.0, -blunder.eval_diff_user)
        res = analyze_tactical_patterns(
            board_before=blunder.board_before,
            actual_move=blunder.move,
            best_move=blunder.best_move,
            eval_loss=eval_loss,
            player=blunder.player,
        )
        return res.commentary

    return "評価値が大きく低下した着手です。最善手との形勢差を確認しましょう。"


# 原因因子ごとの次局へのアドバイス（フェーズ別分析で使う）
_FACTOR_TIPS: Dict[str, str] = {
    "mobility": "打つ前に「返す石の周りに空きマスがいくつあるか」「打った後に相手が何箇所打てるか」を数え、少ない方を選びましょう。",
    "edge": "辺に打つときは、打った後の辺の形（ウイング・単独C・ストナーの標的形）を確かめ、山やブロックのような安定形を目指しましょう。",
    "stable": "隅を取れる手・確定石が増える手を最優先で探し、相手に隅を渡す手は「代わりに何を得るか」を確かめてから打ちましょう。",
    "parity": "空きエリアごとのマス数を数え、奇数空きには自分から、偶数空きは相手から打たせて、各エリアの最後の一手を取りましょう。",
    "discs": "終盤は少なく返す必要はありません。手止まりを崩さない範囲で、一番多く返せる手を選びましょう。",
    "": "形の差が小さい局面での損失です。打つ前に相手の最善の応手（咎め手）を1手読む習慣をつけましょう。",
}


def build_phase_breakdown(analyses: List[PlyAnalysis]) -> Tuple[List[str], Optional[Dict[str, Any]]]:
    """
    ユーザーの着手を4フェーズ（序盤・中盤前半・中盤後半・終盤）に分け、
    フェーズごとの悪手数・損失合計・主な原因因子を表にする。
    戻り値: (Markdown行のリスト, 最も損失が大きかったフェーズの情報 or None)
    """
    stats: Dict[str, Dict[str, Any]] = {
        ph["name"]: {"phase": ph, "moves": 0, "blunders": 0, "loss": 0.0, "factors": Counter()}
        for ph in PHASES
    }
    for a in analyses:
        if not a.is_user_turn:
            continue
        st = stats[get_phase(count_stones(a.board_before)[2])["name"]]
        st["moves"] += 1
        if a.is_blunder:
            st["blunders"] += 1
            st["loss"] += max(0.0, -a.eval_diff_user)
            st["factors"][getattr(a, "tactical_factor", "") or ""] += 1

    lines = [
        "### 🧭 フェーズ別の悪手分析",
        "| フェーズ | 空き | この時期の重視点 | 着手数 | 悪手 | 損失合計 | 主な原因 |",
        "| :--- | :---: | :--- | :---: | :---: | :---: | :--- |",
    ]
    for ph in PHASES:
        st = stats[ph["name"]]
        if st["factors"]:
            top = st["factors"].most_common(1)[0][0]
            cause = FACTOR_LABELS.get(top, "読みの差（形の差は小さい）")
        else:
            cause = "-"
        lines.append(
            f"| {ph['name']} | {ph['max_empty']}〜{ph['min_empty']} | {ph['focus']}・{ph['advice']} | "
            f"{st['moves']} | {st['blunders']} | -{st['loss']:.0f}石 | {cause} |"
        )

    worst = max(stats.values(), key=lambda st: st["loss"])
    if worst["loss"] <= 0:
        return lines, None
    top_factor = worst["factors"].most_common(1)[0][0]
    ph = worst["phase"]
    lines.append("")
    lines.append(
        f"- **最も石を失ったのは{ph['name']}（悪手{worst['blunders']}回・合計 -{worst['loss']:.0f}石）**。"
        f"この時期は「{ph['focus']}」（{ph['advice']}）が最重要です。"
    )
    lines.append(f"- {_FACTOR_TIPS.get(top_factor, _FACTOR_TIPS[''])}")
    return lines, {"phase": ph["name"], "loss": worst["loss"], "factor": top_factor}


def _fallback_overall_summary(
    analyses: List[PlyAnalysis],
    blunders: List[PlyAnalysis],
    user_color: int,
    black_score: int,
    white_score: int,
    dilemmas: Optional[List[DilemmaAnalysis]] = None,
) -> str:
    """APIキー未設定時の対局全体の弱点サマリー生成"""
    user_moves = [a for a in analyses if a.is_user_turn and a.features is not None]
    if not user_moves:
        return "ユーザーの着手データが不足しているため、全体の傾向分析をスキップしました。"

    avg_openness = sum(a.features.openness_total for a in user_moves if a.features) / len(user_moves)
    x_count = sum(1 for a in user_moves if a.features and a.features.is_x_move)
    c_count = sum(1 for a in user_moves if a.features and a.features.is_c_move)
    nakawari_count = sum(1 for a in user_moves if a.features and a.features.is_nakawari)
    high_open_blunders = sum(1 for b in blunders if b.features and b.features.openness_total >= 4)

    # 検出された戦術タグの集約
    blunder_tactical_tags = []
    for b in blunders:
        for t in getattr(b, "tactical_tags", []):
            if t not in blunder_tactical_tags:
                blunder_tactical_tags.append(t)

    summary_lines = [
        "### 📊 対局全体の戦術統計",
        f"- **あなたの担当色**: {color_name(user_color)} （最終スコア: 黒 {black_score} - 白 {white_score}）",
        f"- **総着手数**: {len(user_moves)} 手 / **検出された悪手(Blunder)数**: {len(blunders)} 手",
        f"- **平均開放度**: {avg_openness:.1f} （中割り成功数: {nakawari_count} 回）",
        f"- **危険マス着手**: X打ち {x_count} 回 / C打ち {c_count} 回",
    ]
    if blunder_tactical_tags:
        summary_lines.append(f"- **検出された戦術的弱点**: {', '.join(f'`#{t}`' for t in blunder_tactical_tags)}")

    if dilemmas:
        summary_lines.append(f"- **迷い手メモ記録数**: **{len(dilemmas)} 箇所**")
        summary_lines.append("")
        summary_lines.append("### ⚖️ 「あの時迷った手」の判定ダイジェスト")
        for idx, d in enumerate(dilemmas, 1):
            alts_str = ", ".join(a.move_str for a in d.alternative_evals)
            summary_lines.append(f"- **第{d.ply}手目 (実戦 `{d.actual_move_str}` vs 迷い `{alts_str}`)**: {d.verdict_title}")
            summary_lines.append(f"  - {d.summary_text}")
    phase_lines, _ = build_phase_breakdown(analyses)
    summary_lines.extend([""] + phase_lines)
    summary_lines.extend([
        "",
        "### 🔍 あなたの弱点の癖・改善アドバイス",
    ])

    if not blunders:
        summary_lines.append(
            "- **素晴らしい対局内容です！** 大きな評価値下落（悪手）がなく、開放度と相手のモビリティをよくコントロールできています。\n"
            "- さらなるレベルアップのためには、終盤の偶数理論（相手が打った空きマス領域に追随して最後の一手を残す技術）を意識するとさらに勝率が安定します。"
        )
    else:
        # パターン別の重点指摘
        if any("偶数理論" in t or "手止まり" in t for t in blunder_tactical_tags):
            summary_lines.append(
                "- **【終盤の偶数理論と手止まり】**: 終盤（残り空きマス16以下）で奇数空きエリアではなく偶数空きエリアに先に手を出して手止まりを相手に渡してしまう傾向が見られます。"
                "「奇数空きには自分から、偶数空きは相手から打たせる」原則を徹底しましょう。"
            )
        if any("外壁" in t or "壁" in t for t in blunder_tactical_tags):
            summary_lines.append(
                "- **【外壁化（フロンティア形成）の回避】**: 中盤に外周沿いで直線状に自分の石を連ねてしまい、相手の打ち場を広げて自分の手数を減らす『壁』を作ってしまっています。"
                "外側の石ではなく、周囲が埋まっている内側の石だけを返す中割りを意識しましょう。"
            )
        if any("ストナー" in t or "ウイング" in t for t in blunder_tactical_tags):
            summary_lines.append(
                "- **【辺の攻防手筋（ストナー・ウイング攻め）】**: 相手の辺の悪形（ウイングやC打ち含む4石）に対する攻め手筋（ウイング攻めX、対角線を通すストナーの仕掛け）の見逃し、または自らが標的となる悪形を作ってしまう場面がありました。"
            )
        if x_count > 0 or c_count > 0:
            summary_lines.append(
                "- **【隅周辺の危険マス管理】**: 空いている隅の周囲（Xマス: b2/g2/b7/g7、Cマス: 隅の隣）への早い着手が悪手の原因になっています。"
                "中盤で打つ場所が減ってきた段階でも、X打ち・C打ちをする前に「中央付近の中割り」や「安全な辺への着手」が残っていないか必ず確認しましょう。"
            )
        if high_open_blunders > 0 or avg_openness >= 4.0:
            summary_lines.append(
                "- **【開放度理論の徹底】**: 一度に多くの石や外周の石を返しすぎてしまい、自分の石が壁（フロンティア）となって相手の手数（モビリティ）を増やしてしまう傾向が見られます。"
                "序盤〜中盤は「自分の石を少なく保ち、空きマスに囲まれていない内側の石だけを1〜2個返す（中割り）」ことを最優先目標にしてみましょう。"
            )
        summary_lines.append(
            "- **【次の一手を決めるチェックリスト】**:\n"
            "  1. その手で裏返る石の周りに空きマスがいくつあるか（開放度3以下が理想）。\n"
            "  2. その手を打った直後、相手が隅や好形の中割りに打てるようにならないか。\n"
            "  3. 隅がまだ空いている場合、Xマス・Cマスへの着手を避けられているか。\n"
            "  4. 終盤（残り16マス以下）では、奇数空きエリアから先に打てているか。"
        )

    return "\n".join(summary_lines)


def generate_blunder_explanation(
    blunder: PlyAnalysis,
    api_key: Optional[str] = None,
) -> Tuple[str, bool]:
    """
    単一の悪手局面について Gemini (`gemini-2.5-flash`) で戦術解説を生成する。
    戻り値: (解説Markdown文字列, Gemini APIを使用したかどうか)
    """
    key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key or blunder.features is None:
        return _fallback_blunder_explanation(blunder), False

    f = blunder.features
    board_ascii = format_board_ascii(blunder.board_before)

    tactical_context = getattr(blunder, "tactical_comment", "")
    tactical_tags_str = ", ".join(getattr(blunder, "tactical_tags", []))

    prompt = f"""あなたはプロのオセロ（リバーシ）AIコーチです。
以下のユーザーが悪手（Blunder）を打った局面データ、戦術特徴量、およびルールベース戦術推論エンジンによるパターン検出結果をもとに、
オセロ戦術論（開放度理論、中割り、モビリティ・手数理論、隅・X打ち・C打ちのリスク、ストナーの罠、ウイング攻め、偶数理論、手止まりなど）に基づいて分かりやすく解説してください。

【局面情報】
- 手数: {blunder.ply}手目（ユーザー手番: {color_name(blunder.player)}）
- 着手前の盤面（●=黒, ○=白, ・=空）:
{board_ascii}

【着手と評価値】
- ユーザーの実際の着手: {f.move_str}
- エンジン推奨の最善手: {f.best_move_str}
- ユーザー視点の評価値（最終石差予測）変化: {blunder.eval_before_user:+.1f} 石 → {blunder.eval_after_user:+.1f} 石（{blunder.eval_diff_user:+.1f} 石の悪化）

【戦術推論エンジンによる検出パターン】
- 検出タグ: {tactical_tags_str or '形勢判断ミス'}
- ルールベース推論要点:
{tactical_context}

【算出された盤面特徴量】
- 打った手の開放度（裏返した石の周囲8近傍の空きマス数合計）: {f.openness_total} （重複なし空きマス数: {f.openness_unique} / 返した石数: {f.flipped_count}）
- 直後の相手の着手可能マス数（モビリティ）の変化:
  * 着手前の相手合法手数: {f.opp_mobility_before}
  * 実戦手({f.move_str})直後の相手合法手数: {f.opp_mobility_after}
  * 最善手({f.best_move_str})だった場合の相手合法手数: {f.opp_mobility_best} （最善手比 {f.opp_mobility_delta:+d}）
- 中割り判定: 実戦手={'中割り' if f.is_nakawari else '非中割り'} / 最善手={'中割り' if f.best_is_nakawari else '非中割り'}
- 危険マス判定: X打ち={'該当(危険)' if f.is_x_move else '非該当'}, C打ち={'該当(危険)' if f.is_c_move else '非該当'}
- 特徴補足: {f.danger_detail}
- 局面フェーズ: {get_phase(count_stones(blunder.board_before)[2])['name']}（空き{count_stones(blunder.board_before)[2]}マス）。このフェーズの重視点「{get_phase(count_stones(blunder.board_before)[2])['focus']}・{get_phase(count_stones(blunder.board_before)[2])['advice']}」に沿って説明してください。

以下の2つの見出しで簡潔かつ具体的に（合計250〜400文字程度で）解説してください：
1. **なぜ悪手なのか**（検出された戦術パターンや開放度・相手モビリティ等の数値に触れながら理由を説明）
2. **どう考えるべきだったか**（最善手 `{f.best_move_str}` の狙いと、この局面で意識すべきオセロの考え方）

注意:
- 上の推論要点に「読み筋で比較」がある場合は、その実際の進行（隅を誰がいつ取るか、パス、各エリアの最後の一手、最終石数）を根拠に説明してください。
- 「X打ち・C打ちだから危険」という一般論だけで済ませないでください。特に最善手も隅の隣の手だったり、最善手が隅を相手に渡す手だったりする場合は、なぜその手順の方が石数で得をするのかを読み筋に沿って説明してください。
"""

    try:
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
            ),
        )
        if response and response.text:
            return response.text.strip(), True
    except Exception as e:
        fallback = _fallback_blunder_explanation(blunder)
        return f"{fallback}\n\n> *(注: Gemini API 呼び出しエラーのため戦術ルールエンジンで解説を生成しました: `{e}`)*", False

    return _fallback_blunder_explanation(blunder), False


def generate_overall_coaching_summary(
    analyses: List[PlyAnalysis],
    blunders: List[PlyAnalysis],
    user_color: int,
    black_score: int,
    white_score: int,
    kifu_str: str,
    api_key: Optional[str] = None,
    dilemmas: Optional[List[DilemmaAnalysis]] = None,
) -> Tuple[str, bool]:
    """
    対局全体を通した「ユーザーの弱点の癖・改善アドバイス」を Gemini (`gemini-2.5-flash`) でサマリー生成する。
    戻り値: (サマリーMarkdown文字列, Gemini APIを使用したかどうか)
    """
    key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        return _fallback_overall_summary(analyses, blunders, user_color, black_score, white_score, dilemmas=dilemmas), False

    user_moves = [a for a in analyses if a.is_user_turn and a.features is not None]
    avg_openness = (
        sum(a.features.openness_total for a in user_moves if a.features) / len(user_moves)
        if user_moves else 0.0
    )
    x_count = sum(1 for a in user_moves if a.features and a.features.is_x_move)
    c_count = sum(1 for a in user_moves if a.features and a.features.is_c_move)
    nakawari_count = sum(1 for a in user_moves if a.features and a.features.is_nakawari)

    blunder_summaries = []
    for b in blunders:
        f = b.features
        if f:
            blunder_summaries.append(
                f"- {b.ply}手目: 実戦 `{b.move_str}` (最善 `{b.best_move_str}`), "
                f"評価値変動 {b.eval_diff_user:+.1f}石, 開放度={f.openness_total}, "
                f"相手着手可能数={f.opp_mobility_after}(最善なら{f.opp_mobility_best}), "
                f"中割り={'Yes' if f.is_nakawari else 'No'}, X打ち={'Yes' if f.is_x_move else 'No'}, C打ち={'Yes' if f.is_c_move else 'No'}"
            )
    blunder_list_str = "\n".join(blunder_summaries) if blunder_summaries else "閾値を超える悪手はありませんでした。"

    dilemma_section = ""
    if dilemmas:
        d_lines = []
        for d in dilemmas:
            alts_str = ", ".join(a.move_str for a in d.alternative_evals)
            d_lines.append(f"- {d.ply}手目の迷い: 実戦「{d.actual_move_str}」 vs 迷い「{alts_str}」 → 【{d.verdict_title}】: {d.summary_text}")
        dilemma_section = f"\n【対局中に迷われた局面の判定】\n" + "\n".join(d_lines) + "\n"

    phase_text = "\n".join(build_phase_breakdown(analyses)[0])

    prompt = f"""あなたはプロのオセロ（リバーシ）AIコーチです。
1局を通したユーザーの対局データと悪手（Blunder）一覧を分析し、
「ユーザーの弱点の癖」と「次局に向けた具体的な改善アドバイス」をサマリーとしてまとめてください。

【対局結果と全体統計】
- ユーザー担当手番: {color_name(user_color)}
- 最終石数: 黒 {black_score} - 白 {white_score}
- 棋譜: {kifu_str}
- ユーザーの総着手数: {len(user_moves)} 手
- ユーザー着手の平均開放度: {avg_openness:.2f}
- 中割り達成数: {nakawari_count} 手
- 危険マス着手回数: X打ち {x_count} 回, C打ち {c_count} 回
- 悪手（Blunder）数: {len(blunders)} 手
{dilemma_section}
【抽出された悪手一覧】
{blunder_list_str}

【フェーズ別の悪手分析（序盤は開放度、中盤前半は辺、中盤後半は確定石、終盤は偶数理論・手止まりが重視される）】
{phase_text}

以下の構成でMarkdown形式で出力してください：
1. **対局全体の総評とプレイスタイル傾向**
2. **フェーズ別の弱点**（どのフェーズで最も石を失ったか、そのフェーズで重視すべき観点に照らして何が足りなかったか）
3. **見られた弱点の癖（開放度・手数管理・辺の形・確定石・偶数理論など）**
4. **対局中に迷われた局面の振り返りとアドバイス**（迷い手の記録がある場合）
5. **上達のための3つの実践アクションプラン**（最も損失の大きいフェーズを優先）
"""

    try:
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
            ),
        )
        if response and response.text:
            return response.text.strip(), True
    except Exception as e:
        fallback = _fallback_overall_summary(analyses, blunders, user_color, black_score, white_score, dilemmas=dilemmas)
        return f"{fallback}\n\n> *(注: Gemini API 呼び出しエラーのため戦術ルールエンジンでサマリーを生成しました: `{e}`)*", False

    return _fallback_overall_summary(analyses, blunders, user_color, black_score, white_score, dilemmas=dilemmas), False


def generate_sandbox_position_coaching(
    board: List[List[int]],
    turn: int,
    candidates_info: List[Dict[str, Any]],
    api_key: Optional[str] = None,
) -> Tuple[str, bool]:
    """
    検討タブ（Sandbox）で指定された任意の盤面と候補手について、
    Gemini (`gemini-2.5-flash`) による詳細な戦術コーチング解説を生成する。
    APIキー未設定時は戦術推論エンジンによる詳細レポートを返す。
    """
    b_cnt, w_cnt, e_cnt = count_stones(board)
    turn_str = color_name(turn)
    board_ascii = format_board_ascii(board)

    # 候補手一覧の整形
    cand_lines = []
    for idx, c in enumerate(candidates_info, 1):
        loss_desc = f"(損失 {c['loss']:.1f}石)" if c['loss'] > 0 else "(最善手)"
        cand_lines.append(
            f"{idx}. **{c['move_str']}**: 予測最終石差 {c['eval']:+.1f}石 {loss_desc} | 返却石: {c['flipped_cnt']}個 | 相手返し手: {c['opp_mob']}箇所 (予想返答: {c['opp_best_str']}) | {c['tag']}"
        )
    cand_summary = "\n".join(cand_lines)

    key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    if key:
        prompt = f"""あなたはプロのオセロ（リバーシ）AIコーチです。
ユーザーが「局面検討＆AIアナライザー」で研究・検討している以下の盤面について、オセロ戦術論（開放度理論、中割り、手数・モビリティ理論、偶数理論・パリティ、ストナーの罠、ウイング攻め、X打ち・C打ちのリスク、手止まりなど）に基づいて、プロの視点から分かりやすく要点とベストな方針を解説してください。

【検討中の局面情報】
- 手番: {turn_str}
- 現在の石数: 黒 {b_cnt}個 / 白 {w_cnt}個 / 残り空きマス {e_cnt}マス
- 盤面（●=黒, ○=白, ・=空）:
{board_ascii}

【エンジンによる候補手一覧と評価値】
{cand_summary}

以下の見出し構成で、具体的かつ実践的にアドバイスしてください：
### 💡 この局面の核心と急所
（この局面で最も重視すべき戦術概念、残りマス数に応じた優先度、危険な罠などを2〜3文で簡潔に）

### 🏆 推奨手と理由
（なぜ最善手が優れているのか、裏返る石の数や相手に与える手数、隅や辺のコントロールの観点から）

### ⚠️ 注意すべき悪手・危険な手
（候補手の中に潜む大悪手や疑問手について、なぜそれが損になるのか具体的に指摘）
"""
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.4,
                ),
            )
            if response and response.text:
                return response.text.strip(), True
        except Exception:
            pass

    # ルールベース戦術エンジンによるフォールバック解説
    best_c = candidates_info[0] if candidates_info else None
    lines = [
        f"### 💡 この局面の核心と急所 (戦術推論エンジン)",
        f"- **現在の手番**: **{turn_str}**（黒 {b_cnt}石 / 白 {w_cnt}石 / 空き {e_cnt}マス）",
    ]
    if e_cnt <= 16:
        lines.append("- **ゲーム段階**: **終盤戦**。中盤までの「少なく返す」セオリーから「偶数理論（パリティ）」「各エリアの手止まり確保」「最終石数の最大化」が最優先となります。")
    elif e_cnt <= 36:
        lines.append("- **ゲーム段階**: **中盤戦**。相手の手数（モビリティ）を奪い、自分の石を内側に潜り込ませる「開放度理論」「中割り」が最も重要です。")
    else:
        lines.append("- **ゲーム段階**: **序盤戦**。外壁を作らず、相手に打たせる場所を絞る手筋を意識しましょう。")

    if best_c:
        lines.append(f"\n### 🏆 推奨手: `{best_c['move_str']}` (予測石差 {best_c['eval']:+.1f}石)")
        lines.append(f"- 返却石数: **{best_c['flipped_cnt']}個** / 相手の着手可能数: **{best_c['opp_mob']}箇所**（AI予想返答: `{best_c['opp_best_str']}`）")
        lines.append(f"- この手は相手に余計な着手権を与えず、形勢を最も安定して維持できる手筋です。")

    # 悪手や危険手があるか
    bad_candidates = [c for c in candidates_info if c["loss"] >= 2.0]
    if bad_candidates:
        lines.append("\n### ⚠️ 注意すべき危険手")
        for bc in bad_candidates:
            tac = analyze_tactical_patterns(board, bc["move"], best_c["move"] if best_c else None, bc["loss"], turn)
            tac_reason = tac.primary_pattern["name"] if tac.primary_pattern else f"損失 {bc['loss']:.1f}石"
            lines.append(f"- **`{bc['move_str']}`** (損失 **-{bc['loss']:.1f}石**): {tac_reason}")
            if tac.primary_pattern and "explanation" in tac.primary_pattern:
                lines.append(f"  > {tac.primary_pattern['explanation']}")

    return "\n".join(lines), False


def export_latest_game_review_md(
    analyses: List[PlyAnalysis],
    blunders: List[PlyAnalysis],
    user_color: int,
    engine_name: str,
    black_score: int,
    white_score: int,
    kifu_str: str,
    blunder_threshold: float = -4.0,
    record_id: Optional[int] = None,
    output_path: str = REVIEW_MD_PATH,
    interrupted: bool = False,
    dilemmas: Optional[List[DilemmaAnalysis]] = None,
) -> str:
    """
    終局後解析が完了した際、APIキーの有無にかかわらずプロジェクト直下に
    `latest_game_review.md` を自動出力・上書き保存する。
    Antigravity上で直接「対局結果をレビューして」と対話するための構造化ドキュメント。
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_score = black_score if user_color == BLACK else white_score
    opp_score = white_score if user_color == BLACK else black_score
    if interrupted:
        result_str = f"途中終了 (中断時点の石差: {user_score - opp_score:+d}石)"
    elif user_score > opp_score:
        result_str = f"勝利 (WIN: +{user_score - opp_score}石)"
    elif user_score < opp_score:
        result_str = f"敗北 (LOSE: {user_score - opp_score}石)"
    else:
        result_str = "引き分け (DRAW: 0石)"

    user_moves = [a for a in analyses if a.is_user_turn and a.features is not None]
    avg_openness = (
        sum(a.features.openness_total for a in user_moves if a.features) / len(user_moves)
        if user_moves else 0.0
    )
    nakawari_count = sum(1 for a in user_moves if a.features and a.features.is_nakawari)
    x_count = sum(1 for a in user_moves if a.features and a.features.is_x_move)
    c_count = sum(1 for a in user_moves if a.features and a.features.is_c_move)

    rec_id_str = f"#{record_id}" if record_id is not None else "未割当"

    lines: List[str] = [
        "# 🎓 Antigravity オセロAIコーチング用・最新対局レビューシート (`latest_game_review.md`)",
        "",
        "## 🤖 Antigravity へのプロンプト指示（システムロール定義）",
        "> **【Antigravityへの指示】**",
        "> ユーザーが「対局結果をレビューして」や特定の局面について質問した場合、あなたはこのファイル（`latest_game_review.md`）および長期カルテ（`player_growth_log.md`）の内容を読み取り、**プロのオセロ（リバーシ）専属コーチ**として回答してください。",
        "> 回答にあたっては以下の点を含めて丁寧にコーチングを行い、ユーザーからの追加の質問にも対話形式で答えてください：",
        "> 1. **対局全体の総評**: 最終スコア・勝敗と、評価値推移から見る勝負の分かれ目（ターニングポイント）。",
        "> 2. **各悪手（Blunder）の戦術的解説**: 「なぜ悪手なのか」「どう考えるべきだったか（AI最善手の狙い）」を、算出された盤面特徴量（**開放度**・**相手モビリティの変化**・**中割りの有無**・**C打ち/X打ちの危険マス判定**）の具体的な数値を引用しながら解説すること。",
        "> 3. **長期的な弱点の癖と棋力の成長比較**: `player_growth_log.md` の過去対局データと比較し、「改善している点（例：開放度が下がった、好手率が上がった）」や「繰り返している弱点の癖」を指摘すること。",
        f"> 4. **レビューの自動保存（重要）**: レビューを回答する際（または「レビューを保存して」と言われた際）、Pythonワンライナー等で `db.update_match_review({record_id or 1}, review_markdown, weakness_tags)` と `coach.export_player_growth_md(db.list_match_records())` を実行し、あなたのレビュー結果と弱点タグをSQLite履歴（ID: {rec_id_str}）と `player_growth_log.md` に永続化してください。",
        "",
        "---",
        "",
        "## 1. 対局結果サマリー",
        f"- **SQLite 履歴レコードID**: `{rec_id_str}`",
        f"- **対局日時**: {now_str}",
        f"- **あなたの担当手番**: {color_name(user_color)}",
        f"- **対戦相手エンジン**: {engine_name}",
        f"- **最終スコア**: 黒 (●) **{black_score}** 石 - 白 (○) **{white_score}** 石",
        f"- **勝敗結果**: **{result_str}**",
        f"- **総手数**: {len(analyses)} 手",
        f"- **悪手（Blunder）抽出閾値**: 評価値 {blunder_threshold:+.1f} 石以下の下落",
        f"- **検出された悪手数**: **{len(blunders)} 手**",
        f"- **あなたの全体戦術統計**: 平均開放度 `{avg_openness:.2f}` / 中割り達成 `{nakawari_count}` 回 / 危険なX打ち `{x_count}` 回 / 危険なC打ち `{c_count}` 回",
        "",
        *build_phase_breakdown(analyses)[0],
        "",
        "---",
        "",
        "## 2. 棋譜全体（Kifu & 全手評価値推移）",
        "### 連結棋譜文字列",
        "```text",
        kifu_str if kifu_str else "(着手なし)",
        "```",
        "",
        "### 手順・評価値推移一覧（評価値はあなた視点の予測最終石差）",
        "| 手数 (Ply) | 手番 | 着手 | AI推奨最善手 | 着手前評価値 | 着手後評価値 | 石損(評価値変化) | 判定 |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for a in analyses:
        actor = f"{color_name(a.player)} ({'あなた' if a.is_user_turn else 'AI'})"
        badge = "🚨 **悪手(Blunder)**" if a.is_blunder else ("⭕ 最善一致" if (a.is_user_turn and a.move_str == a.best_move_str) else "-")
        lines.append(
            f"| {a.ply}手目 | {actor} | `{a.move_str}` | `{a.best_move_str}` | {a.eval_before_user:+.1f} | {a.eval_after_user:+.1f} | {a.eval_diff_user:+.1f} | {badge} |"
        )

    lines.extend([
        "",
        "---",
        "",
        f"## 3. 各悪手（Blunder）の詳細データ（全 {len(blunders)} 件）",
    ])

    if not blunders:
        lines.append(
            f"\n本対局では閾値（{blunder_threshold:+.1f}石）を超える悪手（Blunder）は検出されませんでした。各着手の精度や中盤のさらなる改善点についてレビューしてください。"
        )
    else:
        for idx, b in enumerate(blunders, start=1):
            f = b.features
            board_ascii = format_board_ascii(b.board_before)
            lines.extend([
                f"### 🚨 悪手 #{idx}：{b.ply}手目（あなたの手番: {color_name(b.player)}）",
                f"- **手数**: {b.ply}手目",
                f"- **打った手（実戦手）**: `{b.move_str}`",
                f"- **AI最善手**: `{b.best_move_str}`",
                f"- **石損（評価値変化）**: **{b.eval_diff_user:+.1f} 石** （着手前 `{b.eval_before_user:+.1f}` → 着手後 `{b.eval_after_user:+.1f}`）",
                f"- **局面フェーズ**: {get_phase(count_stones(b.board_before)[2])['name']}（空き{count_stones(b.board_before)[2]}マス） ／ 主な原因: "
                f"{FACTOR_LABELS.get(getattr(b, 'tactical_factor', ''), '読みの差（形の差は小さい）')}",
            ])
            tactical_tags = getattr(b, "tactical_tags", [])
            if tactical_tags:
                lines.append(f"- **戦術タグ**: {', '.join(f'`#{t}`' for t in tactical_tags)}")
            if getattr(b, "coach_comment", ""):
                lines.extend([
                    "- **戦術解説・アドバイス**:",
                    b.coach_comment,
                ])
            if f is not None:
                danger_flags = []
                if f.is_x_move:
                    danger_flags.append("⚠️ 危険なX打ち（空いている隅の斜め隣）")
                if f.is_c_move:
                    danger_flags.append("⚠️ 危険なC打ち（空いている隅の縦横隣）")
                if not danger_flags:
                    danger_flags.append("該当なし（C/X危険マスではない）")

                lines.extend([
                    f"- **開放度**: **{f.openness_total}** （裏返した石の周囲8近傍の空きマス数合計 / 重複なし空きマス数: `{f.openness_unique}` / 裏返した石数: `{f.flipped_count}` 個）",
                    f"- **直後の相手モビリティ（着手可能マス数）の変化**:",
                    f"  - 着手前の相手合法手数: `{f.opp_mobility_before}` 箇所",
                    f"  - 実戦手 (`{f.move_str}`) 直後の相手合法手数: **`{f.opp_mobility_after}` 箇所**",
                    f"  - 最善手 (`{f.best_move_str}`) を打っていた場合の相手合法手数: **`{f.opp_mobility_best}` 箇所** （最善手との差: `{f.opp_mobility_delta:+d}` 箇所）",
                    f"- **中割り判定**: 実戦手＝**{'⭕ 中割り' if f.is_nakawari else '❌ 非中割り'}** ／ AI最善手＝**{'⭕ 中割り' if f.best_is_nakawari else '❌ 非中割り'}**",
                    f"- **危険マス（C打ち・X打ち）判定**: {' / '.join(danger_flags)}",
                    f"- **局面特徴メモ**: {f.danger_detail}",
                ])
            lines.extend([
                "- **着手前の盤面（`●`=黒, `○`=白, `・`=空）**:",
                "```text",
                board_ascii,
                "```",
                "",
            ])

    if dilemmas:
        lines.extend([
            "",
            "---",
            "",
            f"## 4. ⚖️ 「あの時迷った手」の判定 ＆ AI振り返り（全 {len(dilemmas)} 件）",
        ])
        for idx, d in enumerate(dilemmas, 1):
            alts_str = ", ".join(a.move_str for a in d.alternative_evals)
            lines.extend([
                f"### ⚖️ 迷い手 #{idx}：第{d.ply}手目 実戦「`{d.actual_move_str}`」 vs 迷った「`{alts_str}`」",
                f"- **判定結果**: **{d.verdict_title}**",
                f"- **要約**: {d.summary_text}",
                f"- **実戦手 (`{d.actual_eval.move_str}`)**: 予測石差 `{d.actual_eval.eval_user:+.1f}` 石 ／ 開放度 `{d.actual_eval.openness}` ／ 直後の相手手数 `{d.actual_eval.opp_mobility}` 箇所 ／ {'⭕ 中割り' if d.actual_eval.is_nakawari else '❌ 非中割り'}",
            ])
            for alt in d.alternative_evals:
                lines.append(
                    f"- **迷った手 (`{alt.move_str}`)**: 予測石差 `{alt.eval_user:+.1f}` 石 ／ 開放度 `{alt.openness}` ／ 直後の相手手数 `{alt.opp_mobility}` 箇所 ／ {'⭕ 中割り' if alt.is_nakawari else '❌ 非中割り'}"
                )
            if d.actual_move_str != d.best_move_str:
                lines.append(f"- **AI最善手 (`{d.best_move_str}`)**: 予測石差 `{d.best_eval_user:+.1f}` 石")
            if d.coach_comment:
                lines.extend([
                    "- **コーチ解説・戦術アドバイス**:",
                    d.coach_comment,
                ])
            lines.extend([
                "- **着手前の盤面（`●`=黒, `○`=白, `・`=空）**:",
                "```text",
                format_board_ascii(d.board_before),
                "```",
                "",
            ])

    with open(output_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")

    return output_path


def export_player_growth_md(
    records: List[Dict[str, Any]],
    output_path: str = GROWTH_MD_PATH,
) -> str:
    """
    全対局履歴（SQLite）から、ユーザーの長期的な弱点の癖・棋力成長推移・
    保存されたAntigravityコーチングノートをまとめた `player_growth_log.md` を出力する。
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 古い順（時系列順）に並べ替え
    chronological = list(reversed(records))
    total_games = len(chronological)

    lines: List[str] = [
        "# 📈 オセロ長期弱点カルテ ＆ 棋力成長記録 (`player_growth_log.md`)",
        f"- **最終更新日時**: {now_str}",
        f"- **累計対局数**: {total_games} 局",
        "",
        "> **【Antigravityへの指示】**",
        "> このファイルにはユーザーの過去の全対局における棋力指標（好手率・平均石損・平均開放度・中割り率・危険マス着手数）の推移と、これまでに蓄積された弱点タグ・コーチングレビューが記録されています。",
        "> 「最近の成長を教えて」「私の長期的な弱点の癖は何？」と聞かれた場合や、`latest_game_review.md` をレビューする際の比較データとして活用してください。",
        "",
    ]

    if not chronological:
        lines.append("まだ記録された対局がありません。")
        with open(output_path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")
        return output_path

    wins = sum(1 for r in chronological if "WIN" in r.get("result_label", ""))
    # 途中終了（中断）局は勝敗がつかないため勝率の分母から除く
    decided_games = sum(1 for r in chronological if "中断" not in r.get("result_label", ""))
    win_rate = (wins / decided_games) * 100.0 if decided_games else 0.0
    avg_acc = sum(float(r.get("accuracy_rate") or 0.0) for r in chronological) / total_games
    avg_loss = sum(float(r.get("avg_eval_loss") or 0.0) for r in chronological) / total_games
    avg_open = sum(float(r.get("avg_openness") or 0.0) for r in chronological) / total_games
    avg_naka = sum(float(r.get("nakawari_rate") or 0.0) for r in chronological) / total_games

    # 弱点タグ集計
    tag_counts: Dict[str, int] = {}
    for r in chronological:
        raw_tags = (r.get("weakness_tags") or "").strip()
        if raw_tags:
            for t in raw_tags.split(","):
                t = t.strip()
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1

    lines.extend([
        "## 1. 通算パフォーマンス ＆ 長期弱点ランキング",
        f"- **通算勝率**: **{win_rate:.1f}%** ({wins}勝 / 決着{decided_games}局 ※中断局を除く)",
        f"- **平均好手・最善手率 (Accuracy)**: **{avg_acc:.1f}%**",
        f"- **1手あたり平均石損**: **{avg_loss:.2f} 石** （0に近いほど高精度）",
        f"- **通算平均開放度**: **{avg_open:.2f}** ／ **平均中割り率**: **{avg_naka:.1f}%**",
        "",
        "### 🔍 頻出する弱点の癖（タグ出現回数）",
    ])

    if not tag_counts:
        lines.append("- 特筆すべき頻出弱点タグはまだありません。")
    else:
        for tag, cnt in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True):
            rate = (cnt / total_games) * 100.0
            lines.append(f"- **`#{tag}`**: {cnt} 局で発生（発生率 {rate:.0f}%）")

    lines.extend([
        "",
        "---",
        "",
        "## 2. 棋力成長の推移テーブル（時系列順）",
        "| 対局ID | 日時 | 手番 | 結果 | スコア(黒-白) | 好手率(%) | 平均石損 | 悪手数 | 平均開放度 | 中割り率(%) | X/C危険手 | 検出された弱点タグ |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])

    for r in chronological:
        x_c = f"X:{r.get('x_move_count', 0)} / C:{r.get('c_move_count', 0)}"
        tags_str = r.get("weakness_tags") or "-"
        lines.append(
            f"| #{r['id']} | {r['played_at']} | {color_name(r['user_color'])} | {r['result_label']} | "
            f"{r['black_score']}-{r['white_score']} | {float(r.get('accuracy_rate') or 0):.1f}% | "
            f"{float(r.get('avg_eval_loss') or 0):.2f} | {r['blunder_count']} | "
            f"{float(r.get('avg_openness') or 0):.2f} | {float(r.get('nakawari_rate') or 0):.1f}% | "
            f"{x_c} | {tags_str} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 3. 保存された対局別コーチングレビュー履歴（新しい順）",
    ])

    for r in records[:15]:
        review_body = (r.get("antigravity_review") or r.get("coach_summary") or "").strip()
        lines.extend([
            f"### 🗂️ 対局 #{r['id']} ({r['played_at']} | {r['result_label']} | 黒{r['black_score']}-白{r['white_score']})",
            f"- **弱点タグ**: `{r.get('weakness_tags') or 'なし'}`",
            f"- **棋譜**: `{r['kifu']}`",
            "",
            review_body,
            "",
        ])

    with open(output_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")

    return output_path
