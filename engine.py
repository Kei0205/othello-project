"""
engine.py
オセロAIエンジンラッパー（Egaroucid for Console 7.8.1 標準連携 ＆ 組み込みAlpha-Betaエンジン）
および終局後の全着手評価値算出・悪手（Blunder）自動抽出パイプライン
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import atexit
import math
import os
import queue
import subprocess
import threading

from othello import (
    BLACK,
    WHITE,
    EMPTY,
    CORNERS,
    X_SQUARES,
    C_SQUARES,
    MoveRecord,
    MoveFeatures,
    apply_move,
    coord_to_str,
    count_stones,
    extract_move_features,
    get_legal_moves,
    is_game_over,
    str_to_coord,
)
from tactics import analyze_tactical_patterns
from line_compare import explain_line_difference

DEFAULT_EGAROUCID_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "egaroucid_console",
    "Egaroucid_for_Console_7_8_1_Windows_SIMD",
    "Egaroucid_for_Console_7_8_1_SIMD.exe",
)

# Windows で subprocess 起動時に黒いコンソールウィンドウが一瞬表示されるのを防ぐ
_NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

_KILL_ON_CLOSE_JOB = None


def _bind_child_to_this_process(proc: subprocess.Popen) -> None:
    """
    Windows で、このPythonプロセスが終了したら常駐 Egaroucid も一緒に終了させる。
    Streamlit をウィンドウごと閉じる・強制終了すると atexit が走らず、親を失った Egaroucid が
    標準入力のEOFを読み続けてCPUを1コア100%使い続けるため、Job Object (KILL_ON_JOB_CLOSE) に入れておく。
    失敗しても対局自体には影響しないため、例外は握りつぶす。
    """
    global _KILL_ON_CLOSE_JOB
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class _IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimit),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        if _KILL_ON_CLOSE_JOB is None:
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return
            info = _ExtendedLimit()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            # 9 = JobObjectExtendedLimitInformation
            if not kernel32.SetInformationJobObject(
                wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                kernel32.CloseHandle(wintypes.HANDLE(job))
                return
            _KILL_ON_CLOSE_JOB = job  # ハンドルはプロセス終了まで保持する（閉じると子が終了する）
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        handle = kernel32.OpenProcess(0x0100 | 0x0001, False, proc.pid)
        if handle:
            kernel32.AssignProcessToJobObject(wintypes.HANDLE(_KILL_ON_CLOSE_JOB), wintypes.HANDLE(handle))
            kernel32.CloseHandle(wintypes.HANDLE(handle))
    except Exception:
        pass


def _parse_egaroucid_row(line: str) -> Optional[Tuple[Tuple[int, int], float]]:
    """
    Egaroucid の `hint` テーブル出力の1行をパースし、(着手座標, スコア) を返す。ヘッダー行や無関係な行は None。
    例: |           Book|              -|             e6|             +0|  000:00:00.000|              0|              0|
    """
    pos = line.find("|")
    if pos < 0:
        return None
    s = line[pos:].strip()
    if "Level|" in s or "Move|" in s:
        return None
    parts = [p.strip() for p in s.split("|")]
    if len(parts) < 5:
        return None
    coord = str_to_coord(parts[3].lower())
    if coord is None:
        return None
    try:
        score_val = float(parts[4].replace("+", ""))
    except ValueError:
        score_val = 0.0
    return coord, max(-64.0, min(64.0, score_val))


class _EgaroucidProcess:
    """
    Egaroucid for Console を常駐させて標準入出力で対話するプロセスラッパー。
    起動（評価関数・定石Bookの読み込み）に約1.4秒かかるため、毎手プロセスを立ち上げ直さずに再利用する。
    """

    def __init__(self, exe_path: str, level: int) -> None:
        self.exe_path = exe_path
        self.level = level
        self.lock = threading.Lock()
        self._lines: "queue.Queue[str]" = queue.Queue()
        self.proc = subprocess.Popen(
            [exe_path, "-l", str(level), "-q"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(exe_path)),
            creationflags=_NO_WINDOW_FLAGS,
        )
        _bind_child_to_this_process(self.proc)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def query(self, board_str: str, timeout: float) -> Optional[Tuple[Tuple[int, int], float]]:
        """1局面を問い合わせて (最善手, 手番視点スコア) を返す。タイムアウト・異常時は None。"""
        if not self.alive or self.proc.stdin is None:
            return None
        # 前回の残り出力を破棄
        while not self._lines.empty():
            try:
                self._lines.get_nowait()
            except queue.Empty:
                break
        try:
            self.proc.stdin.write(f"setboard {board_str}\nhint 1\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            return None
        while True:
            try:
                line = self._lines.get(timeout=timeout)
            except queue.Empty:
                return None
            row = _parse_egaroucid_row(line)
            if row is not None:
                return row

    def close(self) -> None:
        try:
            if self.alive and self.proc.stdin is not None:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=2)
        except Exception:
            pass
        if self.alive:
            self.proc.kill()


_PROCESS_POOL: Dict[Tuple[str, int], _EgaroucidProcess] = {}
_POOL_LOCK = threading.Lock()


def _get_egaroucid_process(exe_path: str, level: int) -> Optional[_EgaroucidProcess]:
    """(実行ファイル, レベル) ごとに常駐プロセスを1つ確保して返す"""
    key = (os.path.abspath(exe_path), level)
    with _POOL_LOCK:
        proc = _PROCESS_POOL.get(key)
        if proc is not None and proc.alive:
            return proc
        try:
            proc = _EgaroucidProcess(exe_path, level)
        except OSError:
            return None
        _PROCESS_POOL[key] = proc
        return proc


def _discard_egaroucid_process(proc: _EgaroucidProcess) -> None:
    with _POOL_LOCK:
        for k, v in list(_PROCESS_POOL.items()):
            if v is proc:
                del _PROCESS_POOL[k]
    proc.close()


@atexit.register
def _shutdown_egaroucid_processes() -> None:
    with _POOL_LOCK:
        procs = list(_PROCESS_POOL.values())
        _PROCESS_POOL.clear()
    for p in procs:
        p.close()

# 8x8 位置重みテーブル（組み込みエンジンフォールバック用）
POSITION_WEIGHTS: List[List[int]] = [
    [100, -25,  12,   6,   6,  12, -25, 100],
    [-25, -45,  -4,  -2,  -2,  -4, -45, -25],
    [ 12,  -4,   8,   2,   2,   8,  -4,  12],
    [  6,  -2,   2,   1,   1,   2,  -2,   6],
    [  6,  -2,   2,   1,   1,   2,  -2,   6],
    [ 12,  -4,   8,   2,   2,   8,  -4,  12],
    [-25, -45,  -4,  -2,  -2,  -4, -45, -25],
    [100, -25,  12,   6,   6,  12, -25, 100],
]


@dataclass
class EvalResult:
    """特定局面に対するエンジンの評価結果"""
    best_move: Optional[Tuple[int, int]]
    score_for_player: float   # 手番プレイヤー視点の予測最終石差 (-64.0 ～ +64.0)
    score_for_black: float    # 黒視点の予測最終石差 (-64.0 ～ +64.0)


@dataclass
class PlyAnalysis:
    """1手ごとの解析データ（形勢グラフおよび悪手抽出用）"""
    ply: int
    player: int
    move: Optional[Tuple[int, int]]
    move_str: str
    best_move: Optional[Tuple[int, int]]
    best_move_str: str
    eval_before_user: float       # 着手前のユーザー視点評価値（予測石差）
    eval_after_user: float        # 実際の着手後のユーザー視点評価値（予測石差）
    eval_before_black: float      # 着手前の黒視点評価値
    eval_after_black: float       # 実際の着手後の黒視点評価値
    eval_diff_user: float         # この1手によるユーザー視点の評価値変動 (after - before)
    is_user_turn: bool
    is_blunder: bool
    features: Optional[MoveFeatures]
    board_before: List[List[int]]
    board_after: List[List[int]]
    coach_comment: str = ""
    tactical_comment: str = ""
    tactical_tags: List[str] = field(default_factory=list)
    tactical_phase: str = ""      # 局面フェーズ（序盤/中盤前半/中盤後半/終盤）
    tactical_factor: str = ""     # 主な理由の評価因子（mobility/edge/stable/parity/discs、特定できなければ空）


class OthelloEngine:
    """
    オセロエンジンラッパークラス。
    - mode="cli": Egaroucid for Console 7.8.1 (または Edax) を subprocess 経由で呼び出し
    - mode="builtin": Python組み込みの戦術評価＋Alpha-Beta探索＋終盤完全読みエンジン
    - Lv.0: 🐣 超初級（ひよこ / 入門）- 初心者が楽しく勝てる接待AI（ミス多め・角譲り・最多石取りグリーディー）
    - Lv.1: 🌱 初級（ルーキー / 見習い）- 基本的な位置重視だが35%緩手・石取り重視
    - Lv.2: 🌿 初中級（アマ級位）- 浅い読み＋開放度ペナルティ
    - Lv.3: ⚔️ 中級（アマ初段）- AlphaBeta 2-3手読み / Egaroucid Lv.1
    - Lv.4+: 上級・達人（Egaroucid Lv.3〜15）- 本格SIMD探索
    """

    def __init__(
        self,
        mode: str = "cli",
        level: int = 5,
        cli_path: str = DEFAULT_EGAROUCID_PATH,
    ) -> None:
        self.mode = mode
        self.level = max(0, min(20, level))
        self.cli_path = cli_path.strip() or DEFAULT_EGAROUCID_PATH

    @property
    def is_cli_available(self) -> bool:
        return self.mode == "cli" and bool(self.cli_path) and os.path.isfile(self.cli_path)

    @property
    def name(self) -> str:
        if self.level == 0:
            return "🐣 超初級 AI (ひよこ)"
        if self.level == 1 and not self.is_cli_available:
            return "🌱 初級 AI (ルーキー)"
        if self.level == 1 and self.is_cli_available:
            return "🌱 初級 AI (Egaroucid Lv.1)"
        if self.level == 2 and not self.is_cli_available:
            return "🌿 初中級 AI (アマ級位)"
        if self.is_cli_available:
            return f"Egaroucid 7.8.1 SIMD (Lv.{self.level})"
        level_names = {0: "Lv.0 (入門)", 1: "Lv.1 (初級)", 2: "Lv.2 (中級)", 3: "Lv.3 (上級)", 4: "Lv.4 (強化)"}
        return f"Built-in AlphaBeta {level_names.get(self.level, f'Lv.{self.level}')}"

    def choose_move(self, board: List[List[int]], player: int) -> Optional[Tuple[int, int]]:
        """現在の盤面からAIの着手を選択する"""
        if self.level == 0:
            return self._choose_level_0_move(board, player)
        elif self.level == 1:
            return self._choose_level_1_move(board, player)
        elif self.level == 2 and not self.is_cli_available:
            return self._choose_level_2_move(board, player)

        res = self.evaluate_position(board, player)
        return res.best_move

    def _choose_level_0_move(self, board: List[List[int]], player: int) -> Optional[Tuple[int, int]]:
        """
        超初級（ひよこ・入門）：
        - オセロ初心者が気持ちよく勝てるレベル！
        - たまに隅をあえて取らなかったり、X打ち・C打ちを打ってくれたり、石をたくさん取ろうとする（グリーディーな手）。
        """
        import random
        legal_dict = get_legal_moves(board, player)
        if not legal_dict:
            return None
        legal_moves = list(legal_dict.keys())
        if len(legal_moves) == 1:
            return legal_moves[0]

        # 1. X打ち・C打ち（初心者がうっかり打ってしまう危険手）があれば30%の確率で打つ
        xc_moves = [mv for mv in legal_moves if mv in X_SQUARES or mv in C_SQUARES]
        if xc_moves and random.random() < 0.35:
            return random.choice(xc_moves)

        # 2. 初心者あるある「たくさんひっくり返せる手を打つ」（グリーディー）
        # たくさんひっくり返すと外側の石が増えて相手に有利になる
        if random.random() < 0.55:
            return max(legal_moves, key=lambda mv: len(legal_dict[mv]))

        # 3. 隅以外のマスをあえて選ぶ（角があっても70%の確率で見落とす演出）
        non_corner_moves = [mv for mv in legal_moves if mv not in CORNERS]
        if non_corner_moves and random.random() < 0.70:
            return random.choice(non_corner_moves)

        return random.choice(legal_moves)

    def _choose_level_1_move(self, board: List[List[int]], player: int) -> Optional[Tuple[int, int]]:
        """
        初級（ルーキー・見習い）：
        - 基本的な位置評価や石取りを意識するが、35%程度の確率で緩手・ミスをする。
        - ユーザーが中割りや基本定石を意識すればしっかりと勝てる難易度。
        """
        import random
        legal_dict = get_legal_moves(board, player)
        if not legal_dict:
            return None
        legal_moves = list(legal_dict.keys())
        if len(legal_moves) == 1:
            return legal_moves[0]

        # 35%の確率でランダムまたは最多石取り
        if random.random() < 0.35:
            if random.random() < 0.60:
                return max(legal_moves, key=lambda mv: len(legal_dict[mv]))
            return random.choice(legal_moves)

        # 残りは1手先の簡単な位置重み評価
        def simple_score(mv: Tuple[int, int]) -> float:
            r, c = mv
            flips = len(legal_dict[mv])
            pos_val = POSITION_WEIGHTS[r][c]
            return pos_val + flips * 0.5

        return max(legal_moves, key=simple_score)

    def _choose_level_2_move(self, board: List[List[int]], player: int) -> Optional[Tuple[int, int]]:
        """
        初中級（アマ級位）：
        - 1手先の探索＋開放度ペナルティ。15%の確率で緩手を指す。
        """
        import random
        legal_dict = get_legal_moves(board, player)
        if not legal_dict:
            return None
        legal_moves = list(legal_dict.keys())
        if len(legal_moves) == 1:
            return legal_moves[0]

        if random.random() < 0.15:
            return random.choice(legal_moves)

        def move_score(mv: Tuple[int, int]) -> float:
            r, c = mv
            b_next, flips = apply_move(board, r, c, player)
            opp_moves = len(get_legal_moves(b_next, -player))
            pos_val = POSITION_WEIGHTS[r][c]
            return pos_val * 1.5 - opp_moves * 3.0

        return max(legal_moves, key=move_score)

    def warm_up(self) -> None:
        """常駐 Egaroucid プロセスを先行起動しておき、初手から待ち時間なく応答できるようにする"""
        if self.is_cli_available and self.level >= 1:
            _get_egaroucid_process(self.cli_path, max(1, self.level))

    def evaluate_position(
        self,
        board: List[List[int]],
        player: int,
        depth_override: Optional[int] = None,
    ) -> EvalResult:
        """
        指定局面の最善手と、予測最終石差（-64〜+64）を返す。
        """
        if self.is_cli_available:
            cli_res = self._evaluate_via_cli(board, player, level_override=depth_override)
            if cli_res is not None:
                return cli_res

        return self._evaluate_builtin(board, player, depth_override=depth_override)

    def evaluate_positions_batch(
        self,
        queries: List[Tuple[List[List[int]], int]],
        level_override: Optional[int] = None,
    ) -> List[EvalResult]:
        """
        複数局面を一括で評価する。
        Egaroucid CLI 利用時は1回のプロセス起動で全局面を一括評価するため極めて高速（約1秒）。
        """
        if not queries:
            return []

        if self.is_cli_available:
            batch_res = self._evaluate_batch_via_cli(queries, level_override=level_override)
            if batch_res is not None and len(batch_res) == len(queries):
                return batch_res

        return [
            self._evaluate_builtin(b, p, depth_override=min(3, level_override or 2))
            for b, p in queries
        ]

    def _board_to_edax_str(self, board: List[List[int]], player: int) -> str:
        """Egaroucid / Edax 互換の65文字盤面文字列を生成"""
        chars = []
        for r in range(8):
            for c in range(8):
                if board[r][c] == BLACK:
                    chars.append("X")
                elif board[r][c] == WHITE:
                    chars.append("O")
                else:
                    chars.append("-")
        turn_char = "X" if player == BLACK else "O"
        return "".join(chars) + " " + turn_char

    def _parse_egaroucid_table_lines(self, stdout_text: str) -> List[Tuple[Optional[Tuple[int, int]], float]]:
        """
        Egaroucid for Console の `hint 1` テーブル出力行をパースし、[(move_coord, score), ...] を返す。
        例:
        |          Level|          Depth|           Move|          Score|           Time|          Nodes|            NPS|
        |           Book|              -|             e6|             +0|  000:00:00.000|              0|              0|
        |              5|         5@100%|             f6|            -36|  000:00:00.000|          12884|       12884000|
        """
        results: List[Tuple[Optional[Tuple[int, int]], float]] = []
        for line in stdout_text.splitlines():
            row = _parse_egaroucid_row(line)
            if row is not None:
                results.append(row)
        return results

    def _evaluate_via_cli(
        self,
        board: List[List[int]],
        player: int,
        level_override: Optional[int] = None,
    ) -> Optional[EvalResult]:
        res_list = self._evaluate_batch_via_cli([(board, player)], level_override=level_override)
        if res_list and len(res_list) == 1:
            return res_list[0]
        return None

    def _query_timeout(self, level: int, n_queries: int) -> float:
        """レベルと局面数に応じたタイムアウト秒数（高レベル・大量局面で途中打ち切りされないよう余裕を持たせる）"""
        per_query = 2.0 + level * 0.6
        return 10.0 + per_query * max(1, n_queries)

    def _evaluate_batch_via_cli(
        self,
        queries: List[Tuple[List[List[int]], int]],
        level_override: Optional[int] = None,
    ) -> Optional[List[EvalResult]]:
        """
        Egaroucid for Console で複数局面を評価する。
        まず常駐プロセス（起動コストなし）で問い合わせ、失敗した場合は単発プロセスへの一括送信にフォールバックする。
        """
        lv = level_override if level_override is not None else self.level
        final_results: List[Optional[EvalResult]] = [None] * len(queries)
        # (クエリ番号, 送信する盤面文字列, パス局面として相手手番で問い合わせたか)
        pending: List[Tuple[int, str, bool]] = []

        for idx, (board, player) in enumerate(queries):
            if get_legal_moves(board, player):
                pending.append((idx, self._board_to_edax_str(board, player), False))
            elif is_game_over(board):
                b, w, _ = count_stones(board)
                diff_b = float(b - w)
                diff_p = diff_b if player == BLACK else -diff_b
                final_results[idx] = EvalResult(best_move=None, score_for_player=diff_p, score_for_black=diff_b)
            else:
                # パス局面は相手手番として問い合わせ、スコアを反転する
                pending.append((idx, self._board_to_edax_str(board, -player), True))

        if pending:
            raw_rows = self._run_queries_persistent(lv, [p[1] for p in pending])
            if raw_rows is None:
                raw_rows = self._run_queries_oneshot(lv, [p[1] for p in pending])
            if raw_rows is None:
                return None

            for (q_idx, _, is_pass), (coord, score_val) in zip(pending, raw_rows):
                board, player = queries[q_idx]
                if is_pass:
                    score_p = -score_val
                    best: Optional[Tuple[int, int]] = None
                else:
                    legal = get_legal_moves(board, player)
                    best = coord if coord in legal else next(iter(legal.keys()))
                    score_p = score_val
                score_b = score_p if player == BLACK else -score_p
                final_results[q_idx] = EvalResult(best_move=best, score_for_player=score_p, score_for_black=score_b)

        if any(r is None for r in final_results):
            return None
        return [r for r in final_results if r is not None]

    def _run_queries_persistent(
        self, level: int, board_strs: List[str]
    ) -> Optional[List[Tuple[Tuple[int, int], float]]]:
        """常駐 Egaroucid プロセスに局面を順次問い合わせる（失敗時は None）"""
        proc = _get_egaroucid_process(self.cli_path, level)
        if proc is None:
            return None
        per_query_timeout = self._query_timeout(level, 1)
        rows: List[Tuple[Tuple[int, int], float]] = []
        with proc.lock:
            for bs in board_strs:
                row = proc.query(bs, timeout=per_query_timeout)
                if row is None:
                    _discard_egaroucid_process(proc)
                    return None
                rows.append(row)
        return rows

    def _run_queries_oneshot(
        self, level: int, board_strs: List[str]
    ) -> Optional[List[Tuple[Tuple[int, int], float]]]:
        """単発の Egaroucid プロセスに全局面を一括送信する（常駐プロセスが使えない場合のフォールバック）"""
        cmd_lines: List[str] = []
        for bs in board_strs:
            cmd_lines.append(f"setboard {bs}")
            cmd_lines.append("hint 1")
        cmd_lines.append("quit")
        try:
            proc = subprocess.run(
                [self.cli_path, "-l", str(level), "-q"],
                input="\n".join(cmd_lines) + "\n",
                text=True,
                capture_output=True,
                cwd=os.path.dirname(os.path.abspath(self.cli_path)),
                timeout=self._query_timeout(level, len(board_strs)),
                creationflags=_NO_WINDOW_FLAGS,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        parsed = self._parse_egaroucid_table_lines(proc.stdout)
        if len(parsed) != len(board_strs):
            return None
        return [(c, s) for c, s in parsed if c is not None]

    def _evaluate_builtin(
        self,
        board: List[List[int]],
        player: int,
        depth_override: Optional[int] = None,
    ) -> EvalResult:
        legal = get_legal_moves(board, player)
        _, _, empty_count = count_stones(board)

        if not legal:
            if is_game_over(board):
                b, w, _ = count_stones(board)
                diff_b = float(b - w)
                diff_p = diff_b if player == BLACK else -diff_b
                return EvalResult(best_move=None, score_for_player=diff_p, score_for_black=diff_b)
            else:
                opp_res = self._evaluate_builtin(board, -player, depth_override=depth_override)
                return EvalResult(
                    best_move=None,
                    score_for_player=-opp_res.score_for_player,
                    score_for_black=opp_res.score_for_black,
                )

        if depth_override is not None:
            depth = min(4, depth_override)
        else:
            depth = min(4, max(1, self.level))

        exact_endgame_threshold = 8 if depth >= 3 else 6
        if empty_count <= exact_endgame_threshold:
            search_depth = empty_count
            exact_mode = True
        else:
            search_depth = depth
            exact_mode = False

        ordered_moves = self._order_moves(board, player, list(legal.keys()))

        best_move = ordered_moves[0]
        best_score = -math.inf
        alpha = -math.inf
        beta = math.inf

        for mv in ordered_moves:
            next_board, _ = apply_move(board, mv[0], mv[1], player)
            score = -self._alphabeta(
                next_board,
                -player,
                search_depth - 1,
                -beta,
                -alpha,
                exact_mode=exact_mode,
            )
            if score > best_score:
                best_score = score
                best_move = mv
            if score > alpha:
                alpha = score

        best_score = round(max(-64.0, min(64.0, best_score)), 1)
        score_black = best_score if player == BLACK else -best_score
        return EvalResult(
            best_move=best_move,
            score_for_player=best_score,
            score_for_black=score_black,
        )

    def _order_moves(
        self,
        board: List[List[int]],
        player: int,
        moves: List[Tuple[int, int]],
    ) -> List[Tuple[int, int]]:
        def move_priority(mv: Tuple[int, int]) -> int:
            r, c = mv
            if mv in CORNERS:
                return 1000
            if mv in X_SQUARES and board[X_SQUARES[mv][0]][X_SQUARES[mv][1]] == EMPTY:
                return -500
            if mv in C_SQUARES and board[C_SQUARES[mv][0]][C_SQUARES[mv][1]] == EMPTY:
                return -250
            return POSITION_WEIGHTS[r][c]

        return sorted(moves, key=move_priority, reverse=True)

    def _alphabeta(
        self,
        board: List[List[int]],
        player: int,
        depth: int,
        alpha: float,
        beta: float,
        exact_mode: bool = False,
    ) -> float:
        legal = get_legal_moves(board, player)
        if not legal:
            opp_legal = get_legal_moves(board, -player)
            if not opp_legal:
                b, w, _ = count_stones(board)
                diff = float(b - w)
                return diff if player == BLACK else -diff
            return -self._alphabeta(board, -player, depth, -beta, -alpha, exact_mode=exact_mode)

        if depth <= 0:
            if exact_mode:
                b, w, _ = count_stones(board)
                diff = float(b - w)
                return diff if player == BLACK else -diff
            return self._static_stone_diff_eval(board, player)

        ordered = self._order_moves(board, player, list(legal.keys()))
        value = -math.inf
        for mv in ordered:
            next_board, _ = apply_move(board, mv[0], mv[1], player)
            score = -self._alphabeta(next_board, -player, depth - 1, -beta, -alpha, exact_mode=exact_mode)
            if score > value:
                value = score
            if value > alpha:
                alpha = value
            if alpha >= beta:
                break
        return value

    def _static_stone_diff_eval(self, board: List[List[int]], player: int) -> float:
        b_stones, w_stones, empty_cnt = count_stones(board)
        if empty_cnt == 0 or is_game_over(board):
            diff = float(b_stones - w_stones)
            return diff if player == BLACK else -diff

        opponent = -player
        pos_score = 0.0
        frontier_player = 0
        frontier_opp = 0

        for r in range(8):
            for c in range(8):
                v = board[r][c]
                if v == EMPTY:
                    continue
                w = POSITION_WEIGHTS[r][c]
                if (r, c) in X_SQUARES:
                    cr, cc = X_SQUARES[(r, c)]
                    if board[cr][cc] != EMPTY:
                        w = 8
                elif (r, c) in C_SQUARES:
                    cr, cc = C_SQUARES[(r, c)]
                    if board[cr][cc] != EMPTY:
                        w = 10

                if v == player:
                    pos_score += w
                else:
                    pos_score -= w

                is_frontier = False
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == EMPTY:
                            is_frontier = True
                            break
                    if is_frontier:
                        break
                if is_frontier:
                    if v == player:
                        frontier_player += 1
                    else:
                        frontier_opp += 1

        my_mob = len(get_legal_moves(board, player))
        opp_mob = len(get_legal_moves(board, opponent))
        mobility_score = (my_mob - opp_mob) * 2.2
        frontier_score = (frontier_opp - frontier_player) * 1.1

        raw = (pos_score * 0.22) + mobility_score + frontier_score
        if empty_cnt < 16:
            stone_diff = (b_stones - w_stones) if player == BLACK else (w_stones - b_stones)
            raw = raw * 0.65 + stone_diff * 0.55

        return max(-64.0, min(64.0, raw))


def analyze_game_history(
    history: List[MoveRecord],
    user_color: int,
    engine: OthelloEngine,
    blunder_threshold: float = -4.0,
) -> List[PlyAnalysis]:
    """
    終局後（または途中まで）の全棋譜を走査し、
    1. 各局面の評価値（最終石差予測）をエンジンで一括算出（Egaroucid バッチ評価対応）
    2. ユーザー側の着手で評価値が blunder_threshold（デフォルト -4.0 石）以下に悪化した手を悪手（Blunder）として抽出
    3. 悪手局面の盤面特徴量（開放度・相手モビリティ変化・中割り・C/X危険マス）を算出
    """
    valid_records = [rec for rec in history if rec.move is not None]
    if not valid_records:
        return []

    # 1. 着手前局面と着手後局面をバッチで一括評価（Egaroucidなら1プロセスで全手高速評価）
    queries: List[Tuple[List[List[int]], int]] = []
    for rec in valid_records:
        queries.append((rec.board_before, rec.player))
        queries.append((rec.board_after, -rec.player))

    # 解析は対戦レベルより深めに読むが、全局面（約120局面）を現実的な時間で処理できるよう上限を設ける
    analysis_level = min(max(6, engine.level), 12) if engine.is_cli_available else 2
    batch_evals = engine.evaluate_positions_batch(queries, level_override=analysis_level)

    analyses: List[PlyAnalysis] = []
    for idx, rec in enumerate(valid_records):
        assert rec.move is not None
        is_user_turn = (rec.player == user_color)

        before_eval = batch_evals[2 * idx]
        after_opp_eval = batch_evals[2 * idx + 1]

        best_move = before_eval.best_move or rec.move

        if best_move == rec.move:
            eval_after_black = before_eval.score_for_black
        else:
            eval_after_black = after_opp_eval.score_for_black

        if user_color == BLACK:
            eval_before_user = before_eval.score_for_black
            eval_after_user = eval_after_black
        else:
            eval_before_user = -before_eval.score_for_black
            eval_after_user = -eval_after_black

        eval_diff_user = round(eval_after_user - eval_before_user, 1)

        is_blunder = (
            is_user_turn
            and (rec.move != best_move)
            and (eval_diff_user <= blunder_threshold)
        )

        features: Optional[MoveFeatures] = None
        tactical_comment = ""
        tactical_tags: List[str] = []
        tactical_phase = ""
        tactical_factor = ""
        if is_user_turn:
            features = extract_move_features(
                board_before=rec.board_before,
                player=rec.player,
                actual_move=rec.move,
                best_move=best_move,
            )
            if is_blunder:
                # 相手の最善応手（咎め手）: 実戦手の後の局面評価で得た最善手をそのまま使う
                tactical_res, tactical_comment = build_bad_move_commentary(
                    engine,
                    rec.board_before,
                    rec.player,
                    rec.move,
                    best_move,
                    eval_actual_user=eval_after_user,
                    eval_best_user=eval_before_user,
                    opp_reply_actual=after_opp_eval.best_move,
                )
                tactical_tags = tactical_res.tags
                tactical_phase = tactical_res.phase_name
                tactical_factor = tactical_res.primary_factor

        analyses.append(
            PlyAnalysis(
                ply=rec.ply,
                player=rec.player,
                move=rec.move,
                move_str=rec.move_str,
                best_move=best_move,
                best_move_str=coord_to_str(*best_move),
                eval_before_user=round(eval_before_user, 1),
                eval_after_user=round(eval_after_user, 1),
                eval_before_black=round(before_eval.score_for_black, 1),
                eval_after_black=round(eval_after_black, 1),
                eval_diff_user=eval_diff_user,
                is_user_turn=is_user_turn,
                is_blunder=is_blunder,
                features=features,
                board_before=rec.board_before,
                board_after=rec.board_after,
                coach_comment=tactical_comment,
                tactical_comment=tactical_comment,
                tactical_tags=tactical_tags,
                tactical_phase=tactical_phase,
                tactical_factor=tactical_factor,
            )
        )

    return analyses


def build_bad_move_commentary(
    engine: "OthelloEngine",
    board_before: List[List[int]],
    player: int,
    actual_move: Tuple[int, int],
    best_move: Tuple[int, int],
    eval_actual_user: Optional[float] = None,
    eval_best_user: Optional[float] = None,
    opp_reply_actual: Optional[Tuple[int, int]] = None,
) -> Tuple[Any, str]:
    """
    悪手1件の解説を組み立てる（tactics のフェーズ別パターン解説 ＋ エンジンの咎め手 ＋ 読み筋比較）。
    評価値は着手側から見た値を渡す。戻り値は (TacticalAnalysisResult, 解説Markdown)。
    """
    eval_loss = 0.0
    if eval_actual_user is not None and eval_best_user is not None:
        eval_loss = max(0.0, eval_best_user - eval_actual_user)

    # 最善手を打った場合の相手の最善応手も1局面だけ追加で読む（咎め手との比較用）
    opp_reply_best = None
    try:
        b_best, _ = apply_move(board_before, best_move[0], best_move[1], player)
        if get_legal_moves(b_best, -player):
            opp_reply_best = engine.evaluate_position(b_best, -player).best_move
        if opp_reply_actual is None:
            b_act, _ = apply_move(board_before, actual_move[0], actual_move[1], player)
            if get_legal_moves(b_act, -player):
                opp_reply_actual = engine.evaluate_position(b_act, -player).best_move
    except Exception:
        pass

    res = analyze_tactical_patterns(
        board_before=board_before,
        actual_move=actual_move,
        best_move=best_move,
        eval_loss=eval_loss,
        player=player,
        opp_reply_actual=opp_reply_actual,
        opp_reply_best=opp_reply_best,
    )
    text = res.commentary
    # 形の差が小さく理由が特定できない手は、局面を問わず読み筋（中盤は8手先）で具体的な違いを示す
    force_line = not res.all_detected_patterns and not res.primary_factor
    line_info: Dict[str, Any] = {}
    line_text = explain_line_difference(
        engine,
        board_before,
        player,
        actual_move,
        best_move,
        eval_actual_user=eval_actual_user,
        eval_best_user=eval_best_user,
        force=force_line,
        info=line_info,
    )
    if force_line and line_info:
        text = _reason_from_line(res, text, line_info, actual_move, best_move)
    if line_text:
        text = text + "\n\n" + line_text
    return res, text


_GENERIC_REASON_MARK = "目に見える形の差はほとんどなく"


def _reason_from_line(res: Any, text: str, info: Dict[str, Any], actual_move: Tuple[int, int], best_move: Tuple[int, int]) -> str:
    """
    盤面の形（開放度・辺・確定石など）では差が見えない手について、読み筋の比較結果から
    「主な理由」と原因因子を決め直す（隅の取り合い → パス・手止まり → 安全手枯渇 → セクター支配 → 手数 の順に判定）。
    """
    from tactics import FACTOR_TAGS, FACTOR_LABELS

    act_s, bst_s = coord_to_str(*actual_move), coord_to_str(*best_move)
    ahead = "終局まで" if info.get("to_end") else f"{info.get('plies')}手先まで"
    phase = res.phase_name or "この局面"
    factor = ""
    custom_tag = ""
    reason = ""

    col_a, col_b = info.get("mobility_collapse", (None, None))
    sec_name, sec_gain = info.get("sector_diff", ("", 0))

    if info.get("corner_gain", 0) >= 1:
        factor = "stable"
        reason = (
            f"打った直後の形の差は小さいものの、{ahead}読むと隅の取り合いで差がつきます。"
            f"最善 {bst_s} の進行の方が隅の収支が良く、{phase}で重要な確定石の確保につながります（下の読み筋比較）。"
        )
    elif info.get("opp_pass_gain", 0) >= 1 or info.get("regions_won", 0) >= 1:
        factor = "parity"
        reason = (
            f"打った直後の形の差は小さいものの、{ahead}読むと、最善 {bst_s} の進行では相手をパスに追い込んだり、"
            f"空きエリアの最後の一手（手止まり）を自分が取れたりします（下の読み筋比較）。"
        )
    elif col_a is not None and (col_b is None or col_b > col_a):
        factor = "mobility"
        custom_tag = "危険手強要（手詰まり）"
        reason = (
            f"実戦 {act_s} を打つと、わずか {col_a} 手後に安全に打てる場所がなくなり（手詰まり）、"
            f"空き隅の隣（XマスやCマス）へ打たざるを得なくなります。最善 {bst_s} の進行ならば余裕手を維持して主導権を握り続けられます。"
        )
    elif sec_gain >= 2:
        if sec_name in ("上辺", "下辺", "左辺", "右辺"):
            factor = "edge"
            custom_tag = "辺の主導権・確定化ミス"
            reason = (
                f"打った直後の形の差は小さいものの、{ahead}読むと{sec_name}の主導権で差がつきます。"
                f"実戦の進行では{sec_name}を相手に制圧されますが、最善 {bst_s} の進行では{sec_name}で自分の石を確実に増やし（+{sec_gain}枚）、"
                f"辺の確定化へと繋げることができます。"
            )
        else:
            factor = "mobility"
            custom_tag = "中割り・中央制圧ミス"
            reason = (
                f"打った直後の形の差は小さいものの、{ahead}読むと盤面中央部の支配力で差がつきます。"
                f"最善 {bst_s} の進行では中央部に石の厚みを残しつつ（+{sec_gain}枚）、相手の手数を抑えて安全な中割りを継続できます。"
            )
    elif info.get("mobility"):
        my_a, op_a, my_b, op_b = info["mobility"]
        net_diff = (my_b - op_b) - (my_a - op_a)
        if net_diff >= 1:
            factor = "mobility"
            reason = (
                f"打った直後の形の差は小さいものの、{ahead}読むと手数に差がつきます。"
                f"相手が打てる場所は 実戦 {act_s} の進行で {op_a}箇所、最善 {bst_s} の進行で {op_b}箇所、"
                f"自分が打てる場所は 実戦 {my_a}箇所 ／ 最善 {my_b}箇所 です。"
                f"{phase}で大切な「相手の手数を減らし、自分の手数を残す」点で、最善手の方が数手先に得をします。"
            )
    if not factor:
        factor = "mobility"
        custom_tag = "中割り・方向選択ミス"
        reason = (
            f"目に見える局所的な差は僅かですが、{ahead}読むと最善 {bst_s} の進行の方が石の返し方向が良く、"
            f"相手に安全な足がかりを与えないため中長期的に優位を保てます。"
        )

    start = text.find("■ 主な理由：\n")
    end = text.find("\n\n", start)
    if start < 0 or _GENERIC_REASON_MARK not in text[start:end if end > 0 else None]:
        return text
    new_block = f"■ 主な理由（{FACTOR_LABELS[factor]}・数手先の読み）：\n{reason}"
    text = text[:start] + new_block + (text[end:] if end > 0 else "")
    res.primary_factor = factor
    tag_to_use = custom_tag if custom_tag else FACTOR_TAGS.get(factor, "戦術判断ミス")
    res.tags = [tag_to_use]
    res.commentary = text
    return text
