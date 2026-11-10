"""
tactics.py
オセロ（リバーシ）戦術解説自動生成のための盤面パターン認識とルールベース言語化エンジン
24種類の高度な戦術パターン（序中盤モビリティ・開放度・フロンティア8種、辺・隅・X/C・ストナー11種、終盤パリティ・手止まり5種）
および優先度調停（Priority Arbitration）によるプロ品質の言語化パイプライン
"""

from __future__ import annotations
from collections import deque
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np

# 8方向の変位ベクトル
DIRECTIONS: List[Tuple[int, int]] = [
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
]

# 隅・Xマス・Cマス・Aマス・Bマスの座標定数
CORNERS: List[Tuple[int, int]] = [(0, 0), (0, 7), (7, 0), (7, 7)]
X_SQUARES: Dict[Tuple[int, int], Tuple[int, int]] = {
    (1, 1): (0, 0),
    (1, 6): (0, 7),
    (6, 1): (7, 0),
    (6, 6): (7, 7),
}
C_SQUARES: Dict[Tuple[int, int], Tuple[int, int]] = {
    (0, 1): (0, 0), (1, 0): (0, 0),
    (0, 6): (0, 7), (1, 7): (0, 7),
    (6, 0): (7, 0), (7, 1): (7, 0),
    (6, 7): (7, 7), (7, 6): (7, 7),
}
A_SQUARES: List[Tuple[int, int]] = [
    (0, 2), (0, 5), (7, 2), (7, 5),
    (2, 0), (5, 0), (2, 7), (5, 7),
]
B_SQUARES: List[Tuple[int, int]] = [
    (0, 3), (0, 4), (7, 3), (7, 4),
    (3, 0), (4, 0), (3, 7), (4, 7),
]

# 4辺と対応する中辺の座標定義テーブル
EDGE_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "上辺",
        "edge": [(0, c) for c in range(8)],
        "sub":  [(1, c) for c in range(8)],
        "x_left": (1, 1), "x_right": (1, 6),
        "corner_left": (0, 0), "corner_right": (0, 7),
    },
    {
        "name": "下辺",
        "edge": [(7, c) for c in range(8)],
        "sub":  [(6, c) for c in range(8)],
        "x_left": (6, 1), "x_right": (6, 6),
        "corner_left": (7, 0), "corner_right": (7, 7),
    },
    {
        "name": "左辺",
        "edge": [(r, 0) for r in range(8)],
        "sub":  [(r, 1) for r in range(8)],
        "x_left": (1, 1), "x_right": (6, 1),
        "corner_left": (0, 0), "corner_right": (7, 0),
    },
    {
        "name": "右辺",
        "edge": [(r, 7) for r in range(8)],
        "sub":  [(r, 6) for r in range(8)],
        "x_left": (1, 6), "x_right": (6, 6),
        "corner_left": (0, 7), "corner_right": (7, 7),
    },
]

# 各Xマスに対応する対角線座標リスト（Xマスから反対側の隅へ向かう順序）
DIAGONAL_LINES: Dict[Tuple[int, int], List[Tuple[int, int]]] = {
    (1, 1): [(2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7)],
    (6, 6): [(5, 5), (4, 4), (3, 3), (2, 2), (1, 1), (0, 0)],
    (1, 6): [(2, 5), (3, 4), (4, 3), (5, 2), (6, 1), (7, 0)],
    (6, 1): [(5, 2), (4, 3), (3, 4), (2, 5), (1, 6), (0, 7)],
}


def coord_to_str(move: Optional[Tuple[int, int]]) -> str:
    """(r, c) 座標を 'a1'〜'h8' のオセロ標準棋譜表記へ変換する"""
    if move is None:
        return "パス"
    r, c = move
    return f"{chr(ord('a') + c)}{r + 1}"


def get_neighbors_8(r: int, c: int) -> List[Tuple[int, int]]:
    """指定座標(r, c)の盤内8近傍座標リストを返す"""
    return [
        (r + dr, c + dc) for dr, dc in DIRECTIONS
        if 0 <= r + dr < 8 and 0 <= c + dc < 8
    ]


def get_flipped_discs(
    board: np.ndarray, move: Optional[Tuple[int, int]], color: int = 1
) -> Tuple[List[Tuple[int, int]], int]:
    """board上でcolorがmove(r, c)に着手した際に裏返る石の座標リストと方向数を返す"""
    if move is None:
        return [], 0
    r, c = move
    if board[r, c] != 0:
        return [], 0
    flipped: List[Tuple[int, int]] = []
    flipped_dirs = 0
    for dr, dc in DIRECTIONS:
        line: List[Tuple[int, int]] = []
        nr, nc = r + dr, c + dc
        while 0 <= nr < 8 and 0 <= nc < 8 and board[nr, nc] == -color:
            line.append((nr, nc))
            nr += dr
            nc += dc
        if line and 0 <= nr < 8 and 0 <= nc < 8 and board[nr, nc] == color:
            flipped.extend(line)
            flipped_dirs += 1
    return flipped, flipped_dirs


def apply_move(
    board: np.ndarray, move: Optional[Tuple[int, int]], color: int = 1
) -> Tuple[np.ndarray, List[Tuple[int, int]]]:
    """着手後の新しい盤面配列と裏返った石のリストを返す"""
    new_board = np.array(board, dtype=int).copy()
    if move is None:
        return new_board, []
    flipped, _ = get_flipped_discs(new_board, move, color)
    if flipped:
        new_board[move[0], move[1]] = color
        for fr, fc in flipped:
            new_board[fr, fc] = color
    return new_board, flipped


def get_legal_moves(board: np.ndarray, color: int = 1) -> List[Tuple[int, int]]:
    """指定した色(color)の合法手座標リストを返す"""
    moves: List[Tuple[int, int]] = []
    for r in range(8):
        for c in range(8):
            if board[r, c] == 0:
                flipped, _ = get_flipped_discs(board, (r, c), color)
                if flipped:
                    moves.append((r, c))
    return moves


class MoveContext:
    """解説生成に必要な着手前後の派生特徴量を一括計算・保持するコンテキストクラス"""

    def __init__(
        self,
        board_before: np.ndarray,
        actual_move: Optional[Tuple[int, int]],
        best_move: Optional[Tuple[int, int]],
        eval_loss: float = 0.0,
    ) -> None:
        self.board_before = np.array(board_before, dtype=int)
        self.actual_move = actual_move
        self.best_move = best_move
        self.eval_loss = eval_loss
        self.empty_count = int(np.sum(self.board_before == 0))

        # 実戦手（悪手）の適用結果
        self.board_actual, self.flipped_actual = apply_move(self.board_before, actual_move, 1)
        _, self.dirs_actual = get_flipped_discs(self.board_before, actual_move, 1)
        self.opp_moves_after_actual = get_legal_moves(self.board_actual, -1)

        # AI最善手の適用結果
        self.board_best, self.flipped_best = apply_move(self.board_before, best_move, 1)
        _, self.dirs_best = get_flipped_discs(self.board_before, best_move, 1)
        self.opp_moves_after_best = get_legal_moves(self.board_best, -1)


# ==============================================================================
# 2. 序盤〜中盤のモビリティ・開放度・フロンティア判定
# ==============================================================================

def compute_openness(
    board_before: np.ndarray, move: Optional[Tuple[int, int]], color: int = 1
) -> Tuple[int, int, int]:
    """
    着手moveの開放指標を計算する。
    戻り値: (unique_openness, sum_openness, max_single_exposure)
    """
    if move is None:
        return 99, 99, 99
    board_after, flipped = apply_move(board_before, move, color)
    if not flipped:
        return 99, 99, 99
    unique_empty: Set[Tuple[int, int]] = set()
    sum_open = 0
    max_exposure = 0
    for fr, fc in flipped:
        adj_empties = [
            (nr, nc) for nr, nc in get_neighbors_8(fr, fc)
            if board_after[nr, nc] == 0
        ]
        unique_empty.update(adj_empties)
        cnt = len(adj_empties)
        sum_open += cnt
        if cnt > max_exposure:
            max_exposure = cnt
    return len(unique_empty), sum_open, max_exposure


def get_frontier_discs(board: np.ndarray, color: int = 1) -> Set[Tuple[int, int]]:
    """指定した色(color)のフロンティア石（空きマスに8近傍で接する石）の座標集合を返す"""
    frontier: Set[Tuple[int, int]] = set()
    for r in range(8):
        for c in range(8):
            if board[r, c] == color:
                if any(board[nr, nc] == 0 for nr, nc in get_neighbors_8(r, c)):
                    frontier.add((r, c))
    return frontier


def is_outer_wall_creation(
    board_before: np.ndarray, move: Optional[Tuple[int, int]], color: int = 1
) -> Tuple[bool, int]:
    """
    外壁化（壁を作る悪手）の判定関数:
    1. 着手により自分のフロンティア石が3個以上純増する（または返した石+着手石のうち3個以上がフロンティアになる）
    2. 新たにフロンティアとなった石（着手位置含む）が一直線上に3マス以上連続している
    """
    if move is None:
        return False, 0
    board_after, flipped = apply_move(board_before, move, color)
    if not flipped:
        return False, 0
    f_before = get_frontier_discs(board_before, color)
    f_after = get_frontier_discs(board_after, color)
    delta_frontier = len(f_after) - len(f_before)

    changed_squares = set(flipped) | {move}
    new_frontier_in_move = changed_squares & f_after

    # 直線状に3つ以上並んだフロンティア石があるか検査
    collinear_wall = False
    for r, c in new_frontier_in_move:
        for dr, dc in [(0, 1), (1, 0), (1, 1), (1, -1)]:
            if (r + dr, c + dc) in new_frontier_in_move and (r + 2 * dr, c + 2 * dc) in new_frontier_in_move:
                collinear_wall = True
                break
        if collinear_wall:
            break

    is_wall = (delta_frontier >= 3 and len(new_frontier_in_move) >= 3) or (collinear_wall and delta_frontier >= 2)
    return is_wall, delta_frontier


def evaluate_midgame_mobility_patterns(ctx: MoveContext) -> List[Dict[str, Any]]:
    """序盤〜中盤のモビリティ・開放度・フロンティア系パターンを判定し、該当リストを返す"""
    results: List[Dict[str, Any]] = []
    if ctx.empty_count <= 16 or ctx.actual_move is None or ctx.best_move is None:
        return results

    u_act, s_act, max_exp_act = compute_openness(ctx.board_before, ctx.actual_move, 1)
    u_bst, s_bst, max_exp_bst = compute_openness(ctx.board_before, ctx.best_move, 1)

    is_wall_act, df_act = is_outer_wall_creation(ctx.board_before, ctx.actual_move, 1)
    is_wall_bst, df_bst = is_outer_wall_creation(ctx.board_before, ctx.best_move, 1)

    act_str = coord_to_str(ctx.actual_move)
    bst_str = coord_to_str(ctx.best_move)

    # [M-08] 引っ張り（相手に悪手を強要する手）の見逃し [Priority: 85 (Tier A)]
    opp_moves_bst = ctx.opp_moves_after_best
    opp_moves_act = ctx.opp_moves_after_actual
    if 0 < len(opp_moves_bst) <= 3 and len(opp_moves_act) >= len(opp_moves_bst) + 2:
        bad_options_only = True
        for om in opp_moves_bst:
            if om in X_SQUARES or om in C_SQUARES:
                continue
            u_opp, _, _ = compute_openness(ctx.board_best, om, -1)
            if u_opp < 4:
                bad_options_only = False
                break
        if bad_options_only or len(opp_moves_bst) <= 2:
            results.append({
                "id": "M-08",
                "name": "引っ張り（悪手強要・手数制限）の見逃し",
                "priority": 85,
                "explanation": (
                    f"実戦の {act_str} は相手に {len(opp_moves_act)} 箇所の選択肢を与えてしまい、楽な展開を許しています。"
                    f"ここは {bst_str} が強烈な「引っ張り（手数制限）」の好手でした。"
                    f"{bst_str} と打つことで相手の合法手をわずか {len(opp_moves_bst)} 箇所に絞り込み、"
                    f"次に危険なX・C打ちや大きな外返しを強制して局面を一気に崩壊させることができました。"
                ),
            })

    # [M-06] 外壁化・フロンティア形成（壁を作る悪手） [Priority: 78 (Tier B)]
    if is_wall_act and not is_wall_bst and (df_act - df_bst >= 2):
        results.append({
            "id": "M-06",
            "name": "外壁化・フロンティア形成（壁を作る悪手）",
            "priority": 78,
            "explanation": (
                f"実戦の {act_str} は外周に沿って自分の石を連ねてしまい、自分のフロンティア石（外壁）を "
                f"{df_act} 個増やしてしまう「壁作り」の悪手です。壁を作ると相手がその壁を飛び越えて打てる場所が"
                f"増える（相手合法手 {len(opp_moves_act)} 手）一方、自分の手数が激減して手詰まりに陥ります。"
                f"最善手 {bst_str} のように内側の石を返し、外側に自分の壁を作らない打ち方を心がけましょう。"
            ),
        })

    # [M-01] 完全中割りの見逃しと高開放度外返し [Priority: 75 (Tier B)]
    if u_bst == 0 and u_act >= 4:
        results.append({
            "id": "M-01",
            "name": "完全中割りの見逃しと高開放度外返し（開放度理論ミス）",
            "priority": 75,
            "explanation": (
                f"実戦の {act_str} は裏返した石の周囲に空きマスが {u_act} 箇所もある「外返し（開放度 {u_act}）」となっており、"
                f"相手に反撃の足がかりを与えています。一方、最善手の {bst_str} は裏返した石の周囲8マスがすべて他の石で"
                f"完全に囲まれた「完全中割り（開放度0）」の絶好手です。まずは盤面内部に開放度0の中割りがないか探すのが鉄則です。"
            ),
        })

    # [M-05] 相手の中割り消し（先読み開放度ブロック）の見逃し [Priority: 74 (Tier B)]
    opp_best_open_after_act = min(
        [compute_openness(ctx.board_actual, m, -1)[0] for m in opp_moves_act],
        default=99,
    )
    opp_best_open_after_bst = min(
        [compute_openness(ctx.board_best, m, -1)[0] for m in opp_moves_bst],
        default=99,
    )
    if opp_best_open_after_act <= 1 and opp_best_open_after_bst >= 3:
        results.append({
            "id": "M-05",
            "name": "相手の中割り消し（先読み開放度ブロック）の見逃し",
            "priority": 74,
            "explanation": (
                f"実戦の {act_str} を打つと、直後に相手から開放度 {opp_best_open_after_act} の綺麗な中割りを打たれて主導権を握られます。"
                f"最善手の {bst_str} は、相手が狙っている中割りの急所を先回りして消す（または自らがその急所に打つ）ことで、"
                f"次の相手の着手を最小でも開放度 {opp_best_open_after_bst} の苦しい外返しへと制限する好手です。"
            ),
        })

    # [M-04] 多方向・複数石中割りの見逃し [Priority: 72 (Tier B)]
    if (ctx.dirs_best >= 2 or len(ctx.flipped_best) >= 3) and u_bst <= 1 and u_act >= 2:
        results.append({
            "id": "M-04",
            "name": "多方向・複数石中割りの見逃し（好手見逃し）",
            "priority": 72,
            "explanation": (
                f"「序盤・中盤は少なく返す」という基本にとらわれて {act_str}（{len(ctx.flipped_actual)}石返し・開放度{u_act}）を"
                f"選びましたが、最善手の {bst_str} は {ctx.dirs_best} 方向に {len(ctx.flipped_best)} 枚の石を返しながらも、"
                f"返した石がすべて内部に収まる「多方向・大量取り中割り（開放度{u_bst}）」の好手です。"
                f"返した石が外気に触れなければ、複数方向を同時に返しても自分の手数は減りません。"
            ),
        })

    # [M-03] むき出しの中割り（悪い中割り） [Priority: 70 (Tier B)]
    if len(ctx.flipped_actual) >= 2 and max_exp_act >= 3 and u_bst <= 2 and max_exp_bst <= 2:
        min_exp_act = min(
            len([n for n in get_neighbors_8(fr, fc) if ctx.board_actual[n[0], n[1]] == 0])
            for fr, fc in ctx.flipped_actual
        )
        if min_exp_act <= 1:
            results.append({
                "id": "M-03",
                "name": "むき出しの中割り（悪い中割り・偽中割り）",
                "priority": 70,
                "explanation": (
                    f"実戦の {act_str} は一見すると内側の石を返す中割りに見えますが、同時に返した別の石が"
                    f"外周の空きマスに {max_exp_act} 箇所も接して「むき出し」になる悪い中割りです。"
                    f"むき出しになった石を起点に相手から容易に外堀を埋められてしまいます。"
                    f"最善手の {bst_str}（開放度 {u_bst}）のように、返したすべての石が綺麗に囲まれる手を選びましょう。"
                ),
            })

    # [M-07] 余裕手（テンポ・手数稼ぎ）の見逃し [Priority: 65 (Tier C)]
    opp_before_count = len(get_legal_moves(ctx.board_before, -1))
    if df_bst <= 0 and u_bst <= 1 and len(opp_moves_bst) <= opp_before_count and u_act >= 3:
        results.append({
            "id": "M-07",
            "name": "余裕手（テンポ・手数稼ぎ）の見逃し",
            "priority": 65,
            "explanation": (
                f"実戦の {act_str} は新たに開放度 {u_act} の石を返して局面の形を崩してしまいました。"
                f"ここは {bst_str} が、相手の打てる場所を増やさず自分の外壁も広げない「余裕手（手数稼ぎ）」でした。"
                f"余裕手を打つことで、自分だけ1手パスしたのと同じ効果を得て、相手に先に形を崩させることができます。"
            ),
        })

    # [M-02] 準中割り・低開放度手の見逃し [Priority: 60 (Tier C)]
    if u_bst in (1, 2) and (u_act - u_bst >= 3) and not any(r["id"] == "M-01" for r in results):
        results.append({
            "id": "M-02",
            "name": "準中割り・低開放度手の見逃し（開放度理論ミス）",
            "priority": 60,
            "explanation": (
                f"実戦の {act_str} は返した石の周囲に {u_act} 箇所の空きマスがある高開放度の手（開放度 {u_act}）です。"
                f"これに対し、最善手の {bst_str} は開放度 {u_bst} の「準中割り」であり、"
                f"相手の着手可能数を最小限に抑えることができます。中盤では開放度が1でも小さい手を選ぶのがセオリーです。"
            ),
        })

    return results


# ==============================================================================
# 3. 辺（エッジ）と隅（コーナー）周辺の形状認識・手筋・罠の判定
# ==============================================================================

def classify_single_edge(board: np.ndarray, edge_def: Dict[str, Any]) -> Dict[str, Any]:
    """
    指定された1辺(edge_def)の形状と所有者を機械的に分類する。
    戻り値: dict { "shape": str, "owner": int (1, -1, or 0), "details": dict }
    """
    e = [board[r, c] for r, c in edge_def["edge"]]
    s = [board[r, c] for r, c in edge_def["sub"]]

    # 両隅が両方とも埋まっている場合は確定石フェーズへ移行済み
    if e[0] != 0 and e[7] != 0:
        return {"shape": "Corner_Occupied", "owner": 0, "details": {}}

    # 1. 山 (Mountain) / ピュア山 (Pure_Mountain): インデックス 1..6 が同色
    if e[1] != 0 and all(x == e[1] for x in e[1:7]):
        color = int(e[1])
        sub_filled = all(x != 0 for x in s[2:6])
        is_pure = all(x == color for x in s[2:6])
        if is_pure:
            return {"shape": "Pure_Mountain", "owner": color, "details": {}}
        return {"shape": "Mountain", "owner": color, "details": {"sub_filled": sub_filled}}

    # 2. ウイング (Wing) / ピュアウイング (Pure_Wing): 5マス連続同色 + 片方Cが空
    if e[1] != 0 and e[6] == 0 and all(x == e[1] for x in e[1:6]):
        color = int(e[1])
        is_pure = all(x == color for x in s[2:6])
        return {
            "shape": "Pure_Wing" if is_pure else "Wing",
            "owner": color,
            "details": {
                "empty_c": edge_def["edge"][6],
                "filled_c": edge_def["edge"][1],
                "attack_x": edge_def["x_right"],
                "target_corner": edge_def["corner_right"],
                "opposite_corner": edge_def["corner_left"],
            },
        }
    if e[6] != 0 and e[1] == 0 and all(x == e[6] for x in e[2:7]):
        color = int(e[6])
        is_pure = all(x == color for x in s[2:6])
        return {
            "shape": "Pure_Wing" if is_pure else "Wing",
            "owner": color,
            "details": {
                "empty_c": edge_def["edge"][1],
                "filled_c": edge_def["edge"][6],
                "attack_x": edge_def["x_left"],
                "target_corner": edge_def["corner_left"],
                "opposite_corner": edge_def["corner_right"],
            },
        }

    # 3. ブロック (Block) / ピュアブロック (Pure_Block) / 爆弾 (Bomb): 両Cが空
    if e[1] == 0 and e[6] == 0:
        center4 = e[2:6]
        if all(x != 0 for x in center4):
            if all(x == center4[0] for x in center4):
                color = int(center4[0])
                is_pure = all(x == color for x in s[2:6])
                return {"shape": "Pure_Block" if is_pure else "Block", "owner": color, "details": {}}
            elif center4[0] == center4[3] and center4[1] == center4[2] and center4[0] == -center4[1]:
                return {"shape": "Bomb", "owner": 0, "details": {"outer_color": center4[0], "inner_color": center4[1]}}

    # 4. 単独C打ち (Isolated_C): Cマスに石があり、隣のA・Bマスが空
    if e[1] != 0 and e[2] == 0 and e[3] == 0:
        return {"shape": "Isolated_C", "owner": int(e[1]), "details": {"c_coord": edge_def["edge"][1]}}
    if e[6] != 0 and e[5] == 0 and e[4] == 0:
        return {"shape": "Isolated_C", "owner": int(e[6]), "details": {"c_coord": edge_def["edge"][6]}}

    # 5. Cを含む4石・3石確保型（ストナーのターゲット形）
    if e[1] != 0 and e[5] == 0 and all(x == e[1] for x in e[1:5]):
        return {
            "shape": "C_Four_Line", "owner": int(e[1]),
            "details": {
                "attach_sq": edge_def["edge"][5],
                "hole_sq": edge_def["edge"][6],
                "attack_x": edge_def["x_right"],
                "corner_c_side": edge_def["corner_left"],
                "corner_hole_side": edge_def["corner_right"],
            },
        }
    if e[6] != 0 and e[2] == 0 and all(x == e[6] for x in e[3:7]):
        return {
            "shape": "C_Four_Line", "owner": int(e[6]),
            "details": {
                "attach_sq": edge_def["edge"][2],
                "hole_sq": edge_def["edge"][1],
                "attack_x": edge_def["x_left"],
                "corner_c_side": edge_def["corner_right"],
                "corner_hole_side": edge_def["corner_left"],
            },
        }
    if e[1] != 0 and e[2] == 0 and e[6] == 0 and all(x == e[1] for x in e[3:6]):
        return {
            "shape": "C_Three_One", "owner": int(e[1]),
            "details": {
                "attach_sq": edge_def["edge"][6],
                "hole_sq": edge_def["edge"][2],
                "attack_x": edge_def["x_left"],
                "corner_c_side": edge_def["corner_left"],
                "corner_hole_side": edge_def["corner_right"],
            },
        }
    if e[6] != 0 and e[5] == 0 and e[1] == 0 and all(x == e[6] for x in e[2:5]):
        return {
            "shape": "C_Three_One", "owner": int(e[6]),
            "details": {
                "attach_sq": edge_def["edge"][1],
                "hole_sq": edge_def["edge"][5],
                "attack_x": edge_def["x_right"],
                "corner_c_side": edge_def["corner_right"],
                "corner_hole_side": edge_def["corner_left"],
            },
        }

    return {"shape": "Other", "owner": 0, "details": {}}


def analyze_x_move_and_stoner(
    board_before: np.ndarray, move: Optional[Tuple[int, int]], color: int = 1
) -> Optional[Dict[str, Any]]:
    """
    moveがX打ちである場合、またはストナーの仕掛け/発動である場合の精密判定を行う。
    戻り値: dict (type: str, is_good: bool, target_corner: tuple, details: dict)
    """
    if move is None:
        return None
    board_after, flipped = apply_move(board_before, move, color)

    # ケースA: move自体がXマスの場合
    if move in X_SQUARES:
        corner = X_SQUARES[move]
        if board_before[corner[0], corner[1]] != 0:
            return {"type": "X_After_Corner_Taken", "is_good": True, "corner": corner}

        opp_moves = get_legal_moves(board_after, -color)
        immediate_corner_loss = corner in opp_moves

        adj_edges = [ed for ed in EDGE_DEFINITIONS if corner in (ed["corner_left"], ed["corner_right"])]

        # 1. ウイング攻めのX打ちか検査
        for ed in adj_edges:
            info = classify_single_edge(board_before, ed)
            if info["shape"] in ("Wing", "Pure_Wing") and info["owner"] == -color:
                if info["details"]["attack_x"] == move:
                    b_after_corner, _ = apply_move(board_after, corner, -color)
                    empty_c = info["details"]["empty_c"]
                    if empty_c in get_legal_moves(b_after_corner, color):
                        return {
                            "type": "Wing_Attack_X",
                            "is_good": True,
                            "corner": corner,
                            "edge_name": ed["name"],
                            "wedged_c": empty_c,
                            "opposite_corner": info["details"]["opposite_corner"],
                        }

        # 2. 隣り合う2辺のピュアブロックに対するX打ちか検査
        if len(adj_edges) == 2:
            s1 = classify_single_edge(board_before, adj_edges[0])
            s2 = classify_single_edge(board_before, adj_edges[1])
            if s1["shape"] == "Pure_Block" and s1["owner"] == -color and s2["shape"] == "Pure_Block" and s2["owner"] == -color:
                return {"type": "Double_Pure_Block_X", "is_good": True, "corner": corner}

        # 3. ストナーの仕掛け（対角線通しX打ち）か検査
        diag_coords = DIAGONAL_LINES[move]
        diag_passed = (not immediate_corner_loss) and any(board_after[r, c] == color for r, c in diag_coords)
        if diag_passed:
            for ed in adj_edges:
                info = classify_single_edge(board_after, ed)
                if info["shape"] in ("C_Four_Line", "C_Three_One") and info["owner"] == -color:
                    if info["details"]["attack_x"] == move:
                        attach_sq = info["details"]["attach_sq"]
                        hole_sq = info["details"]["hole_sq"]
                        if attach_sq in get_legal_moves(board_after, color):
                            return {
                                "type": "Stoner_Setup_X",
                                "is_good": True,
                                "corner": corner,
                                "edge_name": ed["name"],
                                "attach_sq": attach_sq,
                                "hole_sq": hole_sq,
                            }

        # 4. 悪手X打ちの分類
        if immediate_corner_loss:
            return {"type": "Bad_X_Immediate_Loss", "is_good": False, "corner": corner}

        can_cut_diagonal = False
        cut_move_example = None
        for om in opp_moves:
            b_opp, opp_flipped = apply_move(board_after, om, -color)
            if any(sq in diag_coords for sq in opp_flipped):
                if corner in get_legal_moves(b_opp, -color):
                    can_cut_diagonal = True
                    cut_move_example = om
                    break
        if can_cut_diagonal:
            return {"type": "Bad_X_Diagonal_Cut_Loss", "is_good": False, "corner": corner, "cut_move": cut_move_example}

        return {"type": "Safe_Or_Endgame_X", "is_good": True, "corner": corner}

    # ケースB: 既にXマスに自分の石がある状態で、辺へ「ストナーの付け手（発動手）」を打つ場合
    for ed in EDGE_DEFINITIONS:
        if move in ed["edge"]:
            info = classify_single_edge(board_before, ed)
            if info["shape"] in ("C_Four_Line", "C_Three_One") and info["owner"] == -color:
                if info["details"]["attach_sq"] == move:
                    ax = info["details"]["attack_x"]
                    if board_before[ax[0], ax[1]] == color:
                        return {
                            "type": "Stoner_Trigger_Attach",
                            "is_good": True,
                            "edge_name": ed["name"],
                            "x_sq": ax,
                            "hole_sq": info["details"]["hole_sq"],
                            "target_corner": info["details"]["corner_c_side"],
                        }
    return None


def compute_stable_discs(board: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    8x8盤面から自分(1)と相手(-1)の確定石ブールマスクを算出する反復伝播アルゴリズム。
    戻り値: (player_stable_mask, opp_stable_mask)
    """
    player_stable = np.zeros((8, 8), dtype=bool)
    opp_stable = np.zeros((8, 8), dtype=bool)
    axes = [((0, 1), (0, -1)), ((1, 0), (-1, 0)), ((1, 1), (-1, -1)), ((1, -1), (-1, 1))]

    for color, mask in [(1, player_stable), (-1, opp_stable)]:
        for r, c in CORNERS:
            if board[r, c] == color:
                mask[r, c] = True

        changed = True
        while changed:
            changed = False
            for r in range(8):
                for c in range(8):
                    if board[r, c] != color or mask[r, c]:
                        continue
                    all_axes_safe = True
                    for (dr1, dc1), (dr2, dc2) in axes:
                        ray1, ray2 = [], []
                        k = 1
                        while 0 <= r + k * dr1 < 8 and 0 <= c + k * dc1 < 8:
                            ray1.append((r + k * dr1, c + k * dc1))
                            k += 1
                        k = 1
                        while 0 <= r + k * dr2 < 8 and 0 <= c + k * dc2 < 8:
                            ray2.append((r + k * dr2, c + k * dc2))
                            k += 1

                        side1_stable = all(mask[nr, nc] for nr, nc in ray1)
                        side2_stable = all(mask[nr, nc] for nr, nc in ray2)
                        line_full = all(board[nr, nc] != 0 for nr, nc in (ray1 + ray2))

                        if not (side1_stable or side2_stable or line_full):
                            all_axes_safe = False
                            break
                    if all_axes_safe:
                        mask[r, c] = True
                        changed = True

    return player_stable, opp_stable


def _empty_adjacent_corner(board: np.ndarray, move: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    """move が空いている隅の隣（X/Cマス）なら、その隅の座標を返す"""
    if move is None:
        return None
    corner = X_SQUARES.get(move) or C_SQUARES.get(move)
    if corner is not None and board[corner[0], corner[1]] == 0:
        return corner
    return None


def evaluate_1gap_attach(board_before: np.ndarray, move: Optional[Tuple[int, int]], player: int = 1) -> List[Dict[str, Any]]:
    """
    辺の1マス空き付け手（1-gap attachment）を判定する。
    着手 move が辺（インデックス 1..6）で、1マス空けた隣に相手の石がある形状。
    - 相手がその空きマス（穴）に打てる場合:
        - 相手が打ったときの開放度が低い（<= 1）なら、相手に安全な中割りを献上する悪手 (ALLOWS_NAKAWARI)
        - 開放度が高いなら相手を外側に引きずり出す手 (EXPOSES_OPPONENT)
    - 相手がその穴に打てない場合:
        - 相手が手出しできない余裕手（手止まり用の1マス空き付け手 SPARE_HOLE）
    """
    if move is None:
        return []
    findings: List[Dict[str, Any]] = []
    for ed in EDGE_DEFINITIONS:
        sqs = ed["edge"]
        if move in sqs[1:7]:
            idx = sqs.index(move)
            b_list = [board_before[cr, cc] for cr, cc in sqs]
            for step in (-2, 2):
                opp_idx = idx + step
                gap_idx = idx + (step // 2)
                if 1 <= opp_idx <= 6 and b_list[opp_idx] == -player and b_list[gap_idx] == 0:
                    gap_sq = sqs[gap_idx]
                    board_next, _ = apply_move(board_before, move, player)
                    opp_legals = get_legal_moves(board_next, -player)
                    if gap_sq not in opp_legals:
                        findings.append({
                            "edge_name": ed["name"],
                            "type": "SPARE_HOLE",
                            "hole": gap_sq,
                            "openness": 0,
                        })
                    else:
                        u_opp, _, _ = compute_openness(board_next, gap_sq, -player)
                        if u_opp <= 1:
                            findings.append({
                                "edge_name": ed["name"],
                                "type": "ALLOWS_NAKAWARI",
                                "hole": gap_sq,
                                "openness": u_opp,
                            })
                        else:
                            findings.append({
                                "edge_name": ed["name"],
                                "type": "EXPOSES_OPPONENT",
                                "hole": gap_sq,
                                "openness": u_opp,
                            })
    return findings


def evaluate_edge_corner_patterns(ctx: MoveContext) -> List[Dict[str, Any]]:
    """辺・隅・X打ち・C打ち・ストナー・確定石に関する戦術パターンを判定する"""
    results: List[Dict[str, Any]] = []
    if ctx.actual_move is None or ctx.best_move is None:
        return results

    act_str = coord_to_str(ctx.actual_move)
    bst_str = coord_to_str(ctx.best_move)

    # [E-05] 隅の取り逃し・相手への隅献上 [Priority: 99 (Tier S)]
    if ctx.best_move in CORNERS and ctx.actual_move not in CORNERS:
        results.append({
            "id": "E-05",
            "name": "隅（コーナー）の取り逃し",
            "priority": 99,
            "explanation": (
                f"この局面では {bst_str} の隅（角）を直接取ることができました。実戦の {act_str} で隅を放置したため、"
                f"確定石の最大の起点を取り逃がす致命的な損失となっています。特別な連打や手止まりの例外を除き、隅は最優先で確保しましょう。"
            ),
        })

    x_act_info = analyze_x_move_and_stoner(ctx.board_before, ctx.actual_move, 1)
    x_bst_info = analyze_x_move_and_stoner(ctx.board_before, ctx.best_move, 1)

    # 最善手も「空き隅の隣（X/C）への着手」または「直後に相手が隅を取れる手」なら、
    # 「危険マスだから悪い」は優劣の理由にならない（終盤は隅を渡す手が最善のこともある）
    act_adj_corner = _empty_adjacent_corner(ctx.board_before, ctx.actual_move)
    bst_adj_corner = _empty_adjacent_corner(ctx.board_before, ctx.best_move)
    best_concedes_corner = any(mv in CORNERS for mv in ctx.opp_moves_after_best)
    both_corner_risky = act_adj_corner is not None and (bst_adj_corner is not None or best_concedes_corner)

    # [E-12] 危険マス同士・隅を渡す手同士の比較 [Priority: 97 (Tier S)]
    if both_corner_risky:
        act_kind = "X" if ctx.actual_move in X_SQUARES else "C"
        if bst_adj_corner is not None:
            bst_kind = "X" if ctx.best_move in X_SQUARES else "C"
            best_desc = f"最善手の {bst_str} も {coord_to_str(bst_adj_corner)} 隅の{bst_kind}マスへの着手"
        else:
            best_desc = f"最善手の {bst_str} も、打った直後に相手が隅を取れる手"
        results.append({
            "id": "E-12",
            "name": "隅を渡す手どうしの打ち順ミス（危険マス同士の比較）",
            "priority": 97,
            "explanation": (
                f"実戦の {act_str} は {coord_to_str(act_adj_corner)} 隅の{act_kind}マスですが、{best_desc}で、"
                f"「危険マスだから悪い」という点では差がありません。"
                f"{ctx.eval_loss:.1f} 石の差は、隅を渡した（または取った）後に、どちらが各エリアの最後の一手（手止まり）を打てるか、"
                f"相手をパスに追い込めるか、で生まれています。"
                + ("終盤では、隅を渡す代わりに手番を整理する手が最善になることがよくあります。" if ctx.empty_count <= 16 else "")
            ),
        })

    # [E-01] 直後に隅を取られる危険なX打ち [Priority: 98 (Tier S)]
    # 実際に相手が直後に隅を取れる場合だけ「直後に隅を取られる」と判定する
    is_bad_x = (
        x_act_info is not None
        and x_act_info.get("type") == "Bad_X_Immediate_Loss"
        and not both_corner_risky
    )
    c_str = coord_to_str(x_act_info["corner"]) if is_bad_x and x_act_info else ""

    if is_bad_x:
        results.append({
            "id": "E-01",
            "name": "直後に隅を取られる危険なX打ち（致命的悪手）",
            "priority": 98,
            "explanation": (
                f"実戦の {act_str} は隅 {c_str} の斜め隣（Xマス）への危険なX打ちであり、"
                f"空いている隅の前のX打ちは相手に隅を明け渡す最も危険な手です。"
                f"最善手の {bst_str} と比較して {ctx.eval_loss:.1f} 石の損失となり、相手に主導権を完全に奪われます。"
                f"ここは {bst_str} と打って辛抱すべき局面でした。"
            ),
        })

    # [E-03] ストナーの罠の発動見逃し・被弾 [Priority: 95 (Tier S)]
    if x_bst_info and x_bst_info["type"] in ("Stoner_Setup_X", "Stoner_Trigger_Attach"):
        if x_bst_info["type"] == "Stoner_Setup_X":
            att_str = coord_to_str(x_bst_info["attach_sq"])
            hole_str = coord_to_str(x_bst_info["hole_sq"])
            results.append({
                "id": "E-03",
                "name": "ストナーの罠（Stoner Trap）の仕掛け見逃し",
                "priority": 95,
                "explanation": (
                    f"相手が{x_bst_info['edge_name']}でC打ちを含む形を作っているため、最善手の {bst_str}（対角線を通すX打ち）から"
                    f"必殺手筋「ストナーの罠」が成立していました。{bst_str} で斜めのラインを通せば、相手が対角線を切った隙に"
                    f"次手で {att_str} へ付け手を打ち、相手が {hole_str} の穴を埋められない（埋めると両隅が通る）ことを利用して"
                    f"確実に辺と隅を奪取できました。"
                ),
            })
        else:
            results.append({
                "id": "E-03",
                "name": "ストナーの罠（付け手発動）の見逃し",
                "priority": 95,
                "explanation": (
                    f"すでに {coord_to_str(x_bst_info['x_sq'])} のX石で対角線が通っているため、最善手の {bst_str} と"
                    f"{x_bst_info['edge_name']}へ付け手を打つことで「ストナーの罠」が爆発していました。"
                    f"相手は穴（{coord_to_str(x_bst_info['hole_sq'])}）に打つとX石が返って両隅を取られるため、"
                    f"一方的に辺の主導権と手数を奪うことができました。"
                ),
            })

    # [E-02] 対角線を切られて隅を取られる危険なX打ち [Priority: 94 (Tier S)]
    if x_act_info and x_act_info["type"] == "Bad_X_Diagonal_Cut_Loss" and not both_corner_risky:
        c_str = coord_to_str(x_act_info["corner"])
        cut_str = coord_to_str(x_act_info["cut_move"])
        results.append({
            "id": "E-02",
            "name": "対角線を切られて隅を取られる危険なX打ち",
            "priority": 94,
            "explanation": (
                f"実戦の {act_str}（X打ち）は一時的に自分の石で斜めの対角線を通しているためすぐには隅を取られませんが、"
                f"直後に相手から {cut_str} などで対角線を切られ（割り込まれ）、次の手で {c_str} の隅を取られてしまいます。"
                f"X打ちでラインを通す際は、相手に対角線を切る手がないか、または切られてもストナー等で反撃できるかを必ず確認しましょう。"
            ),
        })

    # [E-04] ウイング攻めの見逃し [Priority: 93 (Tier S)]
    if x_bst_info and x_bst_info["type"] == "Wing_Attack_X":
        c_str = coord_to_str(x_bst_info["corner"])
        w_str = coord_to_str(x_bst_info["wedged_c"])
        opp_c_str = coord_to_str(x_bst_info["opposite_corner"])
        results.append({
            "id": "E-04",
            "name": "ウイング攻め（X打ちからの辺全奪取）の見逃し",
            "priority": 93,
            "explanation": (
                f"相手が{x_bst_info['edge_name']}に危険な「ウイング」を作っています。ここは最善手の {bst_str}（X打ち）から"
                f"「ウイング攻め」を決める絶好機でした。相手に {c_str} の隅をわざと取らせた直後に {w_str} のCマスへ潜り込むことで、"
                f"反対側の隅 {opp_c_str} とともに{x_bst_info['edge_name']}の確定石を丸ごと奪い取ることができました。"
            ),
        })

    # [E-06] 確定石の大幅増減ミス（やすり攻め見逃し・確定石献上） [Priority: 91 (Tier S)]
    ps_before, os_before = compute_stable_discs(ctx.board_before)
    ps_act, os_act = compute_stable_discs(ctx.board_actual)
    ps_bst, os_bst = compute_stable_discs(ctx.board_best)

    max_os_after_act = int(np.sum(os_act))
    for om in ctx.opp_moves_after_actual:
        b_opp, _ = apply_move(ctx.board_actual, om, -1)
        _, os_tmp = compute_stable_discs(b_opp)
        max_os_after_act = max(max_os_after_act, int(np.sum(os_tmp)))

    max_os_after_bst = int(np.sum(os_bst))
    for om in ctx.opp_moves_after_best:
        b_opp, _ = apply_move(ctx.board_best, om, -1)
        _, os_tmp = compute_stable_discs(b_opp)
        max_os_after_bst = max(max_os_after_bst, int(np.sum(os_tmp)))

    my_stable_gain_bst = int(np.sum(ps_bst)) - int(np.sum(ps_before))
    my_stable_gain_act = int(np.sum(ps_act)) - int(np.sum(ps_before))
    opp_stable_gain_act = max_os_after_act - int(np.sum(os_before))
    opp_stable_gain_bst = max_os_after_bst - int(np.sum(os_before))

    if (my_stable_gain_bst - my_stable_gain_act >= 3) or (opp_stable_gain_act - opp_stable_gain_bst >= 3):
        results.append({
            "id": "E-06",
            "name": "確定石の大幅増減ミス（やすり攻め見逃し・確定石献上）",
            "priority": 91,
            "explanation": (
                f"確定石（絶対に裏返らない石）の収支で大きな差がついています。実戦の {act_str} では相手に確定石を "
                f"{opp_stable_gain_act} 個与えてしまう（自分の増加は {my_stable_gain_act} 個）のに対し、"
                f"最善手の {bst_str} では自分の確定石を {my_stable_gain_bst} 個増やしつつ相手の確定石増加を "
                f"{opp_stable_gain_bst} 個に抑えることができます。隅を取った後は辺に沿って確定石を削り取る「やすり攻め」等を優先しましょう。"
            ),
        })

    # [E-07] 危険な単独C打ち・付け手誘発の悪手 [Priority: 86 (Tier A)]
    if ctx.actual_move in C_SQUARES and ctx.best_move != ctx.actual_move and not both_corner_risky:
        corner = C_SQUARES[ctx.actual_move]
        if ctx.board_before[corner[0], corner[1]] == 0:
            for ed in EDGE_DEFINITIONS:
                if ctx.actual_move in ed["edge"]:
                    info_after = classify_single_edge(ctx.board_actual, ed)
                    if info_after["shape"] == "Isolated_C" and info_after["owner"] == 1:
                        results.append({
                            "id": "E-07",
                            "name": "危険な単独C打ち（孤立C打ちの悪手）",
                            "priority": 86,
                            "explanation": (
                                f"実戦の {act_str} は、隅が空いている状態で辺のCマスに孤立した石を置く「単独C打ち」の悪手です。"
                                f"単独C打ちをすると、相手から隣のAマスやBマスに「付け手」や「1マス空き付け手」を打たれ、"
                                f"{ed['name']}の支配権や隅 {coord_to_str(corner)} を一方的に奪われる原因になります。"
                            ),
                        })
                        break

    # [E-09] 隣り合うピュアブロック形成の悪手 / 崩しXの見逃し [Priority: 84 (Tier A)]
    if x_bst_info and x_bst_info["type"] == "Double_Pure_Block_X":
        results.append({
            "id": "E-09",
            "name": "隣り合うピュアブロック崩し（X打ち）の見逃し",
            "priority": 84,
            "explanation": (
                f"相手が隣り合う2辺で中辺まで一色の「ピュアブロック」を作っています。ここは交点のXマスである {bst_str} に"
                f"飛び込む手が絶好の急所でした。相手が隅を取れば両辺のCマスが両方ともこちらの余裕手となり、"
                f"隅を取らなければこちらが隅を確保できる必勝形を作れました。"
            ),
        })

    # 辺の形状変化の検査
    for ed in EDGE_DEFINITIONS:
        before_shape = classify_single_edge(ctx.board_before, ed)
        act_shape = classify_single_edge(ctx.board_actual, ed)
        bst_shape = classify_single_edge(ctx.board_best, ed)

        # [E-11] 爆弾（Bomb）形の不用意な起爆ミス [Priority: 82 (Tier A)]
        if before_shape["shape"] == "Bomb" and act_shape["shape"] != "Bomb" and bst_shape["shape"] == "Bomb":
            results.append({
                "id": "E-11",
                "name": "爆弾（Bomb）形の不用意な起爆ミス",
                "priority": 82,
                "explanation": (
                    f"{ed['name']}は中央4マスに双方の石が内外で混在する危険な「爆弾形」です。"
                    f"実戦の {act_str} で自分からこの辺（または周辺のX・Cマス）に触れたため、爆弾が起爆して相手に"
                    f"{ed['name']}の主導権を渡してしまいました。最善手 {bst_str} のように他の場所へ打ち、相手から触れさせるのが鉄則です。"
                ),
            })

        # [E-08] ウイング・ピュアウイング形成の悪手 [Priority: 79 (Tier B)]
        if (
            act_shape["shape"] in ("Wing", "Pure_Wing") and act_shape["owner"] == 1
            and before_shape["shape"] not in ("Wing", "Pure_Wing")
            and bst_shape["shape"] not in ("Wing", "Pure_Wing")
        ):
            shape_jp = "ピュアウイング（中辺まで同色のウイング）" if act_shape["shape"] == "Pure_Wing" else "ウイング"
            results.append({
                "id": "E-08",
                "name": f"{shape_jp}形成の悪手",
                "priority": 79,
                "explanation": (
                    f"実戦の {act_str} によって、{ed['name']}に5マス連続の「{shape_jp}」を作ってしまいました。"
                    f"ウイングは片方のCマス（{coord_to_str(act_shape['details']['empty_c'])}）が空いているため、"
                    f"将来相手から {coord_to_str(act_shape['details']['attack_x'])} へのX打ち（ウイング攻め）を狙われる弱点になります。"
                    f"最善手の {bst_str} で辺の悪形化を避けるべきでした。"
                ),
            })

        # [E-10] 山・ピュア山の形成見逃し [Priority: 73 (Tier B)]
        if (
            bst_shape["shape"] in ("Mountain", "Pure_Mountain") and bst_shape["owner"] == 1
            and act_shape["shape"] not in ("Mountain", "Pure_Mountain")
        ):
            shape_jp = "ピュア山（中辺まで同色の山）" if bst_shape["shape"] == "Pure_Mountain" else "山"
            results.append({
                "id": "E-10",
                "name": f"{shape_jp}の形成見逃し（辺の安定化ミス）",
                "priority": 73,
                "explanation": (
                    f"最善手の {bst_str} と打てば、{ed['name']}の6マスを確保した安定形「{shape_jp}」を完成させることができました。"
                    f"特にピュア山は相手のX打ちによる攻撃を完全に無効化できる非常に有利な形です。辺を確保する際は山・ピュア山を目指しましょう。"
                ),
            })

    # [E-14] ストナーの罠の受動的被弾・仕掛け失敗 [Priority: 92 (Tier S)]
    for om in ctx.opp_moves_after_actual:
        x_opp_info = analyze_x_move_and_stoner(ctx.board_actual, om, -1)
        if x_opp_info and x_opp_info.get("type") in ("Stoner_Setup_X", "Stoner_Trigger_Attach"):
            results.append({
                "id": "E-14",
                "name": "ストナーの罠の受動的被弾（相手の手筋成立）",
                "priority": 92,
                "explanation": (
                    f"実戦の {act_str} によって、相手から {coord_to_str(om)} への手筋で「ストナーの罠」が成立してしまいます。"
                    f"相手に辺の主導権と隅を一方的に奪われる致命的な隙を与えました。最善手の {bst_str} で防ぐべきでした。"
                ),
            })
            break

    # [E-13] 1マス空き付け手による中割り献上・辺の悪形化 [Priority: 83 (Tier A)]
    act_1gap = evaluate_1gap_attach(ctx.board_before, ctx.actual_move, 1)
    bst_1gap = evaluate_1gap_attach(ctx.board_before, ctx.best_move, 1)
    bad_attach = next((g for g in act_1gap if g["type"] == "ALLOWS_NAKAWARI"), None)
    if bad_attach and ctx.actual_move != ctx.best_move:
        hole_s = coord_to_str(bad_attach["hole"])
        results.append({
            "id": "E-13",
            "name": "1マス空き付け手による中割り献上・辺の悪形化",
            "priority": 83,
            "explanation": (
                f"実戦の {act_str} は{bad_attach['edge_name']}で相手の石から1マス空けて打つ「1マス空き付け手」ですが、"
                f"相手から間（{hole_s}）に割り込まれると、相手に開放度の低い安全な手（中割り）を献上してしまいます。"
                f"最善手の {bst_str} のように、相手に好手を与えない安全な手を選ぶべきでした。"
            ),
        })

    # [E-15] 余裕手・手止まり用の1マス空き付け手見逃し [Priority: 80 (Tier B)]
    good_attach = next((g for g in bst_1gap if g["type"] == "SPARE_HOLE"), None)
    if good_attach and not bad_attach and ctx.actual_move != ctx.best_move:
        hole_s = coord_to_str(good_attach["hole"])
        results.append({
            "id": "E-15",
            "name": "余裕手・手止まり用の1マス空き付け手見逃し",
            "priority": 80,
            "explanation": (
                f"最善手の {bst_str} は{good_attach['edge_name']}で相手の石と1マス空けて打つ好手筋（1マス空き付け手）でした。"
                f"相手は間の {hole_s} に直接打つことができないため、相手に手番を渡さず自分の余裕手を増やすことができます。"
                f"実戦の {act_str} ではこの急所を見逃してしまいました。"
            ),
        })

    # [E-16] ピュアブロック・爆弾形成の急所見逃し [Priority: 81 (Tier B)]
    for ed in EDGE_DEFINITIONS:
        bst_shape_e = classify_single_edge(ctx.board_best, ed)
        act_shape_e = classify_single_edge(ctx.board_actual, ed)
        before_shape_e = classify_single_edge(ctx.board_before, ed)
        if (
            bst_shape_e["shape"] in ("Pure_Block", "Block", "Bomb")
            and act_shape_e["shape"] not in ("Pure_Block", "Block", "Bomb")
            and before_shape_e["shape"] not in ("Pure_Block", "Block", "Bomb")
            and ctx.best_move in ed["edge"]
        ):
            shape_name = "ピュアブロック" if bst_shape_e["shape"] == "Pure_Block" else ("爆弾（Bomb）" if bst_shape_e["shape"] == "Bomb" else "ブロック")
            results.append({
                "id": "E-16",
                "name": f"{shape_name}形成の急所見逃し",
                "priority": 81,
                "explanation": (
                    f"最善手の {bst_str} と打てば、{ed['name']}の中央に安定した「{shape_name}」を完成させることができました。"
                    f"ピュアブロックや爆弾は相手から手出しされにくく、辺の主導権を握る強力な拠点になります。"
                    f"実戦の {act_str} ではこの辺の急所を見逃してしまいました。"
                ),
            })
            break

    return results


# ==============================================================================
# 4. 終盤（残り空きマス16マス以下）特有の戦術パターン判定
# ==============================================================================

def get_empty_regions_bfs(board: np.ndarray) -> List[Set[Tuple[int, int]]]:
    """
    8近傍BFSにより盤面上の空きマス(0)を連結領域ごとに分割する。
    戻り値: list of set of (r, c)
    """
    visited: Set[Tuple[int, int]] = set()
    regions: List[Set[Tuple[int, int]]] = []
    for r in range(8):
        for c in range(8):
            if board[r, c] == 0 and (r, c) not in visited:
                region: Set[Tuple[int, int]] = set()
                q: deque[Tuple[int, int]] = deque([(r, c)])
                visited.add((r, c))
                while q:
                    cr, cc = q.popleft()
                    region.add((cr, cc))
                    for nr, nc in get_neighbors_8(cr, cc):
                        if board[nr, nc] == 0 and (nr, nc) not in visited:
                            visited.add((nr, nc))
                            q.append((nr, nc))
                regions.append(region)
    return regions


def find_region_of_move(
    regions: List[Set[Tuple[int, int]]], move: Optional[Tuple[int, int]]
) -> Set[Tuple[int, int]]:
    """指定座標moveが属する空きマス領域(set)を返す"""
    if move is None:
        return set()
    for reg in regions:
        if move in reg:
            return reg
    return set()


def evaluate_endgame_patterns(ctx: MoveContext) -> List[Dict[str, Any]]:
    """終盤（および中盤終盤移行期）のパリティ・手止まり・大量取りタイミングを判定する"""
    results: List[Dict[str, Any]] = []
    if ctx.actual_move is None or ctx.best_move is None:
        return results

    act_str = coord_to_str(ctx.actual_move)
    bst_str = coord_to_str(ctx.best_move)

    # [G-04] 終盤前の早すぎる大量取りミス [Priority: 76 (Tier B)]
    if 17 <= ctx.empty_count <= 26:
        my_moves_after_act = get_legal_moves(ctx.board_actual, 1)
        if (
            len(ctx.flipped_actual) >= 6
            and len(ctx.flipped_actual) - len(ctx.flipped_best) >= 3
            and len(ctx.opp_moves_after_actual) >= len(my_moves_after_act) + 3
        ):
            results.append({
                "id": "G-04",
                "name": "終盤前の早すぎる大量取りミス（手数枯渇）",
                "priority": 76,
                "explanation": (
                    f"まだ空きマスが {ctx.empty_count} マス残っている段階で、実戦の {act_str} は一度に "
                    f"{len(ctx.flipped_actual)} 枚もの石を取りすぎてしまいました。終盤に入る前に自分の石を増やしすぎると、"
                    f"相手の手数が {len(ctx.opp_moves_after_actual)} 箇所に増える一方で自分の打てる場所が枯渇し、"
                    f"終盤で一方的に連打を浴びてしまいます。ここは {bst_str}（{len(ctx.flipped_best)}枚返し）で我慢すべき局面でした。"
                ),
            })

    if ctx.empty_count > 16:
        return results

    regions_before = get_empty_regions_bfs(ctx.board_before)
    reg_act = find_region_of_move(regions_before, ctx.actual_move)
    reg_bst = find_region_of_move(regions_before, ctx.best_move)

    # [G-01] 手止まり・連打の見逃し [Priority: 89 (Tier A)]
    rem_reg_bst = reg_bst - {ctx.best_move} if ctx.best_move else set()
    rem_reg_act = reg_act - {ctx.actual_move} if ctx.actual_move else set()

    bst_is_1sq_wedge = (len(reg_bst) == 1)
    bst_creates_lockout = (
        len(rem_reg_bst) >= 1
        and all(sq not in ctx.opp_moves_after_best for sq in rem_reg_bst)
        and any(sq in get_legal_moves(ctx.board_best, 1) for sq in rem_reg_bst)
    )
    act_is_wedge = (len(reg_act) == 1) or (
        len(rem_reg_act) >= 1 and all(sq not in ctx.opp_moves_after_actual for sq in rem_reg_act)
    )

    if (bst_is_1sq_wedge or bst_creates_lockout) and not act_is_wedge:
        if bst_is_1sq_wedge:
            results.append({
                "id": "G-01",
                "name": "1マス空き手止まりの見逃し",
                "priority": 89,
                "explanation": (
                    f"終盤では各エリアの最後の1手である「手止まり」を打つことが最優先です。最善手の {bst_str} は"
                    f"1マス空きを埋める手止まりの好手であり、周囲の石を確定させつつ次のエリアを選ぶ主導権を握ることができました。"
                ),
            })
        else:
            results.append({
                "id": "G-01",
                "name": "連打・手止まり形成（相手の着手封じ）の見逃し",
                "priority": 89,
                "explanation": (
                    f"最善手の {bst_str} は、打った後に相手がその空き領域（残り {len(rem_reg_bst)} マス）に打てなくなり、"
                    f"自分だけが続けて打てる「連打（手止まり確定）」の決め手でした。実戦の {act_str} では相手に反撃の手番を与えてしまっています。"
                ),
            })

    # [G-02] 奇数空き領域の放棄と偶数領域への着手（偶数理論ミス） [Priority: 88 (Tier A)]
    if len(reg_bst) % 2 == 1 and len(reg_act) % 2 == 0 and reg_bst != reg_act:
        results.append({
            "id": "G-02",
            "name": "奇数空き領域の放棄と偶数領域への着手（偶数理論ミス）",
            "priority": 88,
            "explanation": (
                f"終盤の「偶数理論」に反する着手です。実戦の {act_str} は {len(reg_act)} マス空きの「偶数空きエリア」に"
                f"自分から手を出したため、残り {len(reg_act)-1} マス（奇数空き）となって相手にそのエリアの手止まりを献上してしまいます。"
                f"最善手の {bst_str} のように、{len(reg_bst)} マス空きの「奇数空きエリア」から先に打つことで、"
                f"自分が各エリアの最終手（手止まり）を独占できました。"
            ),
        })

    # [G-03] 相手が打てない奇数空きの温存ミス（打つ順番の誤り） [Priority: 83 (Tier A)]
    opp_moves_before = set(get_legal_moves(ctx.board_before, -1))
    if len(reg_act) % 2 == 1 and reg_act != reg_bst:
        opp_can_play_reg_act = any(sq in opp_moves_before for sq in reg_act)
        opp_can_play_reg_bst = any(sq in opp_moves_before for sq in reg_bst)
        if not opp_can_play_reg_act and opp_can_play_reg_bst:
            results.append({
                "id": "G-03",
                "name": "相手が打てない奇数空きの温存ミス（着手順序ミス）",
                "priority": 83,
                "explanation": (
                    f"実戦の {act_str} が属する {len(reg_act)} マス空きエリアは、相手からは打つことができない自分専用の奇数空きです。"
                    f"いつでも自分から打てるエリアを急いで埋める必要はありません。ここは相手も打てる {bst_str} のエリアから先に消化し、"
                    f"{act_str} のエリアは最後まで温存して手止まりを増やすのが終盤の正しい手順です。"
                ),
            })

    # [G-05] 終盤の実石稼ぎ不足（多方向大量取りの見逃し） [Priority: 71 (Tier B)]
    if ctx.empty_count <= 12 and len(ctx.flipped_best) - len(ctx.flipped_actual) >= 3:
        results.append({
            "id": "G-05",
            "name": "終盤の実石稼ぎ不足（大量取りの見逃し）",
            "priority": 71,
            "explanation": (
                f"残り {ctx.empty_count} マスの終盤では、中盤までの「少なく返す」セオリーから「返せる石を最大限に稼ぐ」思考へ"
                f"切り替える必要があります。実戦の {act_str} は {len(ctx.flipped_actual)} 枚しか返していませんが、"
                f"最善手の {bst_str} は {ctx.dirs_best} 方向に {len(ctx.flipped_best)} 枚の石を一気に裏返し、"
                f"そのまま最終的な石数を大きく増やすことができました。"
            ),
        })

    # [G-06] 終盤のパス追い込み被弾（手止まり・主導権完全喪失） [Priority: 93 (Tier S)]
    if ctx.opp_moves_after_actual and ctx.eval_loss >= 3.0:
        causes_lockout = False
        lockout_reply = None
        for om in ctx.opp_moves_after_actual:
            b_after_om, _ = apply_move(ctx.board_actual, om, -1)
            if len(get_legal_moves(b_after_om, 1)) == 0 and len(get_legal_moves(b_after_om, -1)) > 0:
                causes_lockout = True
                lockout_reply = om
                break
        if causes_lockout and lockout_reply:
            results.append({
                "id": "G-06",
                "name": "終盤のパス追い込み被弾（手止まり・主導権完全喪失）",
                "priority": 93,
                "explanation": (
                    f"実戦の {act_str} を打った後、相手に {coord_to_str(lockout_reply)} に打ち返されると、"
                    f"自分は打てる合法手が1箇所もなくなり「強制パス」に追い込まれてしまいます。"
                    f"終盤でパスが発生すると、相手に残りの空きマスを全て独占（手止まり総取り）され、"
                    f"一方的に石を奪われる壊滅的な展開になります。最善手 {bst_str} ならばパスを回避できました。"
                ),
            })

    # [G-07] ハイパーパリティ崩れ・複数エリアの着手順ミス [Priority: 88 (Tier A)]
    if len(regions_before) >= 2 and reg_act != reg_bst:
        act_rem = len(reg_act) - 1
        bst_rem = len(reg_bst) - 1
        if act_rem % 2 == 1 and bst_rem % 2 == 0:
            results.append({
                "id": "G-07",
                "name": "ハイパーパリティ崩れ・複数エリアの着手順ミス",
                "priority": 88,
                "explanation": (
                    f"盤上に複数の空きエリア（{len(regions_before)}領域）が存在する終盤では、エリアごとの偶奇（ハイパーパリティ）を"
                    f"正確にコントロールする必要があります。実戦の {act_str} は残り奇数マス（相手に手止まりを渡す）にしてしまう着手でした。"
                    f"最善手の {bst_str} のエリアから先に打つことで、相手に打つ場所を強制し、すべてのエリアで最後の一手（手止まり）を"
                    f"確保するパリティ勝利を確定できました。"
                ),
            })

    return results


# ==============================================================================
# 5. ゲーム進行フェーズと戦術評価因子の重み（序盤→終盤で重視する観点が入れ替わる）
# ==============================================================================

FACTOR_KEYS: List[str] = ["mobility", "edge", "stable", "parity", "discs"]
FACTOR_LABELS: Dict[str, str] = {
    "mobility": "開放度・モビリティ",
    "edge": "辺の形・罠",
    "stable": "確定石・隅",
    "parity": "パリティ・手止まり",
    "discs": "実石数差",
}
# パターンに当てはまらなかった悪手に付ける、原因因子別の弱点タグ
FACTOR_TAGS: Dict[str, str] = {
    "mobility": "開放度・手数管理ミス",
    "edge": "辺の形の判断ミス",
    "stable": "確定石・隅の判断ミス",
    "parity": "偶数理論・手止まりミス",
    "discs": "石数の稼ぎ不足",
}

# 4フェーズの定義と、各フェーズで重視する因子の重み（0〜100）。
# 重みは Egaroucid の60フェーズ評価構造と各局面セオリーに基づく相対値で、
# anchor（各フェーズの代表的な空きマス数）の間は線形補間する。
PHASES: List[Dict[str, Any]] = [
    {
        "key": "opening", "name": "序盤", "min_empty": 40, "max_empty": 59, "anchor": 50.0,
        "focus": "開放度0の中割り徹底", "advice": "外壁化・手数の減少を抑制",
        "weights": {"mobility": 90, "edge": 20, "stable": 30, "parity": 5, "discs": 0},
    },
    {
        "key": "early_mid", "name": "中盤前半", "min_empty": 25, "max_empty": 39, "anchor": 32.0,
        "focus": "辺の死活・罠構築", "advice": "ストナー警戒・ウイング攻め",
        "weights": {"mobility": 60, "edge": 85, "stable": 65, "parity": 20, "discs": 5},
    },
    {
        "key": "late_mid", "name": "中盤後半", "min_empty": 17, "max_empty": 24, "anchor": 20.5,
        "focus": "確定石の急速拡大", "advice": "隅の獲得・対角線死守",
        "weights": {"mobility": 25, "edge": 65, "stable": 90, "parity": 70, "discs": 25},
    },
    {
        "key": "endgame", "name": "終盤", "min_empty": 1, "max_empty": 16, "anchor": 8.0,
        "focus": "偶数理論・手止まり", "advice": "奇数空き先着・逆偶数",
        "weights": {"mobility": 5, "edge": 20, "stable": 80, "parity": 95, "discs": 80},
    },
]

# 優先度 → Tier（解説の見出しに出す）
TIER_LABELS: List[Tuple[int, str, str]] = [
    (90, "S", "致命的損失"),
    (80, "A", "戦略的破綻"),
    (70, "B", "構造的不利"),
    (0, "C", "改善の余地"),
]

# 損失がこれ未満の手は「悪手」と呼ばず、形の注意点として扱う（設計書 5.1）
SMALL_LOSS = 2.0


def get_phase(empty_count: int) -> Dict[str, Any]:
    """空きマス数から4フェーズのどれかを返す"""
    for ph in PHASES:
        if ph["min_empty"] <= empty_count <= ph["max_empty"]:
            return ph
    return PHASES[0] if empty_count > 59 else PHASES[-1]


def phase_weights(empty_count: int) -> Dict[str, float]:
    """空きマス数に応じた因子の重み（フェーズの代表点の間を線形補間）"""
    anchors = sorted(PHASES, key=lambda p: -p["anchor"])  # 空きが多い順
    if empty_count >= anchors[0]["anchor"]:
        return {k: float(v) for k, v in anchors[0]["weights"].items()}
    if empty_count <= anchors[-1]["anchor"]:
        return {k: float(v) for k, v in anchors[-1]["weights"].items()}
    for hi, lo in zip(anchors, anchors[1:]):
        if lo["anchor"] <= empty_count <= hi["anchor"]:
            t = (hi["anchor"] - empty_count) / (hi["anchor"] - lo["anchor"])
            return {k: hi["weights"][k] + (lo["weights"][k] - hi["weights"][k]) * t for k in FACTOR_KEYS}
    return {k: float(v) for k, v in anchors[-1]["weights"].items()}


def tier_of(priority: float) -> Tuple[str, str]:
    for threshold, tier, label in TIER_LABELS:
        if priority >= threshold:
            return tier, label
    return "C", "改善の余地"


def pattern_factor(pattern_id: str, empty_count: int) -> str:
    """戦術パターンがどの評価因子に属するか（フェーズ重みの適用先）"""
    if pattern_id == "G-04":
        return "mobility"
    if pattern_id == "G-05":
        return "discs"
    if pattern_id == "E-12":
        return "parity" if empty_count <= 16 else "stable"
    if pattern_id in ("E-01", "E-02", "E-05", "E-06"):
        return "stable"
    if pattern_id.startswith("E"):
        return "edge"
    if pattern_id.startswith("G"):
        return "parity"
    return "mobility"


_SHAPE_JP = {
    "Wing": "ウイング", "Pure_Wing": "ピュアウイング", "Isolated_C": "単独C打ち",
    "C_Four_Line": "Cを含む4石（ストナーの標的形）", "C_Three_One": "3:1片方C打ち（ストナーの標的形）",
    "Pure_Block": "ピュアブロック", "Mountain": "山", "Pure_Mountain": "ピュア山", "Block": "ブロック",
}
_BAD_EDGE_SHAPES = ("Wing", "Pure_Wing", "Isolated_C", "C_Four_Line", "C_Three_One", "Pure_Block")
_GOOD_EDGE_SHAPES = ("Mountain", "Pure_Mountain", "Block")


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def evaluate_phase_factors(ctx: MoveContext) -> Dict[str, Dict[str, Any]]:
    """
    実戦手と最善手を5つの評価因子で比べ、「実戦手がどれだけ劣っているか」を 0〜1 と説明文で返す。
    パターン（M/E/G）に当てはまらない悪手でも、どの観点で損をしたかを数値で示すために使う。
    """
    out: Dict[str, Dict[str, Any]] = {}
    act, bst = ctx.actual_move, ctx.best_move
    act_s, bst_s = coord_to_str(act), coord_to_str(bst)

    # 1. 開放度・モビリティ
    u_act, _, _ = compute_openness(ctx.board_before, act, 1)
    u_bst, _, _ = compute_openness(ctx.board_before, bst, 1)
    u_act_v = 0 if u_act == 99 else u_act
    u_bst_v = 0 if u_bst == 99 else u_bst
    n_act, n_bst = len(ctx.opp_moves_after_actual), len(ctx.opp_moves_after_best)
    out["mobility"] = {
        # 開放度・相手の手数とも1の差は誤差とみなし、2以上の差から理由として扱う
        "score": _clamp01((max(u_act_v - u_bst_v - 1, 0) + max(n_act - n_bst - 1, 0)) / 4.0),
        "detail": f"開放度 実戦 {act_s}={u_act_v} ／ 最善 {bst_s}={u_bst_v}、直後に相手が打てる場所 実戦 {n_act}箇所 ／ 最善 {n_bst}箇所",
    }

    # 2. 辺の形・罠
    edge_notes: List[str] = []
    for ed in EDGE_DEFINITIONS:
        sa = classify_single_edge(ctx.board_actual, ed)
        sb = classify_single_edge(ctx.board_best, ed)
        if sa["owner"] == 1 and sa["shape"] in _BAD_EDGE_SHAPES and sb["shape"] != sa["shape"]:
            edge_notes.append(f"実戦では{ed['name']}が自分の「{_SHAPE_JP[sa['shape']]}」（狙われやすい形）になる")
        if sb["owner"] == 1 and sb["shape"] in _GOOD_EDGE_SHAPES and sa["shape"] != sb["shape"]:
            edge_notes.append(f"最善なら{ed['name']}が安定した「{_SHAPE_JP[sb['shape']]}」になる")

    act_1gap_f = evaluate_1gap_attach(ctx.board_before, act, 1)
    bst_1gap_f = evaluate_1gap_attach(ctx.board_before, bst, 1)
    if any(g["type"] == "ALLOWS_NAKAWARI" for g in act_1gap_f):
        edge_notes.append("実戦は1マス空き付け手で相手に中割りを許す")
    if any(g["type"] == "SPARE_HOLE" for g in bst_1gap_f):
        edge_notes.append("最善なら相手が打てない1マス空き付け手（余裕手）になる")

    out["edge"] = {
        "score": _clamp01(0.4 * len(edge_notes)),
        "detail": "、".join(edge_notes) if edge_notes else "辺の形の差はなし",
    }

    # 3. 確定石・隅（相手の1手応手後まで見る）
    ps_before, os_before = compute_stable_discs(ctx.board_before)
    ps_act, os_act = compute_stable_discs(ctx.board_actual)
    ps_bst, os_bst = compute_stable_discs(ctx.board_best)

    def max_opp_stable(board_after: np.ndarray, opp_moves: List[Tuple[int, int]], base: int) -> int:
        best = base
        for om in opp_moves:
            b2, _ = apply_move(board_after, om, -1)
            best = max(best, int(np.sum(compute_stable_discs(b2)[1])))
        return best

    my_gain_a = int(np.sum(ps_act)) - int(np.sum(ps_before))
    my_gain_b = int(np.sum(ps_bst)) - int(np.sum(ps_before))
    opp_gain_a = max_opp_stable(ctx.board_actual, ctx.opp_moves_after_actual, int(np.sum(os_act))) - int(np.sum(os_before))
    opp_gain_b = max_opp_stable(ctx.board_best, ctx.opp_moves_after_best, int(np.sum(os_bst))) - int(np.sum(os_before))
    stable_diff = (my_gain_b - my_gain_a) + (opp_gain_a - opp_gain_b)
    corners_given = [c for c in CORNERS if c in ctx.opp_moves_after_actual and c not in ctx.opp_moves_after_best]
    best_takes_corner = bst in CORNERS and act not in CORNERS
    stable_notes = [f"確定石の収支 実戦 自分{my_gain_a:+d}・相手{opp_gain_a:+d} ／ 最善 自分{my_gain_b:+d}・相手{opp_gain_b:+d}"]
    if corners_given:
        stable_notes.append("実戦の後は相手が隅 " + "・".join(coord_to_str(c) for c in corners_given) + " を取れる")
    if best_takes_corner:
        stable_notes.append(f"最善手 {bst_s} は隅")
    out["stable"] = {
        # 確定石の収支は2個以上の差から理由として扱う
        "score": _clamp01(max(stable_diff - 1, 0) / 5.0 + 0.5 * len(corners_given) + (0.5 if best_takes_corner else 0.0)),
        "detail": "、".join(stable_notes),
    }

    # 4. パリティ・手止まり（空き24マス以下で意味を持つ）
    parity_score = 0.0
    parity_notes: List[str] = []
    if ctx.empty_count <= 24:
        regions = get_empty_regions_bfs(ctx.board_before)
        reg_a = find_region_of_move(regions, act)
        reg_b = find_region_of_move(regions, bst)
        if reg_a != reg_b:
            pa = "奇数" if len(reg_a) % 2 else "偶数"
            pb = "奇数" if len(reg_b) % 2 else "偶数"
            parity_notes.append(f"実戦は{len(reg_a)}マス空き（{pa}）、最善は{len(reg_b)}マス空き（{pb}）のエリアに着手")
            if len(reg_a) % 2 == 0 and len(reg_b) % 2 == 1:
                parity_score += 0.6
        rem_b = reg_b - {bst}
        rem_a = reg_a - {act}
        lock_b = len(reg_b) == 1 or (rem_b and all(sq not in ctx.opp_moves_after_best for sq in rem_b))
        lock_a = len(reg_a) == 1 or (rem_a and all(sq not in ctx.opp_moves_after_actual for sq in rem_a))
        if lock_b and not lock_a:
            parity_score += 0.6
            parity_notes.append(f"最善 {bst_s} はそのエリアの手止まり（相手が続けて打てない）になる")
        # 最善手なら相手がパス（自分の連打）になるのに、実戦手では相手に手番を渡している
        if not ctx.opp_moves_after_best and ctx.opp_moves_after_actual:
            parity_score += 0.5
            parity_notes.append(f"最善 {bst_s} なら相手はパスになる")
    out["parity"] = {
        "score": _clamp01(parity_score),
        "detail": "、".join(parity_notes) if parity_notes else "エリアの偶奇・手止まりの差はなし",
    }

    # 5. 実石数差
    d_flip = len(ctx.flipped_best) - len(ctx.flipped_actual)
    out["discs"] = {
        # 1〜2枚の差は理由にしない（終盤の大量取りの差は3枚以上から）
        "score": _clamp01((d_flip - 2) / 4.0),
        "detail": f"返す石 実戦 {len(ctx.flipped_actual)}枚 ／ 最善 {len(ctx.flipped_best)}枚",
    }
    return out


def _describe_reply(board_after: np.ndarray, reply: Optional[Tuple[int, int]]) -> str:
    """エンジンが選んだ相手の応手（咎め手）を、戦術的な意味つきで短く説明する（盤面は手番側=1に正規化済み）"""
    if reply is None:
        return "パス"
    rs = coord_to_str(reply)
    if reply in CORNERS:
        return f"{rs}（隅を取る）"
    flipped, _ = get_flipped_discs(board_after, reply, -1)
    if not flipped:
        return rs
    if reply in X_SQUARES and board_after[X_SQUARES[reply][0], X_SQUARES[reply][1]] == 0:
        return f"{rs}（X打ち）"
    if reply in C_SQUARES and board_after[C_SQUARES[reply][0], C_SQUARES[reply][1]] == 0:
        return f"{rs}（C打ち）"
    u, _, _ = compute_openness(board_after, reply, -1)
    if u <= 1:
        return f"{rs}（開放度{u}の中割り）"
    return f"{rs}（{len(flipped)}枚返し・開放度{u}）"


_FACTOR_EXPLAIN = {
    "mobility": "{phase}は相手の打てる場所を減らし、自分の石を内側にとどめるのが最優先です。{act} はこの点で {bst} に劣ります（{detail}）。",
    "edge": "{phase}は辺の形の良し悪しが勝負を分けます。{detail}。",
    "stable": "{phase}は確定石と隅の取り合いが中心になります。{detail}。",
    "parity": "{phase}は「どのエリアで誰が最後に打つか」（偶数理論・手止まり）が石数を決めます。{detail}。",
    "discs": "{phase}は返せる石の数がそのまま最終石数に直結します。{detail}。",
}


@dataclass
class TacticalAnalysisResult:
    """局面の戦術解析結果（検出された全パターンと調停結果）"""
    primary_pattern: Optional[Dict[str, Any]]
    secondary_pattern: Optional[Dict[str, Any]]
    all_detected_patterns: List[Dict[str, Any]]
    commentary: str
    tags: List[str]
    phase_name: str = ""
    primary_factor: str = ""   # FACTOR_KEYS のいずれか（主な理由がどの観点か）
    factors: Optional[Dict[str, Dict[str, Any]]] = None


def analyze_tactical_patterns(
    board_before: List[List[int]],
    actual_move: Optional[Tuple[int, int]],
    best_move: Optional[Tuple[int, int]],
    eval_loss: float,
    player: int = 1,
    opp_reply_actual: Optional[Tuple[int, int]] = None,
    opp_reply_best: Optional[Tuple[int, int]] = None,
) -> TacticalAnalysisResult:
    """
    指定局面を戦術理論に基づいて解析し、調停されたパターンと解説文を返す。
    手番プレイヤーを常に 1 に正規化して処理するため、黒番・白番問わず均一に機能する。

    - ゲーム進行フェーズ（序盤/中盤前半/中盤後半/終盤）に応じて評価因子の重みを変え、
      パターンの優先度にも反映する（例: 序盤は開放度、終盤は手止まり・石数が重い）。
    - パターンに当てはまらない手も、5因子（開放度・辺・確定石・パリティ・石数）の比較で理由を示す。
    - 損失が2石未満の手は「悪手」と呼ばず、形の注意点として扱う。
    - opp_reply_*（エンジンが読んだ相手の最善応手＝咎め手）が渡されれば、具体的な応手として示す。
    """
    act_str = coord_to_str(actual_move)
    bst_str = coord_to_str(best_move)

    if actual_move == best_move or eval_loss <= 0:
        return TacticalAnalysisResult(
            primary_pattern=None,
            secondary_pattern=None,
            all_detected_patterns=[],
            commentary="最善手です。局面の急所を的確に捉えています。",
            tags=["最善手"],
        )

    # 手番プレイヤー視点に正規化（手番側=1, 相手=-1）
    board_norm = np.array(board_before, dtype=int) * player
    ctx = MoveContext(board_norm, actual_move, best_move, eval_loss)

    phase = get_phase(ctx.empty_count)
    weights = phase_weights(ctx.empty_count)
    factors = evaluate_phase_factors(ctx)

    detected: List[Dict[str, Any]] = []
    detected.extend(evaluate_edge_corner_patterns(ctx))
    detected.extend(evaluate_endgame_patterns(ctx))
    detected.extend(evaluate_midgame_mobility_patterns(ctx))

    # 優先度調停: パターン本来の優先度に、そのフェーズでの因子の重みを加味する（±10程度）
    for d in detected:
        d["factor"] = pattern_factor(d["id"], ctx.empty_count)
        d["score"] = d["priority"] + (weights[d["factor"]] - 50.0) * 0.2
    detected.sort(key=lambda x: x["score"], reverse=True)

    small = eval_loss < SMALL_LOSS
    if small:
        severity = "ほぼ互角（許容範囲）"
    else:
        severity = "致命的な悪手" if eval_loss >= 12 else ("大きな悪手" if eval_loss >= 6 else "疑問手（微損）")
    loss_s = f"{eval_loss:.1f}" if small else f"{int(round(eval_loss))}"
    header = (
        f"【判定：{severity}（予測最終石差 -{loss_s} 石）】実戦手：{act_str} ／ 最善手：{bst_str}\n"
        f"【局面フェーズ：{phase['name']}（空き{ctx.empty_count}マス）｜この時期の重視点：{phase['focus']}・{phase['advice']}】\n"
    )

    tags: List[str] = []
    for d in detected:
        # パターン名を短縮タグ化
        tname = d["name"].split("（")[0].replace("の見逃し", "").replace("ミス", "")
        if len(tname) <= 2:
            # 「隅（コーナー）の取り逃し」→「隅」のように短くなりすぎる名前は、括弧だけ外して使う
            tname = re.sub(r"（[^）]*）", "", d["name"])
        if tname not in tags:
            tags.append(tname)

    # 因子別の比較（重み × 劣り具合）
    weighted = sorted(
        ((k, factors[k]["score"] * weights[k] / 100.0) for k in FACTOR_KEYS),
        key=lambda kv: kv[1], reverse=True,
    )
    factor_lines = [
        f"- {FACTOR_LABELS[k]}（重み{weights[k]:.0f}）: {factors[k]['detail']}"
        for k, w in weighted if factors[k]["score"] > 0
    ]

    primary: Optional[Dict[str, Any]] = None
    secondary: Optional[Dict[str, Any]] = None
    if detected:
        primary = detected[0]
        primary_factor = primary["factor"]
        tier, tier_label = tier_of(primary["priority"])
        title = "形の上での注意点（損失は小さい）" if small else "主な理由"
        commentary = f"{header}■ {title}（Tier {tier}：{tier_label}｜{primary['name']}）：\n{primary['explanation']}"
        if small:
            commentary += f"\nただし、この局面での損失は {eval_loss:.1f} 石と小さく、形勢を大きく損なう手ではありません。"
        if (
            len(detected) >= 2 and detected[1]["priority"] >= 70
            and detected[1]["id"][0] != primary["id"][0]
        ):
            secondary = detected[1]
            s_tier, s_label = tier_of(secondary["priority"])
            commentary += f"\n\n■ 補足ポイント（Tier {s_tier}：{s_label}｜{secondary['name']}）：\n{secondary['explanation']}"
    else:
        top_key, top_w = weighted[0]
        if top_w >= 0.15 and factors[top_key]["score"] > 0:
            primary_factor = top_key
            tags = [FACTOR_TAGS[top_key]]
            reason = _FACTOR_EXPLAIN[top_key].format(
                phase=phase["name"], act=act_str, bst=bst_str, detail=factors[top_key]["detail"]
            )
            title = "形の上での注意点（損失は小さい）" if small else f"主な理由（{FACTOR_LABELS[top_key]}）"
            commentary = f"{header}■ {title}：\n{reason}"
        else:
            primary_factor = ""
            tags = ["形勢判断ミス"]
            commentary = (
                f"{header}■ 主な理由：\n"
                f"開放度・辺の形・確定石・偶奇・返す石の数といった目に見える形の差はほとんどなく、"
                f"数手先の読みで {loss_s} 石の差がつく局面です。下のエンジンの応手や読み筋で、相手が何を狙っているかを確認しましょう。"
            )
        if small:
            commentary += f"\nこの局面での損失は {eval_loss:.1f} 石と小さく、悪手というほどではありません。"

    # エンジンの読み（咎め手）: 実戦手に対して相手が実際にどう応じるか
    if opp_reply_actual is not None or opp_reply_best is not None:
        parts = []
        if opp_reply_actual is not None:
            parts.append(f"実戦 {act_str} の後、相手の最善の応手は {_describe_reply(ctx.board_actual, opp_reply_actual)}")
        if opp_reply_best is not None:
            parts.append(f"最善 {bst_str} なら相手の最善の応手は {_describe_reply(ctx.board_best, opp_reply_best)}")
        commentary += "\n\n■ エンジンの読み（咎め手）：\n" + "。".join(parts) + "。"

    if factor_lines:
        commentary += f"\n\n■ フェーズ別チェック（{phase['name']}の重みで比較）：\n" + "\n".join(factor_lines)

    return TacticalAnalysisResult(
        primary_pattern=primary,
        secondary_pattern=secondary,
        all_detected_patterns=detected,
        commentary=commentary,
        tags=tags[:4],
        phase_name=phase["name"],
        primary_factor=primary_factor,
        factors=factors,
    )


def generate_coaching_commentary(
    board_before: List[List[int]],
    actual_move: Optional[Tuple[int, int]],
    best_move: Optional[Tuple[int, int]],
    eval_loss: float,
    player: int = 1,
) -> str:
    """Egaroucid等の出力から外部LLMを一切使わずにプロレベルの悪手・好手解説を生成するマスター関数"""
    return analyze_tactical_patterns(board_before, actual_move, best_move, eval_loss, player).commentary
