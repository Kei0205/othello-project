"""
line_compare.py
悪手局面で「実戦手」と「最善手」それぞれからエンジンの読み筋（双方最善の進行）をたどり、
隅の受け渡し・パス（連打）・各空きエリアの最後の一手（手止まり）・最終石数を比較して、
「なぜ隅を渡す手の方が良いのか」「どちらも危険マスなのになぜ差がつくのか」を具体的に言語化するモジュール。

「X打ちだから危険」のような一般論では、
- 終盤であえて隅を取らせる手が最善になる理由
- どちらも危険マス（X/C）なのに優劣がつく理由
を説明できないため、実際の進行を並べて差がついた箇所を示す。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from othello import (
    BLACK,
    EMPTY,
    CORNERS,
    X_SQUARES,
    C_SQUARES,
    apply_move,
    coord_to_str,
    count_stones,
    get_legal_moves,
    is_game_over,
)

Move = Tuple[int, int]

# 終局まで読み切って比較する残り空きマス数の上限（Egaroucid がほぼ一瞬で完全読みできる範囲）
ENDGAME_EMPTIES = 16
# 中盤で比較するときにたどる手数
MIDGAME_PLIES = 8
# 読み筋をたどるときの Egaroucid レベル（終盤は完全読み、中盤は速さ優先）
ENDGAME_LEVEL = 15
MIDGAME_LEVEL = 8
# 組み込みエンジン（Python）しかない場合に終局まで読む上限
BUILTIN_ENDGAME_EMPTIES = 10


@dataclass
class PlayedLine:
    """1つの候補手から始まる読み筋"""
    first: Move
    steps: List[Tuple[int, Optional[Move]]]   # (手番, 着手 or None=パス)
    reached_end: bool                          # 終局までたどったか
    final_diff: Optional[int]                  # 着手側から見た最終石差（終局まで読んだ場合）
    corner_takers: Dict[Move, int]             # 隅 -> 取った側
    # (着手前の空きエリア, 最後に打った側, そのエリアの打てる手を打ち尽くしたか)
    region_last: List[Tuple[List[Move], Optional[int], bool]]
    passes: Dict[int, int]                     # 手番 -> パス回数
    final_board: List[List[int]]               # 読み筋の最後の局面
    mobility_collapse_step: Optional[int] = None # プレイヤーが安全手を失った(X/C強要)手数
    sector_discs: Dict[str, int] = field(default_factory=dict) # 主要セクターでの石数


def _color(player: int) -> str:
    return "黒" if player == BLACK else "白"


def empty_regions(board: List[List[int]]) -> List[List[Move]]:
    """空きマスを8近傍でつながったエリアに分ける"""
    seen = set()
    regions: List[List[Move]] = []
    for r in range(8):
        for c in range(8):
            if board[r][c] != EMPTY or (r, c) in seen:
                continue
            stack = [(r, c)]
            seen.add((r, c))
            region: List[Move] = []
            while stack:
                cr, cc = stack.pop()
                region.append((cr, cc))
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        nr, nc = cr + dr, cc + dc
                        if (dr or dc) and 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == EMPTY and (nr, nc) not in seen:
                            seen.add((nr, nc))
                            stack.append((nr, nc))
            regions.append(sorted(region))
    return regions


def region_label(region: List[Move]) -> str:
    """エリアを「右上（g1・h1・g2）」のような読める名前にする"""
    avg_r = sum(r for r, _ in region) / len(region)
    avg_c = sum(c for _, c in region) / len(region)
    vert = "上" if avg_r < 3.5 else "下"
    horiz = "左" if avg_c < 3.5 else "右"
    squares = "・".join(coord_to_str(r, c) for r, c in sorted(region, key=lambda rc: (rc[0], rc[1])))
    return f"{horiz}{vert}（{squares}）"


def corner_adjacency(board: List[List[int]], move: Optional[Move]) -> Optional[Tuple[str, Move]]:
    """move が空き隅の隣（X/C）なら ('X' or 'C', 隅) を返す"""
    if move is None:
        return None
    if move in X_SQUARES:
        corner = X_SQUARES[move]
        if board[corner[0]][corner[1]] == EMPTY:
            return "X", corner
    if move in C_SQUARES:
        corner = C_SQUARES[move]
        if board[corner[0]][corner[1]] == EMPTY:
            return "C", corner
    return None


def _choose(engine: Any, board: List[List[int]], turn: int, level: int) -> Move:
    legal = get_legal_moves(board, turn)
    res = engine.evaluate_position(board, turn, depth_override=level)
    if res.best_move in legal:
        return res.best_move
    return next(iter(legal))


def play_out(
    engine: Any,
    board: List[List[int]],
    player: int,
    first: Move,
    max_plies: Optional[int],
    level: int,
) -> PlayedLine:
    """first を打った後、双方がエンジンの最善手を打ち続けた進行をたどる"""
    regions = empty_regions(board)
    b, _ = apply_move(board, first[0], first[1], player)
    steps: List[Tuple[int, Optional[Move]]] = [(player, first)]
    turn = -player
    collapse_step: Optional[int] = None
    while not is_game_over(b) and (max_plies is None or len(steps) < max_plies) and len(steps) < 70:
        legals = get_legal_moves(b, turn)
        if not legals:
            steps.append((turn, None))
            turn = -turn
            continue
        if turn == player and collapse_step is None:
            safe = [m for m in legals if m not in X_SQUARES and m not in C_SQUARES]
            if not safe:
                collapse_step = len(steps) + 1
        mv = _choose(engine, b, turn, level)
        b, _ = apply_move(b, mv[0], mv[1], turn)
        steps.append((turn, mv))
        turn = -turn

    reached_end = is_game_over(b)
    final_diff = None
    if reached_end:
        bl, wh, _ = count_stones(b)
        final_diff = (bl - wh) if player == BLACK else (wh - bl)

    corner_takers: Dict[Move, int] = {}
    passes: Dict[int, int] = {}
    for who, mv in steps:
        if mv is None:
            passes[who] = passes.get(who, 0) + 1
        elif mv in CORNERS:
            corner_takers[mv] = who

    region_last: List[Tuple[List[Move], Optional[int], bool]] = []
    for region in regions:
        last = None
        for who, mv in steps:
            if mv is not None and mv in region:
                last = who
        # 終局まで読んだ場合は、空きが残っていても（誰も打てない）エリアは決着済み
        settled = reached_end or all(b[r][c] != EMPTY for r, c in region)
        region_last.append((region, last, settled))

    sectors = {
        "上辺": [(0, c) for c in range(8)],
        "下辺": [(7, c) for c in range(8)],
        "左辺": [(r, 0) for r in range(8)],
        "右辺": [(r, 7) for r in range(8)],
        "中央部": [(r, c) for r in range(2, 6) for c in range(2, 6)],
    }
    sector_discs: Dict[str, int] = {
        s_name: sum(1 for r, c in sqs if b[r][c] == player)
        for s_name, sqs in sectors.items()
    }

    return PlayedLine(
        first=first,
        steps=steps,
        reached_end=reached_end,
        final_diff=final_diff,
        corner_takers=corner_takers,
        region_last=region_last,
        passes=passes,
        final_board=b,
        mobility_collapse_step=collapse_step,
        sector_discs=sector_discs,
    )


def _pv_text(line: PlayedLine) -> str:
    parts = []
    for who, mv in line.steps:
        parts.append(f"{_color(who)}パス" if mv is None else f"{_color(who)}{coord_to_str(*mv)}")
    return " → ".join(parts)


def should_compare(board: List[List[int]], actual: Move, best: Optional[Move]) -> bool:
    """読み筋比較が説明に役立つ局面か（終盤、または隅周りの手が絡む局面）"""
    if best is None or best == actual:
        return False
    empties = count_stones(board)[2]
    if empties <= ENDGAME_EMPTIES:
        return True
    return bool(
        corner_adjacency(board, actual) or corner_adjacency(board, best)
        or actual in CORNERS or best in CORNERS
    )


def explain_line_difference(
    engine: Any,
    board: List[List[int]],
    player: int,
    actual: Move,
    best: Move,
    eval_actual_user: Optional[float] = None,
    eval_best_user: Optional[float] = None,
    label_actual: str = "実戦",
    label_best: str = "最善",
    force: bool = False,
    info: Optional[Dict[str, Any]] = None,
) -> str:
    """
    実戦手と最善手の読み筋を並べ、差がついた理由（隅・パス・手止まり・石数）を Markdown で返す。
    best には「良い方の手」を渡す。迷い手の比較では label_actual / label_best で呼び名を変える
    （例: 迷った手の方が悪いときは actual=迷った手, label_actual="迷い", best=実戦手, label_best="実戦"）。
    比較できない（エンジンが重すぎる等）場合は空文字を返す。
    """
    # force=True は「形の差が小さく理由を特定できない手」用。局面を問わず読み筋で違いを示す
    if best is None or best == actual or not (force or should_compare(board, actual, best)):
        return ""
    empties = count_stones(board)[2]
    is_cli = bool(getattr(engine, "is_cli_available", False))
    if empties <= ENDGAME_EMPTIES and (is_cli or empties <= BUILTIN_ENDGAME_EMPTIES):
        max_plies, level = None, ENDGAME_LEVEL
    elif is_cli:
        max_plies, level = MIDGAME_PLIES, MIDGAME_LEVEL
    else:
        return ""  # 組み込みエンジンで中盤を深く読むと遅すぎるため比較しない

    try:
        la = play_out(engine, board, player, actual, max_plies, level)
        lb = play_out(engine, board, player, best, max_plies, level)
    except Exception:
        return ""

    me, opp = _color(player), _color(-player)
    A, B = label_actual, label_best  # 呼び名（「実戦」「最善」など）。手を付けると「実戦手」「最善手」
    act_s, best_s = coord_to_str(*actual), coord_to_str(*best)
    to_end = la.reached_end and lb.reached_end

    def result_text(line: PlayedLine, eval_user: Optional[float]) -> str:
        if line.final_diff is not None:
            return f"最終 {me}{line.final_diff:+d}石"
        if eval_user is not None:
            return f"{len(line.steps)}手後の形勢 {me}{eval_user:+.1f}石"
        return f"{len(line.steps)}手先まで"

    title = "終局までの最善進行" if to_end else f"{MIDGAME_PLIES}手先までの最善進行"
    lines: List[str] = [
        f"#### 🔭 読み筋で比較（{title}）",
        f"- **{A} {act_s}**: {_pv_text(la)} → **{result_text(la, eval_actual_user)}**",
        f"- **{B} {best_s}**: {_pv_text(lb)} → **{result_text(lb, eval_best_user)}**",
        "",
    ]
    if (
        to_end and eval_actual_user is not None and eval_best_user is not None
        and (abs(la.final_diff - eval_actual_user) >= 1 or abs(lb.final_diff - eval_best_user) >= 1)
    ):
        lines.insert(-1, (
            f"  - ※ 読み筋は終局まで完全に読んだ結果のため、解析時の評価値（{A} {eval_actual_user:+.1f} ／ {B} {eval_best_user:+.1f}）と少しずれることがあります。"
        ))

    # 隅に隣接する手かどうか・隅を渡すかどうかで導入文を変える
    adj_a = corner_adjacency(board, actual)
    adj_b = corner_adjacency(board, best)
    opp_corners_b = [c for c, who in lb.corner_takers.items() if who == -player]

    def corner_net(line: PlayedLine) -> int:
        """自分の隅の数 − 相手の隅の数"""
        return sum(1 if who == player else -1 for who in line.corner_takers.values())

    # 良い方の手の進行の方が隅の収支で損をしている＝「あえて隅を渡す」手
    gives_corner_in_best = bool(opp_corners_b) and corner_net(lb) < corner_net(la)

    def adj_text(move_s: str, adj: Tuple[str, Move]) -> str:
        return f"{move_s}（{coord_to_str(*adj[1])}隅の{adj[0]}マス）"

    if gives_corner_in_best:
        corners_s = "・".join(coord_to_str(*c) for c in opp_corners_b)
        lines.append(
            f"**{B}手 {best_s} は、{opp}に {corners_s} の隅を取らせる進行で、隅の数では{A}手より損をします。** "
            f"それでも {act_s} より良いのは、次の点で取り返せるからです。"
        )
    elif adj_a and adj_b:
        lines.append(
            f"{adj_text(act_s, adj_a)} と {adj_text(best_s, adj_b)} は、どちらも空き隅の隣への着手です。"
            "「危険マスだから悪い」という点では同じで、差がつくのはその後の進行です。"
        )
    else:
        lines.append("差がついたのは次の点です。")

    reasons: List[str] = []

    # パス（連打）の違い
    opp_pass_a, opp_pass_b = la.passes.get(-player, 0), lb.passes.get(-player, 0)
    my_pass_a, my_pass_b = la.passes.get(player, 0), lb.passes.get(player, 0)
    if opp_pass_b > opp_pass_a:
        reasons.append(
            f"**{opp}をパスに追い込める**: {best_s} の進行では{opp}が打てる場所を失ってパスし、{me}が続けて打てます（{A}の進行では{opp}のパスは{'ありません' if opp_pass_a == 0 else f'{opp_pass_a}回だけ'}）。"
        )
    if my_pass_a > my_pass_b:
        reasons.append(
            f"**自分のパスを避けられる**: {A} {act_s} の進行では{me}がパスさせられ、{opp}に連打を許します。"
        )

    # 各エリアの最後の一手（手止まり）の違い
    for (region, last_a, settled_a), (_, last_b, settled_b) in zip(la.region_last, lb.region_last):
        # 読み筋の途中でまだ打ち切っていないエリアは「最後の一手」を比べられない
        if last_a == last_b or last_b is None or not (settled_a and settled_b):
            continue
        size = len(region)
        parity = "奇数" if size % 2 == 1 else "偶数"
        if last_b == player:
            reasons.append(
                f"**{region_label(region)}の最後の一手（手止まり）**: {B}の進行では{me}、{A}の進行では{opp}が打ちます"
                f"（{size}マスの{parity}空き）。最後に打った側はそのエリアの石をまとめて確定させられます。"
            )
        else:
            reasons.append(
                f"**{region_label(region)}の最後の一手**: {B}の進行では{opp}に譲りますが、そのぶん他のエリアで取り返しています。"
            )

    # 危険手強要（安全手枯渇 M_safe=0）の違い
    if la.mobility_collapse_step is not None and (lb.mobility_collapse_step is None or lb.mobility_collapse_step > la.mobility_collapse_step):
        reasons.append(
            f"**危険手強要（手詰まりの回避）**: {A} {act_s} の進行では、わずか {la.mobility_collapse_step} 手後に安全に打てるマス（X・Cマス以外のマス）がなくなり、隅を渡す危険マスへの着手を強要されます。一方、{B} {best_s} の進行では安全な手数が維持されます。"
        )

    # 隅の取り合いの違い
    def corner_summary(line: PlayedLine) -> str:
        if not line.corner_takers:
            return "なし"
        return "・".join(f"{_color(who)}{coord_to_str(*c)}" for c, who in line.corner_takers.items())

    # 主要セクター（辺・中央）の支配権の違い
    sector_diffs = {}
    for s_name in ("上辺", "下辺", "左辺", "右辺", "中央部"):
        c_a = la.sector_discs.get(s_name, 0)
        c_b = lb.sector_discs.get(s_name, 0)
        sector_diffs[s_name] = c_b - c_a
    max_sec, max_sec_gain = max(sector_diffs.items(), key=lambda kv: kv[1])
    if max_sec_gain >= 2 and not to_end:
        c_a = la.sector_discs.get(max_sec, 0)
        c_b = lb.sector_discs.get(max_sec, 0)
        reasons.append(
            f"**{max_sec}の支配権（セクター優位）**: {len(lb.steps)}手後の盤面で、{max_sec}の{me}の石は {A} {c_a}枚 に対し、{B}では {c_b}枚（+{max_sec_gain}枚）に達します。{B}手はこの重要エリアを固める決定打となっています。"
        )

    # 中盤の読み筋（途中で打ち切り）では、最後の局面での打てる場所の数（手数）を比べる
    if not to_end:
        def mobility(line: PlayedLine) -> Tuple[int, int]:
            return (
                len(get_legal_moves(line.final_board, player)),
                len(get_legal_moves(line.final_board, -player)),
            )

        my_a, op_a = mobility(la)
        my_b, op_b = mobility(lb)
        if info is not None:
            info["mobility"] = (my_a, op_a, my_b, op_b)
        if (my_b - op_b) > (my_a - op_a):
            reasons.append(
                f"**打てる場所（手数）の差**: {MIDGAME_PLIES}手後に{me}が打てる場所は {A} {my_a}箇所 ／ {B} {my_b}箇所、"
                f"{opp}は {A} {op_a}箇所 ／ {B} {op_b}箇所。{B}の進行の方が{opp}を窮屈にし、{me}に選択肢が残ります。"
            )
        if eval_actual_user is not None and eval_best_user is not None:
            reasons.append(
                f"**形勢**: エンジン評価は {A} {eval_actual_user:+.1f}石 ／ {B} {eval_best_user:+.1f}石"
                f"（{eval_best_user - eval_actual_user:.1f}石の差）。"
            )

    if la.corner_takers != lb.corner_takers:
        if gives_corner_in_best:
            gain = "石数" if to_end else "形勢"
            note = f"　→ 隅の数では{B}手の方が損をしていますが、{gain}では得をしています。"
        elif corner_net(lb) > corner_net(la):
            note = f"　→ {B}手の方が隅の取り合いでも有利です。"
        else:
            note = ""
        reasons.append(f"**隅の取り合い**: {A} {corner_summary(la)} ／ {B} {corner_summary(lb)}{note}")

    # 最終石差
    if to_end and la.final_diff is not None and lb.final_diff is not None:
        reasons.append(
            f"**結果**: 最終石差は {A} {la.final_diff:+d} ／ {B} {lb.final_diff:+d}（{lb.final_diff - la.final_diff}石の差）。"
        )

    if not reasons:
        if to_end:
            reasons.append("隅・パス・手止まりの取り方は同じで、返す石の位置の違いが最終的な石数の差になっています。")
        else:
            reasons.append(
                f"{MIDGAME_PLIES}手先までの範囲では隅・パスの違いはまだ表れていません。"
                "上の進行を見比べて、相手の打てる場所（手数）や返す石の位置がどう変わるかを確認しましょう。"
            )
    lines.extend(f"- {r}" for r in reasons)

    # 呼び出し側が「主な理由」を決められるよう、比較結果の要点を返す
    if info is not None:
        info.update({
            "to_end": to_end,
            "plies": MIDGAME_PLIES if not to_end else None,
            "corner_gain": corner_net(lb) - corner_net(la),           # 正なら良い方の手が隅で得
            "opp_pass_gain": opp_pass_b - opp_pass_a - (my_pass_b - my_pass_a),
            "regions_won": sum(
                1 for (_, la_last, sa), (_, lb_last, sb) in zip(la.region_last, lb.region_last)
                if sa and sb and lb_last == player and la_last != player
            ),
            "final": (la.final_diff, lb.final_diff),
            "mobility_collapse": (la.mobility_collapse_step, lb.mobility_collapse_step),
            "sector_diff": (max_sec, max_sec_gain),
        })

    # まとめの考え方
    lines.append("")
    if gives_corner_in_best and to_end:
        lines.append(
            "💡 **終盤は「隅を取られないこと」よりも「各エリアで最後に打つ権利（手止まり）」と「相手をパスに追い込めるか」が石数を決めます。** "
            "隅を渡しても、その代わりに残りのエリアの手番を自分に有利に整理できるなら、隅を渡す方が得になります。"
        )
    elif gives_corner_in_best:
        lines.append(
            "💡 **隅を渡す手が正解になるのは、渡した代わりに「相手の打てる場所を減らす」「エリアの最後の一手を確保する」などで取り返せるときです。** "
            "隅を渡した後の数手を読んで、何と引き換えになっているかを確かめましょう。"
        )
    elif adj_a and adj_b:
        lines.append(
            "💡 **危険マス同士で迷ったら、「打った後に相手がどの隅を取れるか」「その後どのエリアで自分が最後に打てるか」を1手ずつ確かめましょう。** "
            "X打ち・C打ちかどうかだけでは優劣は決まりません。"
        )
    elif empties <= ENDGAME_EMPTIES:
        lines.append(
            "💡 **終盤は、空きエリアごとに「誰が最後に打てるか」を数えると、石数の差がどこから来るかが見えます。**"
        )
    return "\n".join(lines)
