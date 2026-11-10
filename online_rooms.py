"""
online_rooms.py
同一Wi-Fi (LAN) / ネットワーク経由で2台の端末（PC・スマホなど）から
リアルタイムに対戦するためのスレッドセーフな部屋管理モジュール
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
import random
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from othello import (
    BLACK,
    WHITE,
    OthelloGame,
    color_name,
    coord_to_str,
    get_legal_moves,
    is_game_over,
)


def get_lan_ip() -> str:
    """同一Wi-Fi内でアクセス可能な自機のローカルIPアドレスを取得する"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 実際にパケットを飛ばさずにルートインターフェースのIPを取得
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_connection_url(port: int = 5000) -> str:
    """友達に共有するアクセスURL文字列を返す"""
    ip = get_lan_ip()
    return f"http://{ip}:{port}"


@dataclass
class OnlineRoom:
    room_id: str
    host_name: str
    guest_name: str = ""
    host_color: int = BLACK
    guest_color: int = WHITE
    color_mode: str = "manual"  # "manual" | "random"
    game: OthelloGame = field(default_factory=OthelloGame)
    status: str = "waiting"  # "waiting", "playing", "finished"
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    version: int = 1
    turn_notices: List[str] = field(default_factory=list)
    dilemmas: List[Dict[str, Any]] = field(default_factory=list)
    analysis_done: bool = False
    analyses_json: str = "[]"
    saved_db_id: Optional[int] = None
    pass_event_notice: Optional[str] = None


class OnlineRoomManager:
    """スレッドセーフなオンサイト対戦部屋マネージャー"""

    _instance: Optional["OnlineRoomManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "OnlineRoomManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._rooms = {}
                cls._instance._room_lock = threading.Lock()
            return cls._instance

    def create_room(
        self,
        room_id: str,
        host_name: str = "ホスト",
        host_color: int = BLACK,
        color_mode: str = "manual",
    ) -> OnlineRoom:
        """新しい対戦部屋を作成する（既存部屋があれば上書き初期化）"""
        clean_id = room_id.strip() or "777"
        with self._room_lock:
            guest_color = WHITE if host_color == BLACK else BLACK
            room = OnlineRoom(
                room_id=clean_id,
                host_name=host_name.strip() or "ホスト",
                guest_name="",
                host_color=host_color,
                guest_color=guest_color,
                color_mode=color_mode,
                game=OthelloGame(),
                status="waiting",
                created_at=time.time(),
                last_activity=time.time(),
                version=1,
            )
            self._rooms[clean_id] = room
            return room

    def join_room(self, room_id: str, guest_name: str = "ゲスト") -> Tuple[bool, str, Optional[OnlineRoom]]:
        """部屋に参加する"""
        clean_id = room_id.strip()
        with self._room_lock:
            room = self._rooms.get(clean_id)
            if not room:
                return False, f"合言葉『{clean_id}』の部屋が見つかりません。部屋番号をご確認ください。", None

            # ランダム手番の場合は参加時にコイントスで決定
            if room.color_mode == "random":
                room.host_color = random.choice([BLACK, WHITE])
                room.guest_color = WHITE if room.host_color == BLACK else BLACK
                h_c = "⚫ 黒（先手）" if room.host_color == BLACK else "⚪ 白（後手）"
                g_c = "⚪ 白（後手）" if room.host_color == BLACK else "⚫ 黒（先手）"
                g_clean = guest_name.strip() or "ゲスト"
                room.turn_notices = [
                    f"🎲 【コイントス結果】 {room.host_name} は {h_c}、{g_clean} は {g_c} に決定しました！"
                ]

            room.guest_name = guest_name.strip() or "ゲスト"
            room.status = "playing"
            room.last_activity = time.time()
            room.version += 1
            return True, "部屋に参加しました！対局を開始します。", room

    def get_room(self, room_id: str) -> Optional[OnlineRoom]:
        """部屋情報を取得する"""
        clean_id = room_id.strip()
        with self._room_lock:
            return self._rooms.get(clean_id)

    def list_rooms(self) -> List[Dict[str, Any]]:
        """待機中・対戦中のアクティブな部屋一覧を取得する"""
        with self._room_lock:
            now = time.time()
            summary = []
            for r_id, r in self._rooms.items():
                # 3時間以上経過した部屋は非表示
                if now - r.last_activity < 10800:
                    if r.color_mode == "random":
                        c_label = "🎲 ランダム"
                    else:
                        c_label = "⚫ 黒（先手）" if r.host_color == BLACK else "⚪ 白（後手）"
                    summary.append(
                        {
                            "room_id": r.room_id,
                            "host_name": r.host_name,
                            "guest_name": r.guest_name,
                            "status": r.status,
                            "color_mode": r.color_mode,
                            "color_label": c_label,
                            "created_at": datetime.fromtimestamp(r.created_at).strftime("%H:%M:%S"),
                            "ply": len(r.game.history),
                        }
                    )
            return sorted(summary, key=lambda x: x["created_at"], reverse=True)

    def apply_move(
        self,
        room_id: str,
        player_color: int,
        r: int,
        c: int,
        hesitated_moves: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """部屋の盤面に着手を反映する"""
        with self._room_lock:
            room = self._rooms.get(room_id)
            if not room:
                return False, "部屋が見つかりません"

            if room.game.game_over:
                return False, "この対局は既に終局しています"

            if room.game.current_turn != player_color:
                return False, "相手の手番です。着手をお待ちください"

            legal = get_legal_moves(room.game.board, room.game.current_turn)
            if (r, c) not in legal:
                return False, f"その位置（{coord_to_str(r, c)}）には打てません（合法手ではありません）"

            # 迷い手メモの蓄積
            if hesitated_moves:
                actual_str = coord_to_str(r, c)
                cands = list(hesitated_moves)
                if actual_str not in cands:
                    cands.insert(0, actual_str)
                alts = [m for m in cands if m != actual_str]
                if alts:
                    room.dilemmas.append(
                        {
                            "ply": len(room.game.history) + 1,
                            "player": player_color,
                            "board_before": [row[:] for row in room.game.board],
                            "actual_move": (r, c),
                            "actual_move_str": actual_str,
                            "hesitated_moves": alts,
                            "all_candidates": cands,
                        }
                    )

            # 着手実行
            ok = room.game.make_move(r, c)
            if not ok:
                return False, "着手に失敗しました"

            # パス通知の記録
            if room.game.pass_notices:
                room.pass_event_notice = room.game.pass_notices[-1]
            else:
                room.pass_event_notice = None

            if room.game.game_over:
                room.status = "finished"

            room.last_activity = time.time()
            room.version += 1
            return True, "着手しました"

    def restart_room(
        self,
        room_id: str,
        swap_colors: Optional[bool] = None,
        mode: str = "swap",
    ) -> Tuple[bool, str]:
        """再戦する（先後交代、ランダム、手番維持）"""
        with self._room_lock:
            room = self._rooms.get(room_id)
            if not room:
                return False, "部屋が見つかりません"

            if swap_colors is not None:
                mode = "swap" if swap_colors else "keep"

            if mode == "swap":
                room.host_color, room.guest_color = room.guest_color, room.host_color
                h_c = "⚫ 黒（先手）" if room.host_color == BLACK else "⚪ 白（後手）"
                g_c = "⚪ 白（後手）" if room.host_color == BLACK else "⚫ 黒（先手）"
                room.turn_notices = [f"🔄 手番を交代して再戦を開始しました！（{room.host_name}: {h_c} / {room.guest_name}: {g_c}）"]
            elif mode == "random":
                room.host_color = random.choice([BLACK, WHITE])
                room.guest_color = WHITE if room.host_color == BLACK else BLACK
                h_c = "⚫ 黒（先手）" if room.host_color == BLACK else "⚪ 白（後手）"
                g_c = "⚪ 白（後手）" if room.host_color == BLACK else "⚫ 黒（先手）"
                room.turn_notices = [f"🎲 【コイントス結果】 {room.host_name} は {h_c}、{room.guest_name} は {g_c} に決定しました！"]
            else:
                room.turn_notices = ["▶️ 手番を維持して再戦を開始しました！"]

            room.game = OthelloGame()
            room.status = "playing" if room.guest_name else "waiting"
            room.version += 1
            room.last_activity = time.time()
            room.dilemmas = []
            room.analysis_done = False
            room.saved_db_id = None
            room.pass_event_notice = None
            return True, "新しい盤面で再戦を開始しました"

    def claim_analysis(self, room_id: str) -> bool:
        """終局後解析を担当する権利を1度だけ確保する（両プレイヤーの画面から同時に保存されるのを防ぐ）"""
        with self._room_lock:
            room = self._rooms.get(room_id)
            if not room or room.analysis_done:
                return False
            room.analysis_done = True
            return True

    def release_analysis(self, room_id: str) -> None:
        """解析が失敗したときに解析権を返し、次の更新で再試行できるようにする"""
        with self._room_lock:
            room = self._rooms.get(room_id)
            if room:
                room.analysis_done = False

    def resign_room(self, room_id: str, player_color: int) -> Tuple[bool, str]:
        """対局を途中終了（投了・解析へ移行）する"""
        with self._room_lock:
            room = self._rooms.get(room_id)
            if not room:
                return False, "部屋が見つかりません"
            room.game.game_over = True
            room.status = "finished"
            room.version += 1
            room.last_activity = time.time()
            return True, "対局を終了しました"


# シングルトンインスタンス
room_manager = OnlineRoomManager()
