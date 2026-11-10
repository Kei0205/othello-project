"""
othello.py
オセロ（リバーシ）の盤面表現・ルール判定・および盤面特徴量（開放度・モビリティ・中割り・危険マス）算出モジュール
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

EMPTY = 0
BLACK = 1   # 黒 (先手)
WHITE = -1  # 白 (後手)

DIRECTIONS: List[Tuple[int, int]] = [
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
]

COL_NAMES = "abcdefgh"
ROW_NAMES = "12345678"

# 隅の座標と、それぞれに対応するXマス・Cマスの定義
CORNERS: List[Tuple[int, int]] = [(0, 0), (0, 7), (7, 0), (7, 7)]

# X打ち（隅の斜め隣）: キーがXマス座標、値が隣接する隅の座標
X_SQUARES: Dict[Tuple[int, int], Tuple[int, int]] = {
    (1, 1): (0, 0),
    (1, 6): (0, 7),
    (6, 1): (7, 0),
    (6, 6): (7, 7),
}

# C打ち（隅の縦横隣）: キーがCマス座標、値が隣接する隅の座標
C_SQUARES: Dict[Tuple[int, int], Tuple[int, int]] = {
    (0, 1): (0, 0), (1, 0): (0, 0),
    (0, 6): (0, 7), (1, 7): (0, 7),
    (6, 0): (7, 0), (7, 1): (7, 0),
    (6, 7): (7, 7), (7, 6): (7, 7),
}


def coord_to_str(row: int, col: int) -> str:
    """(row, col) を 'f5' などのオセロ座標文字列に変換する。"""
    if 0 <= row < 8 and 0 <= col < 8:
        return f"{COL_NAMES[col]}{ROW_NAMES[row]}"
    return "pass"


def str_to_coord(move_str: str) -> Optional[Tuple[int, int]]:
    """'f5' などの文字列を (row, col) に変換する。不正な文字列なら None。"""
    s = move_str.strip().lower()
    if len(s) != 2 or s[0] not in COL_NAMES or s[1] not in ROW_NAMES:
        return None
    return (ROW_NAMES.index(s[1]), COL_NAMES.index(s[0]))


def color_name(color: int) -> str:
    if color == BLACK:
        return "黒 (●)"
    elif color == WHITE:
        return "白 (○)"
    return "空"


@dataclass
class MoveRecord:
    """1手の履歴データ"""
    ply: int                              # 手数 (1始まり)
    player: int                           # 着手したプレイヤー (BLACK or WHITE)
    move: Optional[Tuple[int, int]]       # (row, col) または None (パス)
    flipped: List[Tuple[int, int]]        # 裏返した石の座標リスト
    board_before: List[List[int]]         # 着手前の盤面 (8x8)
    board_after: List[List[int]]          # 着手後の盤面 (8x8)

    @property
    def move_str(self) -> str:
        if self.move is None:
            return "pass"
        return coord_to_str(self.move[0], self.move[1])


@dataclass
class MoveFeatures:
    """悪手・着手局面の戦術特徴量"""
    move_str: str
    best_move_str: str
    openness_total: int                   # 開放度（返した石それぞれの周囲8近傍の空きマス数合計）
    openness_unique: int                  # 返した石の周囲にある重複を除いた空きマス数
    flipped_count: int                    # 返した石の数
    opp_mobility_before: int              # 着手前の相手側着手可能数（参考値）
    opp_mobility_after: int               # 着手直後の相手の着手可能マス数
    opp_mobility_best: int                # 最善手を打っていた場合の相手の着手可能マス数
    opp_mobility_delta: int               # 最善手と比較した相手モビリティの増減 (after - best)
    is_nakawari: bool                     # 中割り判定（返した石の周囲に空きマスがほぼ無い手か）
    best_is_nakawari: bool                # 最善手が中割りだったか
    is_x_move: bool                       # 危険なX打ちか（隣接する隅が空きの状態でのXマス着手）
    is_c_move: bool                       # 危険なC打ちか（隣接する隅が空きの状態でのCマス着手）
    danger_detail: str                    # 危険マスや形状に関する説明テキスト

    def to_dict(self) -> dict:
        return {
            "move_str": self.move_str,
            "best_move_str": self.best_move_str,
            "openness_total": self.openness_total,
            "openness_unique": self.openness_unique,
            "flipped_count": self.flipped_count,
            "opp_mobility_before": self.opp_mobility_before,
            "opp_mobility_after": self.opp_mobility_after,
            "opp_mobility_best": self.opp_mobility_best,
            "opp_mobility_delta": self.opp_mobility_delta,
            "is_nakawari": self.is_nakawari,
            "best_is_nakawari": self.best_is_nakawari,
            "is_x_move": self.is_x_move,
            "is_c_move": self.is_c_move,
            "danger_detail": self.danger_detail,
        }


def create_initial_board() -> List[List[int]]:
    """初期盤面を作成して返す"""
    board = [[EMPTY for _ in range(8)] for _ in range(8)]
    board[3][3] = WHITE
    board[3][4] = BLACK
    board[4][3] = BLACK
    board[4][4] = WHITE
    return board


def copy_board(board: List[List[int]]) -> List[List[int]]:
    return [row[:] for row in board]


def get_flips(board: List[List[int]], row: int, col: int, player: int) -> List[Tuple[int, int]]:
    """指定マスに player が打った際に裏返る石の座標リストを返す。非合法手なら空リスト。"""
    if not (0 <= row < 8 and 0 <= col < 8) or board[row][col] != EMPTY:
        return []

    opponent = -player
    flipped: List[Tuple[int, int]] = []

    for dr, dc in DIRECTIONS:
        r, c = row + dr, col + dc
        line: List[Tuple[int, int]] = []
        while 0 <= r < 8 and 0 <= c < 8 and board[r][c] == opponent:
            line.append((r, c))
            r += dr
            c += dc
        if line and 0 <= r < 8 and 0 <= c < 8 and board[r][c] == player:
            flipped.extend(line)

    return flipped


def get_legal_moves(board: List[List[int]], player: int) -> Dict[Tuple[int, int], List[Tuple[int, int]]]:
    """player の合法手辞書 {(row, col): [裏返る座標リスト]} を返す。"""
    moves: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for r in range(8):
        for c in range(8):
            if board[r][c] == EMPTY:
                flips = get_flips(board, r, c, player)
                if flips:
                    moves[(r, c)] = flips
    return moves


def apply_move(board: List[List[int]], row: int, col: int, player: int) -> Tuple[List[List[int]], List[Tuple[int, int]]]:
    """盤面に手を適用した新しい盤面と裏返した石リストを返す。"""
    flips = get_flips(board, row, col, player)
    if not flips:
        raise ValueError(f"Illegal move ({row}, {col}) for player {player}")
    new_board = copy_board(board)
    new_board[row][col] = player
    for fr, fc in flips:
        new_board[fr][fc] = player
    return new_board, flips


def count_stones(board: List[List[int]]) -> Tuple[int, int, int]:
    """(黒石数, 白石数, 空きマス数) を返す。"""
    b = 0
    w = 0
    e = 0
    for r in range(8):
        for c in range(8):
            val = board[r][c]
            if val == BLACK:
                b += 1
            elif val == WHITE:
                w += 1
            else:
                e += 1
    return b, w, e


def is_game_over(board: List[List[int]]) -> bool:
    """黒・白ともに合法手がない場合に終局と判定する。"""
    if get_legal_moves(board, BLACK):
        return False
    if get_legal_moves(board, WHITE):
        return False
    return True


def calculate_openness(board_after: List[List[int]], flipped: List[Tuple[int, int]]) -> Tuple[int, int]:
    """
    開放度を計算する。
    - openness_total: 裏返した各石の周囲8方向にある空きマス数の総和（標準的な開放度理論の定義）
    - openness_unique: 裏返した石群に隣接する重複なしの空きマス数
    """
    total_openness = 0
    unique_empty = set()
    for fr, fc in flipped:
        for dr, dc in DIRECTIONS:
            nr, nc = fr + dr, fc + dc
            if 0 <= nr < 8 and 0 <= nc < 8 and board_after[nr][nc] == EMPTY:
                total_openness += 1
                unique_empty.add((nr, nc))
    return total_openness, len(unique_empty)


def extract_move_features(
    board_before: List[List[int]],
    player: int,
    actual_move: Tuple[int, int],
    best_move: Optional[Tuple[int, int]] = None,
) -> MoveFeatures:
    """
    指定局面でのユーザー着手 (actual_move) と最善手 (best_move) から、
    オセロ戦術特徴量（開放度・モビリティ変化・中割り・危険マス判定）を算出する。
    """
    opponent = -player
    opp_mob_before = len(get_legal_moves(board_before, opponent))

    board_after, flipped = apply_move(board_before, actual_move[0], actual_move[1], player)
    openness_total, openness_unique = calculate_openness(board_after, flipped)
    opp_mob_after = len(get_legal_moves(board_after, opponent))

    # 中割り判定: 裏返した石の周囲の空きマスが0（完全中割り）または1以下
    is_nakawari = (openness_total <= 1)

    if best_move is not None and best_move != actual_move:
        board_best, best_flipped = apply_move(board_before, best_move[0], best_move[1], player)
        best_open_total, _ = calculate_openness(board_best, best_flipped)
        opp_mob_best = len(get_legal_moves(board_best, opponent))
        best_is_nakawari = (best_open_total <= 1)
    else:
        best_move = actual_move
        opp_mob_best = opp_mob_after
        best_is_nakawari = is_nakawari

    # 危険マス（X打ち・C打ち）判定: 対応する隅がまだ空いている場合のみリスクとして検出
    is_x_move = False
    is_c_move = False
    danger_notes: List[str] = []

    if actual_move in X_SQUARES:
        corner_r, corner_c = X_SQUARES[actual_move]
        if board_before[corner_r][corner_c] == EMPTY:
            is_x_move = True
            corner_str = coord_to_str(corner_r, corner_c)
            danger_notes.append(f"空いている隅({corner_str})の斜め隣への【X打ち】です。隅を奪われる直接的な隙になりやすい危険手です。")

    if actual_move in C_SQUARES:
        corner_r, corner_c = C_SQUARES[actual_move]
        if board_before[corner_r][corner_c] == EMPTY:
            is_c_move = True
            corner_str = coord_to_str(corner_r, corner_c)
            danger_notes.append(f"空いている隅({corner_str})の辺隣への【C打ち】です。辺の付け手や隅への侵入を許すリスクがあります。")

    # X打ちやC打ちなどの危険マスは、オセロ理論上「中割り」とはみなさない
    if is_x_move or is_c_move:
        is_nakawari = False

    # 最善手も空いている隅の隣なら、「危険マスだから悪い」は優劣の理由にならないことを明記する
    best_corner = X_SQUARES.get(best_move) or C_SQUARES.get(best_move)
    if best_move != actual_move and best_corner is not None and board_before[best_corner[0]][best_corner[1]] == EMPTY:
        best_is_nakawari = False
        if danger_notes:
            danger_notes.append(
                f"ただし最善手の {coord_to_str(*best_move)} も空いている隅({coord_to_str(*best_corner)})の隣への着手です。"
                "差は危険マスかどうかではなく、その後の打ち順（手止まり・パス）で生まれています（解説の読み筋比較を参照）。"
            )

    if not danger_notes:
        if actual_move in CORNERS:
            danger_notes.append("確定石となる【隅(コーナー)】への着手です。")
        elif is_nakawari:
            danger_notes.append("外側の壁を破らない【中割り】の手筋です。")
        elif openness_total >= 5:
            danger_notes.append(f"開放度が {openness_total} と高く、外側の石を多く返して相手の手数を増やしています。")
        else:
            danger_notes.append("標準的な位置への着手です。")

    return MoveFeatures(
        move_str=coord_to_str(*actual_move),
        best_move_str=coord_to_str(*best_move),
        openness_total=openness_total,
        openness_unique=openness_unique,
        flipped_count=len(flipped),
        opp_mobility_before=opp_mob_before,
        opp_mobility_after=opp_mob_after,
        opp_mobility_best=opp_mob_best,
        opp_mobility_delta=opp_mob_after - opp_mob_best,
        is_nakawari=is_nakawari,
        best_is_nakawari=best_is_nakawari,
        is_x_move=is_x_move,
        is_c_move=is_c_move,
        danger_detail=" ".join(danger_notes),
    )


class OthelloGame:
    """対局状態・棋譜・自動パス処理・終局判定を管理するクラス"""

    def __init__(self) -> None:
        self.board: List[List[int]] = create_initial_board()
        self.current_turn: int = BLACK
        self.history: List[MoveRecord] = []
        self.pass_notices: List[str] = []
        self.game_over: bool = False

    def make_move(self, row: int, col: int) -> bool:
        """
        現在手番のプレイヤーが (row, col) に着手する。
        着手後、相手に合法手がなければ自動パス処理を行い、双方打てなければ終局とする。
        """
        if self.game_over:
            return False

        legal = get_legal_moves(self.board, self.current_turn)
        if (row, col) not in legal:
            return False

        board_before = copy_board(self.board)
        board_after, flips = apply_move(self.board, row, col, self.current_turn)
        self.board = board_after

        record = MoveRecord(
            ply=len(self.history) + 1,
            player=self.current_turn,
            move=(row, col),
            flipped=flips,
            board_before=board_before,
            board_after=copy_board(board_after),
        )
        self.history.append(record)
        self.pass_notices.clear()

        # 次手番の判定と自動パス判定
        next_turn = -self.current_turn
        if get_legal_moves(self.board, next_turn):
            self.current_turn = next_turn
        else:
            # 相手が打てない -> 自分が打てるか確認
            if get_legal_moves(self.board, self.current_turn):
                self.pass_notices.append(f"{color_name(next_turn)} に合法手がないためパスとなりました。{color_name(self.current_turn)} の連続手番です。")
            else:
                self.game_over = True

        return True

    def get_kifu_string(self) -> str:
        """パスを除いた連結棋譜文字列（例: 'f5d6c3...'）を返す"""
        return "".join(rec.move_str for rec in self.history if rec.move is not None)
