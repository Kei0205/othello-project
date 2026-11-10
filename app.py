"""
app.py
オセロ対戦＆AIコーチングWebアプリ (Streamlit)
- 8x8 クリック対戦盤面（黒/白選択・合法手判定・自動パス・終局判定）
- エンジンラッパー（組み込みAlpha-Betaエンジン ＆ Egaroucid/Edax CLI指定対応）
- 終局後の全手評価値（最終石差）算出・Plotly形勢推移グラフ・悪手（Blunder）自動抽出
- 盤面特徴量（開放度・相手モビリティ変化・中割り・C/X危険マス）算出と Gemini (gemini-2.5-flash) 戦術解説
- SQLite3 による対局履歴保存・閲覧タブ
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import hmac
import json
import os
import re
import secrets
import time

import plotly.graph_objects as go
import streamlit as st

from othello import (
    BLACK,
    WHITE,
    EMPTY,
    COL_NAMES,
    ROW_NAMES,
    OthelloGame,
    apply_move,
    color_name,
    coord_to_str,
    count_stones,
    create_initial_board,
    extract_move_features,
    get_legal_moves,
    is_game_over,
    str_to_coord,
)
from engine import (
    DEFAULT_EGAROUCID_PATH,
    OthelloEngine,
    PlyAnalysis,
    analyze_game_history,
)
from coach import (
    DilemmaAnalysis,
    analyze_all_dilemmas,
    export_latest_game_review_md,
    export_player_growth_md,
    generate_blunder_explanation,
    generate_overall_coaching_summary,
    generate_sandbox_position_coaching,
)
from db import (
    delete_match_record,
    init_db,
    list_match_records,
    save_match_record,
    update_match_review,
)
from tactics import (
    FACTOR_LABELS,
    analyze_tactical_patterns,
    get_phase,
    TacticalAnalysisResult,
)
from rating import (
    compute_sequential_ratings,
    diagnose_game_performance,
    estimate_opponent_rating,
    get_rank_title,
)
import urllib.parse
import bg_jobs
from nav import MAIN_TABS, REVIEW_TAB, apply_tab_jump
try:
    from quests_ui import (
        apply_quest_jump,
        render_cheatsheet,
        render_focus_card,
        render_next_steps,
        render_quests_tab,
        render_weakness_summary,
    )
except ImportError:
    def apply_quest_jump() -> None:
        pass

    def render_cheatsheet() -> None:
        pass

    def render_focus_card() -> None:
        pass

    def render_next_steps(rec: Any) -> None:
        pass

    def render_quests_tab() -> None:
        st.info("📚 学習クエスト機能は外部データ連携モジュールのため含まれていません。対局・形勢解析・AIコーチ機能をご利用いただけます。")

    def render_weakness_summary(records: Any) -> None:
        pass
from online_rooms import (
    OnlineRoom,
    get_connection_url,
    get_lan_ip,
    room_manager,
)


st.set_page_config(
    page_title="Othello AI Coach | オセロ対戦＆AIコーチング",
    page_icon="⚫",
    layout="wide",
)

# カスタムCSS: 参考画像2のパール・ラベンダー・スカイブルー・ピンクの光彩パステル背景 ＆ グラスモーフィズムボタン
st.markdown(
    """
    <style>
    /* 全体の背景: ラベンダー・ペールブルー・ベビーピンク・パールホワイトの多層パステルグラデーション＋光彩ライン */
    .stApp {
        background-color: #f3f5fc !important;
        background-image:
            url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1200 900' preserveAspectRatio='none'%3E%3Cdefs%3E%3ClinearGradient id='g1' x1='0%25' y1='100%25' x2='100%25' y2='0%25'%3E%3Cstop offset='0%25' stop-color='%23ffffff' stop-opacity='0.85'/%3E%3Cstop offset='50%25' stop-color='%23f9a8d4' stop-opacity='0.45'/%3E%3Cstop offset='100%25' stop-color='%2393c5fd' stop-opacity='0.7'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cpath d='M520,900 C780,680 960,420 820,60' fill='none' stroke='url(%23g1)' stroke-width='4' opacity='0.85'/%3E%3Cpath d='M480,900 C820,640 1020,380 860,20' fill='none' stroke='%23ffffff' stroke-width='2.5' opacity='0.9'/%3E%3Cpath d='M360,900 C310,650 220,460 110,280' fill='none' stroke='%23ffffff' stroke-width='2' stroke-dasharray='4 6' opacity='0.75'/%3E%3Cpath d='M550,900 C760,760 980,690 1200,710' fill='none' stroke='%23ffffff' stroke-width='3' opacity='0.85'/%3E%3Ccircle cx='530' cy='760' r='5' fill='%23ffffff' opacity='0.9'/%3E%3Ccircle cx='960' cy='680' r='6' fill='%23ffffff' opacity='0.85'/%3E%3C/svg%3E"),
            radial-gradient(circle at 82% 28%, rgba(196, 228, 255, 0.85) 0%, rgba(196, 228, 255, 0) 48%),
            radial-gradient(circle at 28% 85%, rgba(252, 205, 232, 0.78) 0%, rgba(252, 205, 232, 0) 45%),
            radial-gradient(circle at 12% 92%, rgba(253, 234, 195, 0.72) 0%, rgba(253, 234, 195, 0) 35%),
            radial-gradient(circle at 48% 42%, rgba(216, 206, 248, 0.75) 0%, rgba(216, 206, 248, 0) 55%),
            linear-gradient(135deg, #e8ecf8 0%, #eef2fb 35%, #e3eef9 70%, #f5e8f4 100%) !important;
        background-size: cover !important;
        background-attachment: fixed !important;
        color: #2b3048 !important;
    }
    header[data-testid="stHeader"] {
        background: rgba(248, 250, 255, 0.55) !important;
        backdrop-filter: blur(10px);
    }
    /* サイドバーも透明感のあるパール・ラベンダーブルー調グラスモーフィズム */
    section[data-testid="stSidebar"] {
        background: linear-gradient(
            165deg,
            rgba(244, 246, 254, 0.88) 0%,
            rgba(235, 238, 252, 0.86) 50%,
            rgba(248, 236, 248, 0.88) 100%
        ) !important;
        backdrop-filter: blur(14px) !important;
        border-right: 1px solid rgba(255, 255, 255, 0.85) !important;
        box-shadow: 2px 0 18px rgba(148, 163, 218, 0.12) !important;
    }
    /* 見出し・本文テキスト */
    h1, h2, h3, h4, h5, h6, p, label, div[data-testid="stMarkdownContainer"] {
        color: #2b3048;
    }
    /* メトリクスカードのグラスモーフィズム装飾（文字切れ防止） */
    div[data-testid="stMetric"] {
        background: rgba(255, 255, 255, 0.76) !important;
        backdrop-filter: blur(12px) !important;
        border: 1px solid rgba(255, 255, 255, 0.92) !important;
        border-radius: 14px !important;
        padding: 12px 14px !important;
        box-shadow: 0 4px 16px rgba(139, 148, 214, 0.10) !important;
    }
    div[data-testid="stMetricValue"] > div {
        font-size: 1.35rem !important;
        font-weight: 800 !important;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
    }
    /* プライマリボタン: パステル・ラベンダー〜スカイブルー〜ローズの柔らかな光沢グラデーション */
    button[kind="primary"] {
        background: linear-gradient(135deg, #7f9cf5 0%, #9b87f5 52%, #e08ec4 100%) !important;
        color: #ffffff !important;
        border: 1px solid rgba(255, 255, 255, 0.65) !important;
        border-radius: 12px !important;
        font-weight: 700 !important;
        letter-spacing: 0.02em !important;
        padding: 0.55rem 1rem !important;
        box-shadow: 0 4px 15px rgba(139, 135, 245, 0.32), inset 0 1px 1px rgba(255, 255, 255, 0.45) !important;
        transition: all 0.18s ease !important;
    }
    button[kind="primary"] *, button[kind="primary"] p, button[kind="primary"] span {
        color: #ffffff !important;
    }
    button[kind="primary"]:hover:not(:disabled) {
        background: linear-gradient(135deg, #6e8ef2 0%, #8e76f2 52%, #d87ab8 100%) !important;
        box-shadow: 0 6px 20px rgba(139, 135, 245, 0.45), inset 0 1px 2px rgba(255, 255, 255, 0.6) !important;
        transform: translateY(-1px);
    }
    /* セカンダリボタン: パールホワイトのフロストガラス調ボタン */
    button[kind="secondary"] {
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.88) 0%, rgba(243, 245, 255, 0.82) 100%) !important;
        color: #4c5175 !important;
        border: 1px solid rgba(186, 194, 242, 0.75) !important;
        border-radius: 12px !important;
        font-weight: 600 !important;
        padding: 0.52rem 1rem !important;
        backdrop-filter: blur(8px) !important;
        box-shadow: 0 2px 10px rgba(148, 163, 218, 0.14), inset 0 1px 1px rgba(255, 255, 255, 0.9) !important;
        transition: all 0.18s ease !important;
    }
    button[kind="secondary"] *, button[kind="secondary"] p, button[kind="secondary"] span {
        color: #4c5175 !important;
    }
    button[kind="secondary"]:hover:not(:disabled) {
        background: linear-gradient(135deg, #ffffff 0%, #eef2ff 60%, #fce7f3 100%) !important;
        border-color: #a5b4fc !important;
        color: #3730a3 !important;
        box-shadow: 0 4px 14px rgba(148, 163, 218, 0.26) !important;
        transform: translateY(-1px);
    }

    /* 着手ごとの画面全体の薄暗化（stale dimming）・ちらつき・右上ステータス表示を完全無効化 */
    [data-stale="true"], [data-stale="true"] * {
        opacity: 1 !important;
        filter: none !important;
        transition: none !important;
    }
    div[data-testid="stStatusWidget"] {
        display: none !important;
    }
    .stMainBlockContainer, [data-testid="stVerticalBlock"], [data-testid="stHorizontalBlock"] {
        transition: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Streamlit v2 双方向カスタムコンポーネント: SVG DOMを破棄せず永続保持し、画面更新なしで着手＆石返しアニメーションを描画
OTHELLO_BOARD_COMPONENT = st.components.v2.component(
    "interactive_othello_board",
    html='<div id="board-root" class="othello-board-wrap"></div>',
    js="""
    export default function(component) {
        const { data, setTriggerValue, parentElement } = component;
        let root = parentElement.querySelector('#board-root');
        if (!root) {
            root = document.createElement('div');
            root.id = 'board-root';
            root.className = 'othello-board-wrap';
            parentElement.appendChild(root);
        }

        if (!data) return;
        let payload = null;
        try {
            payload = typeof data === 'string' ? JSON.parse(data) : data;
        } catch (e) {
            root.innerHTML = data;
            return;
        }

        const SVG_NS = "http://www.w3.org/2000/svg";
        const cellSize = payload.cell_size || 55;
        const marginTop = 28;
        const marginLeft = 28;
        const marginRight = 10;
        const marginBottom = 10;
        const gridSize = cellSize * 8;
        const totalW = marginLeft + gridSize + marginRight;
        const totalH = marginTop + gridSize + marginBottom;
        const stoneR = +(cellSize * 0.415).toFixed(1);
        const plusLen = +(cellSize * 0.14).toFixed(1);
        const dotR = +(cellSize * 0.13).toFixed(1);
        const starR = Math.max(3.0, +(cellSize * 0.068).toFixed(1));
        const colNames = "abcdefgh";
        const rowNames = "12345678";

        // 初回のみ SVG 盤面 DOM を構築し、以降は同じ DOM 要素を永続的に再利用して画面のちらつきをゼロにする
        let refs = parentElement._boardRefs;
        if (!refs || refs.cellSize !== cellSize || !root.contains(refs.container)) {
            root.innerHTML = "";
            const container = document.createElement("div");
            container.style.display = "flex";
            container.style.flexDirection = "column";
            container.style.alignItems = "center";
            container.style.gap = "8px";
            container.style.width = "100%";
            container.style.maxWidth = `${totalW}px`;

            const captionBox = document.createElement("div");
            captionBox.className = "anim-status-pill";
            container.appendChild(captionBox);

            const svg = document.createElementNS(SVG_NS, "svg");
            svg.setAttribute("width", totalW);
            svg.setAttribute("height", totalH);
            svg.setAttribute("viewBox", `0 0 ${totalW} ${totalH}`);
            svg.style.background = "#142818";
            svg.style.borderRadius = "8px";
            svg.style.boxShadow = "0 6px 18px rgba(15,23,42,0.16)";
            svg.style.display = "block";
            svg.style.maxWidth = "100%";
            svg.style.height = "auto";
            svg.style.touchAction = "manipulation";

            for (let c = 0; c < 8; c++) {
                const t = document.createElementNS(SVG_NS, "text");
                t.setAttribute("x", marginLeft + c * cellSize + cellSize / 2);
                t.setAttribute("y", marginTop - 8);
                t.setAttribute("text-anchor", "middle");
                t.setAttribute("fill", "#f8fafc");
                t.setAttribute("font-family", "Arial, sans-serif");
                t.setAttribute("font-size", Math.max(13, Math.floor(cellSize * 0.30)));
                t.setAttribute("font-weight", "bold");
                t.textContent = colNames[c];
                svg.appendChild(t);
            }

            for (let r = 0; r < 8; r++) {
                const t = document.createElementNS(SVG_NS, "text");
                t.setAttribute("x", marginLeft / 2);
                t.setAttribute("y", marginTop + r * cellSize + cellSize / 2 + 5);
                t.setAttribute("text-anchor", "middle");
                t.setAttribute("fill", "#f8fafc");
                t.setAttribute("font-family", "Arial, sans-serif");
                t.setAttribute("font-size", Math.max(13, Math.floor(cellSize * 0.30)));
                t.setAttribute("font-weight", "bold");
                t.textContent = rowNames[r];
                svg.appendChild(t);
            }

            const cellRects = [];
            const discs = [];
            const flipRings = [];
            const plusGroups = [];
            const hintDots = [];

            for (let r = 0; r < 8; r++) {
                cellRects[r] = [];
                discs[r] = [];
                flipRings[r] = [];
                plusGroups[r] = [];
                hintDots[r] = [];
                for (let c = 0; c < 8; c++) {
                    const x = marginLeft + c * cellSize;
                    const y = marginTop + r * cellSize;
                    const cx = x + cellSize / 2;
                    const cy = y + cellSize / 2;

                    const rect = document.createElementNS(SVG_NS, "rect");
                    rect.setAttribute("x", x);
                    rect.setAttribute("y", y);
                    rect.setAttribute("width", cellSize);
                    rect.setAttribute("height", cellSize);
                    rect.setAttribute("fill", "#2e8540");
                    rect.setAttribute("stroke", "#07190b");
                    rect.setAttribute("stroke-width", "1.8");
                    svg.appendChild(rect);
                    cellRects[r][c] = rect;

                    const ring = document.createElementNS(SVG_NS, "circle");
                    ring.setAttribute("cx", cx);
                    ring.setAttribute("cy", cy);
                    ring.setAttribute("r", stoneR + 2.2);
                    ring.setAttribute("fill", "none");
                    ring.setAttribute("stroke", "#fbbf24");
                    ring.setAttribute("stroke-width", "2.2");
                    ring.setAttribute("stroke-opacity", "0");
                    ring.style.pointerEvents = "none";
                    svg.appendChild(ring);
                    flipRings[r][c] = ring;

                    const disc = document.createElementNS(SVG_NS, "ellipse");
                    disc.setAttribute("cx", cx);
                    disc.setAttribute("cy", cy);
                    disc.setAttribute("rx", stoneR);
                    disc.setAttribute("ry", stoneR);
                    disc.style.pointerEvents = "none";
                    disc.style.display = "none";
                    svg.appendChild(disc);
                    discs[r][c] = disc;

                    const pg = document.createElementNS(SVG_NS, "g");
                    pg.style.pointerEvents = "none";
                    pg.style.display = "none";
                    const l1 = document.createElementNS(SVG_NS, "line");
                    l1.setAttribute("x1", cx - plusLen);
                    l1.setAttribute("y1", cy);
                    l1.setAttribute("x2", cx + plusLen);
                    l1.setAttribute("y2", cy);
                    l1.setAttribute("stroke", "#ef4444");
                    l1.setAttribute("stroke-width", "3.2");
                    l1.setAttribute("stroke-linecap", "round");
                    const l2 = document.createElementNS(SVG_NS, "line");
                    l2.setAttribute("x1", cx);
                    l2.setAttribute("y1", cy - plusLen);
                    l2.setAttribute("x2", cx);
                    l2.setAttribute("y2", cy + plusLen);
                    l2.setAttribute("stroke", "#ef4444");
                    l2.setAttribute("stroke-width", "3.2");
                    l2.setAttribute("stroke-linecap", "round");
                    pg.appendChild(l1);
                    pg.appendChild(l2);
                    svg.appendChild(pg);
                    plusGroups[r][c] = pg;

                    const dot = document.createElementNS(SVG_NS, "circle");
                    dot.setAttribute("cx", cx);
                    dot.setAttribute("cy", cy);
                    dot.setAttribute("r", dotR);
                    dot.setAttribute("fill", "rgba(255, 255, 255, 0.45)");
                    dot.style.pointerEvents = "none";
                    dot.style.display = "none";
                    svg.appendChild(dot);
                    hintDots[r][c] = dot;
                }
            }

            for (const sr of [2, 6]) {
                for (const sc of [2, 6]) {
                    const star = document.createElementNS(SVG_NS, "circle");
                    star.setAttribute("cx", marginLeft + sc * cellSize);
                    star.setAttribute("cy", marginTop + sr * cellSize);
                    star.setAttribute("r", starR);
                    star.setAttribute("fill", "#051208");
                    star.style.pointerEvents = "none";
                    svg.appendChild(star);
                }
            }

            container.appendChild(svg);
            root.appendChild(container);

            refs = {
                cellSize,
                container,
                captionBox,
                svg,
                cellRects,
                discs,
                flipRings,
                plusGroups,
                hintDots,
                board: payload.initial_board.map(row => row.slice()),
                lastGameId: payload.game_id,
                optimisticPly: -1,
                optimisticAt: 0,
                optimisticPromise: Promise.resolve(),
                renderSeq: 0,
            };
            parentElement._boardRefs = refs;
        }

        if (refs.lastGameId !== payload.game_id) {
            refs.lastGameId = payload.game_id;
            refs.optimisticPly = -1;
            refs.optimisticPromise = Promise.resolve();
        }

        const { captionBox, cellRects, discs, flipRings, plusGroups, hintDots } = refs;
        const myRenderSeq = ++refs.renderSeq;
        const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

        const setDiscColor = (disc, colorVal) => {
            if (colorVal === 1) {
                disc.setAttribute("fill", "#050505");
                disc.setAttribute("stroke", "#000000");
                disc.setAttribute("stroke-width", "1");
                disc.style.display = "";
            } else if (colorVal === -1) {
                disc.setAttribute("fill", "#ffffff");
                disc.setAttribute("stroke", "#1e293b");
                disc.setAttribute("stroke-width", "1");
                disc.style.display = "";
            } else {
                disc.style.display = "none";
            }
        };

        const syncBoardToDOM = (boardArr) => {
            for (let r = 0; r < 8; r++) {
                for (let c = 0; c < 8; c++) {
                    refs.board[r][c] = boardArr[r][c];
                    setDiscColor(discs[r][c], boardArr[r][c]);
                    discs[r][c].setAttribute("rx", stoneR);
                    discs[r][c].setAttribute("ry", stoneR);
                }
            }
        };

        const getFlipsJS = (b, r, c, color) => {
            const dirs = [[-1,-1],[-1,0],[-1,1],[0,-1],[0,1],[1,-1],[1,0],[1,1]];
            const flipped = [];
            for (const [dr, dc] of dirs) {
                const line = [];
                let nr = r + dr, nc = c + dc;
                while (nr >= 0 && nr < 8 && nc >= 0 && nc < 8 && b[nr][nc] === -color) {
                    line.push([nr, nc]);
                    nr += dr;
                    nc += dc;
                }
                if (line.length > 0 && nr >= 0 && nr < 8 && nc >= 0 && nc < 8 && b[nr][nc] === color) {
                    for (const pt of line) flipped.push(pt);
                }
            }
            flipped.sort((a, bPt) => Math.max(Math.abs(a[0] - r), Math.abs(a[1] - c)) - Math.max(Math.abs(bPt[0] - r), Math.abs(bPt[1] - c)));
            return flipped;
        };

        const animatePopIn = (disc, colorVal, durationMs = 150) => {
            return new Promise((resolve) => {
                setDiscColor(disc, colorVal);
                const start = performance.now();
                const tick = (now) => {
                    const t = Math.min(1, (now - start) / durationMs);
                    const ease = 1 - Math.pow(1 - t, 3);
                    const rNow = stoneR * (0.25 + 0.75 * ease);
                    disc.setAttribute("rx", rNow.toFixed(2));
                    disc.setAttribute("ry", rNow.toFixed(2));
                    if (t < 1) {
                        requestAnimationFrame(tick);
                    } else {
                        disc.setAttribute("rx", stoneR);
                        disc.setAttribute("ry", stoneR);
                        resolve();
                    }
                };
                requestAnimationFrame(tick);
            });
        };

        const animateFlipDisc = (disc, oldColor, newColor, delayMs = 0, durationMs = 280) => {
            return new Promise((resolve) => {
                setTimeout(() => {
                    const start = performance.now();
                    let swapped = false;
                    const tick = (now) => {
                        const t = Math.min(1, (now - start) / durationMs);
                        let scaleX;
                        if (t < 0.5) {
                            scaleX = 1 - t * 2;
                        } else {
                            if (!swapped) {
                                setDiscColor(disc, newColor);
                                swapped = true;
                            }
                            scaleX = (t - 0.5) * 2;
                        }
                        const liftY = 1 + 0.11 * Math.sin(t * Math.PI);
                        disc.setAttribute("rx", Math.max(0.5, stoneR * scaleX).toFixed(2));
                        disc.setAttribute("ry", (stoneR * liftY).toFixed(2));
                        if (t < 1) {
                            requestAnimationFrame(tick);
                        } else {
                            setDiscColor(disc, newColor);
                            disc.setAttribute("rx", stoneR);
                            disc.setAttribute("ry", stoneR);
                            resolve();
                        }
                    };
                    requestAnimationFrame(tick);
                }, delayMs);
            });
        };

        const clearAllHighlights = () => {
            for (let r = 0; r < 8; r++) {
                for (let c = 0; c < 8; c++) {
                    cellRects[r][c].setAttribute("fill", "#2e8540");
                    flipRings[r][c].setAttribute("stroke-opacity", "0");
                    plusGroups[r][c].style.display = "none";
                }
            }
        };

        const disableAllClicks = () => {
            for (let rr = 0; rr < 8; rr++) {
                for (let cc = 0; cc < 8; cc++) {
                    hintDots[rr][cc].style.display = "none";
                    cellRects[rr][cc].onclick = null;
                    cellRects[rr][cc].classList.remove("legal-cell");
                }
            }
        };

        const activateFinalInteractiveState = () => {
            disableAllClicks();
            clearAllHighlights();
            syncBoardToDOM(payload.final_board);

            if (payload.final_last_move) {
                const [lr, lc] = payload.final_last_move;
                cellRects[lr][lc].setAttribute("fill", "#399b4f");
                plusGroups[lr][lc].style.display = "";
            }
            if (payload.final_flipped) {
                for (const [fr, fc] of payload.final_flipped) {
                    cellRects[fr][fc].setAttribute("fill", "#338f47");
                    flipRings[fr][fc].setAttribute("stroke-opacity", "0.85");
                }
            }
            if (payload.interactive && payload.legal_moves) {
                const userColor = payload.user_color || 1;
                for (const item of payload.legal_moves) {
                    const [r, c] = item.rc;
                    const mvStr = item.move_str;
                    const rect = cellRects[r][c];
                    rect.classList.add("legal-cell");
                    rect.setAttribute("data-move", mvStr);
                    hintDots[r][c].style.display = "";
                    rect.onclick = () => {
                        disableAllClicks();
                        const flipped = getFlipsJS(refs.board, r, c, userColor);
                        const myPly = (payload.current_ply || 0) + 1;
                        refs.optimisticPly = myPly;
                        refs.optimisticAt = performance.now();

                        // 裏で即座にAI思考を開始させつつ、ブラウザ側では0ミリ秒で自分の着手＆石返しアニメーションを再生
                        setTriggerValue("clicked", mvStr);

                        refs.optimisticPromise = (async () => {
                            clearAllHighlights();
                            const stoneIcon = userColor === 1 ? "⚫ 黒" : "⚪ 白";
                            captionBox.textContent = `${stoneIcon}（あなた）が ${mvStr} に着手！ (${flipped.length}石を裏返します)`;
                            cellRects[r][c].setAttribute("fill", "#42ad5a");
                            plusGroups[r][c].style.display = "";
                            await animatePopIn(discs[r][c], userColor, 145);
                            refs.board[r][c] = userColor;

                            const flipPromises = flipped.map(([fr, fc], idx) => {
                                const oldColor = refs.board[fr][fc];
                                refs.board[fr][fc] = userColor;
                                cellRects[fr][fc].setAttribute("fill", "#35944a");
                                flipRings[fr][fc].setAttribute("stroke-opacity", "0.9");
                                return animateFlipDisc(discs[fr][fc], oldColor, userColor, idx * 65, 260);
                            });
                            await Promise.all(flipPromises);
                            captionBox.textContent = `🤖 AIが次の一手を思考中...`;
                        })();
                    };
                }
            }
            captionBox.textContent = payload.default_caption || "";
        };

        (async () => {
            await refs.optimisticPromise;
            if (myRenderSeq !== refs.renderSeq) return;

            // 自分の手をサーバーが受け取る前の盤面が届いたとき（Wi-Fi対戦は1秒ごとに画面を更新するため起きる）は、
            // その古い盤面に戻さず、先に打って見せている盤面のままにする。
            // 数秒たっても追いつかなければ（着手が通らなかったなど）、届いた盤面に合わせる
            if (refs.optimisticPly > (payload.current_ply || 0)) {
                if (performance.now() - refs.optimisticAt < 5000) return;
                refs.optimisticPly = -1;
            }

            const allSteps = payload.steps || [];
            const remainingSteps = allSteps.filter(s => s.ply > refs.optimisticPly);

            if (allSteps.length === 0) {
                activateFinalInteractiveState();
                return;
            }

            // クイックボタン経由など、ブラウザ側で先行アニメーションしていない場合は initial_board から同期
            if (remainingSteps.length === allSteps.length) {
                syncBoardToDOM(payload.initial_board);
            }

            for (let sIdx = 0; sIdx < remainingSteps.length; sIdx++) {
                if (myRenderSeq !== refs.renderSeq) return;
                const stp = remainingSteps[sIdx];
                if (stp.actor === "AI") {
                    captionBox.textContent = `🤖 AIが ${stp.move_str} に着手しています...`;
                    await sleep(160);
                }
                if (myRenderSeq !== refs.renderSeq) return;

                clearAllHighlights();
                const [mr, mc] = stp.move;
                const stoneIcon = stp.player === 1 ? "⚫ 黒" : "⚪ 白";
                captionBox.textContent = `${stoneIcon}（${stp.actor}）が ${stp.move_str} に着手！ (${stp.flipped.length}石を裏返します)`;

                cellRects[mr][mc].setAttribute("fill", "#42ad5a");
                plusGroups[mr][mc].style.display = "";
                await animatePopIn(discs[mr][mc], stp.player, 155);
                refs.board[mr][mc] = stp.player;

                await sleep(60);

                const flipPromises = stp.flipped.map(([fr, fc], idx) => {
                    const oldColor = refs.board[fr][fc];
                    refs.board[fr][fc] = stp.player;
                    cellRects[fr][fc].setAttribute("fill", "#35944a");
                    flipRings[fr][fc].setAttribute("stroke-opacity", "0.9");
                    return animateFlipDisc(discs[fr][fc], oldColor, stp.player, idx * 70, 280);
                });
                await Promise.all(flipPromises);
                await sleep(120);
            }

            if (myRenderSeq === refs.renderSeq) {
                activateFinalInteractiveState();
            }
        })();
    }
    """,
    css="""
    .othello-board-wrap {
        display: flex;
        justify-content: center;
        align-items: center;
        padding: 4px;
        user-select: none;
    }
    .anim-status-pill {
        background: rgba(255, 255, 255, 0.88);
        border: 1px solid #c7d2fe;
        color: #2b3048;
        font-size: 13.5px;
        font-weight: 700;
        padding: 5px 14px;
        border-radius: 999px;
        box-shadow: 0 2px 8px rgba(139, 148, 214, 0.15);
    }
    .legal-cell {
        cursor: pointer;
        transition: fill 0.12s ease;
    }
    .legal-cell:hover {
        fill: #42ad5a !important;
    }
    """,
)


# Wi-Fi対戦の接続URLを「コピー」「シェア」するボタン（Streamlit v2 コンポーネント）
# - コピー: navigator.clipboard は https / localhost でしか使えないため、LAN の http で開いたときは
#   一時 textarea + execCommand('copy') にフォールバックする
# - シェア: スマホ等で Web Share API があれば共有メニュー（LINE など）を開く。無いブラウザでは
#   LINE / メールで送るリンクを表示する
SHARE_BUTTON_COMPONENT = st.components.v2.component(
    "share_url_buttons",
    html='<div class="share-root"></div>',
    js="""
    export default function(component) {
        const { data, parentElement } = component;
        let root = parentElement.querySelector('.share-root');
        if (!root) {
            root = document.createElement('div');
            root.className = 'share-root';
            parentElement.appendChild(root);
        }
        if (!data) return;
        let p = data;
        try {
            p = typeof data === 'string' ? JSON.parse(data) : data;
        } catch (e) {
            return;
        }
        const url = p.url || '';
        const shareText = p.share_text || url;
        const shareMessage = p.share_message || '';
        const title = p.title || '';

        // ボタンは初回だけ作り、再実行（1秒ごとの自動更新など）では使い回す
        let refs = root._refs;
        if (!refs) {
            root.innerHTML = '';
            const row = document.createElement('div');
            row.className = 'share-row';
            const copyBtn = document.createElement('button');
            copyBtn.type = 'button';
            copyBtn.className = 'share-btn';
            const shareBtn = document.createElement('button');
            shareBtn.type = 'button';
            shareBtn.className = 'share-btn share-primary';
            const status = document.createElement('span');
            status.className = 'share-status';
            row.append(copyBtn, shareBtn, status);

            const menu = document.createElement('div');
            menu.className = 'share-menu';
            menu.style.display = 'none';
            const line = document.createElement('a');
            line.className = 'share-link share-line';
            line.target = '_blank';
            line.rel = 'noopener';
            line.textContent = 'LINEで送る';
            const mail = document.createElement('a');
            mail.className = 'share-link share-mail';
            mail.textContent = 'メールで送る';
            menu.append(line, mail);

            root.append(row, menu);
            refs = { copyBtn, shareBtn, status, menu, line, mail, timer: null };
            root._refs = refs;
        }

        refs.copyBtn.textContent = p.copy_label || '📋 URLをコピー';
        refs.shareBtn.textContent = p.share_label || '📤 シェア';
        refs.line.href = 'https://line.me/R/share?text=' + encodeURIComponent(shareText);
        refs.mail.href = 'mailto:?subject=' + encodeURIComponent(title) + '&body=' + encodeURIComponent(shareText);

        const flash = (msg, ok) => {
            refs.status.textContent = msg;
            refs.status.className = 'share-status ' + (ok ? 'ok' : 'ng');
            clearTimeout(refs.timer);
            refs.timer = setTimeout(() => { refs.status.textContent = ''; }, 2500);
        };

        const fallbackCopy = (text) => {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.top = '-1000px';
            document.body.appendChild(ta);
            ta.select();
            ta.setSelectionRange(0, text.length);
            let ok = false;
            try {
                ok = document.execCommand('copy');
            } catch (e) {
                ok = false;
            }
            document.body.removeChild(ta);
            return ok;
        };

        refs.copyBtn.onclick = async () => {
            let ok = false;
            if (navigator.clipboard && window.isSecureContext) {
                try {
                    await navigator.clipboard.writeText(url);
                    ok = true;
                } catch (e) {
                    ok = false;
                }
            }
            if (!ok) ok = fallbackCopy(url);
            flash(ok ? '✅ コピーしました' : '⚠️ コピーできませんでした。URLを長押ししてコピーしてください', ok);
        };

        refs.shareBtn.onclick = async () => {
            if (navigator.share) {
                try {
                    await navigator.share({ title: title, text: shareMessage, url: url });
                    return;
                } catch (e) {
                    if (e && e.name === 'AbortError') return;  // 共有メニューを閉じただけ
                }
            }
            // 共有メニューが使えないブラウザ（PC の http など）は送り先リンクを出す
            refs.menu.style.display = refs.menu.style.display === 'none' ? 'flex' : 'none';
        };
    }
    """,
    css="""
    .share-row {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px;
        margin: 4px 0 2px 0;
    }
    .share-btn {
        border: 1.5px solid #a5b4fc;
        background: #ffffff;
        color: #3730a3;
        border-radius: 10px;
        padding: 6px 14px;
        font-size: 14px;
        font-weight: 700;
        cursor: pointer;
        font-family: inherit;
    }
    .share-btn:hover {
        background: #eef2ff;
    }
    .share-primary {
        background: linear-gradient(135deg, #7f9cf5 0%, #9b87f5 100%);
        border-color: transparent;
        color: #ffffff;
    }
    .share-primary:hover {
        background: linear-gradient(135deg, #6e8ef2 0%, #8e76f2 100%);
    }
    .share-status {
        font-size: 13px;
        font-weight: 700;
    }
    .share-status.ok {
        color: #059669;
    }
    .share-status.ng {
        color: #dc2626;
    }
    .share-menu {
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 6px;
    }
    .share-link {
        display: inline-block;
        padding: 5px 14px;
        border-radius: 999px;
        font-size: 13px;
        font-weight: 700;
        text-decoration: none;
    }
    .share-line {
        background: #06c755;
        color: #ffffff;
    }
    .share-mail {
        background: #e0e7ff;
        color: #3730a3;
    }
    """,
)


def render_share_buttons(url: str, key: str, room_id: str = "") -> None:
    """Wi-Fi対戦の接続URL用「コピー」「シェア」ボタンを表示する（合言葉があればシェア文に含める）"""
    room_part = f"（合言葉: {room_id}）" if room_id else ""
    message = f"オセロで対戦しよう！同じWi-Fiにつないで、このURLを開いてね{room_part}"
    SHARE_BUTTON_COMPONENT(
        data=json.dumps(
            {
                "url": url,
                "title": "オセロ Wi-Fi対戦の招待",
                "share_message": message,
                "share_text": f"{message}\n{url}",
            },
            ensure_ascii=False,
        ),
        key=key,
    )


def build_animated_board_payload(
    game: OthelloGame,
    user_color: int,
    last_animated_ply: int,
    game_id: int = 1,
    cell_size: int = 55,
    match_mode: str = "ai",
    p1_name: str = "黒",
    p2_name: str = "白",
) -> str:
    """
    前回の描画以降に進んだ手（ユーザーの手＋AIの応手、または対人戦の着手）を抽出し、
    石を置いてパタパタ裏返すアニメーション用のJSONペイロードを生成する。
    """
    new_records = game.history[last_animated_ply:] if last_animated_ply < len(game.history) else []
    if new_records:
        initial_board = new_records[0].board_before
    else:
        initial_board = game.board

    steps = []
    for rec in new_records:
        if rec.move is None:
            continue
        mr, mc = rec.move
        # 着手位置から近い順にソートして、波紋のように順番にパタパタ裏返るようにする
        sorted_flips = sorted(
            rec.flipped,
            key=lambda rc: max(abs(rc[0] - mr), abs(rc[1] - mc)),
        )
        if match_mode in ("pvp", "online"):
            actor = p1_name if rec.player == BLACK else p2_name
        else:
            actor = "あなた" if rec.player == user_color else "AI"
        steps.append(
            {
                "ply": rec.ply,
                "player": rec.player,
                "actor": actor,
                "move": [mr, mc],
                "move_str": rec.move_str,
                "flipped": [[fr, fc] for fr, fc in sorted_flips],
            }
        )

    if match_mode == "pvp":
        user_can_click = not game.game_over
    else:
        user_can_click = (not game.game_over) and (game.current_turn == user_color)

    legal_dict = get_legal_moves(game.board, game.current_turn) if user_can_click else {}
    legal_list = [
        {"rc": [r, c], "move_str": coord_to_str(r, c), "flips_count": len(flips)}
        for (r, c), flips in legal_dict.items()
    ]

    last_rec = game.history[-1] if game.history else None
    final_last_move = list(last_rec.move) if (last_rec and last_rec.move) else None
    final_flipped = [[fr, fc] for fr, fc in last_rec.flipped] if last_rec else []

    if game.game_over:
        default_caption = "🏁 終局しました（赤い『＋』が最終手、金色の輪が裏返った石です）"
    elif last_rec and last_rec.move:
        if match_mode in ("pvp", "online"):
            actor_str = f"⚫ {p1_name}" if last_rec.player == BLACK else f"⚪ {p2_name}"
        else:
            actor_str = "あなた" if last_rec.player == user_color else "AI"
        default_caption = (
            f"直前の手: {actor_str}が {last_rec.move_str} に着手（赤い『＋』＝着手位置 ／ 金色の輪＝裏返った{len(last_rec.flipped)}石）"
        )
    else:
        if match_mode in ("pvp", "online"):
            curr_actor = p1_name if game.current_turn == BLACK else p2_name
            curr_c = "⚫ 黒" if game.current_turn == BLACK else "⚪ 白"
            default_caption = f"盤面の白いドットをクリックして {curr_c}（{curr_actor}）が着手します"
        else:
            default_caption = "盤面の白いドットをクリックすると着手します"

    payload = {
        "game_id": game_id,
        "current_ply": len(game.history),
        "cell_size": cell_size,
        "user_color": user_color,
        "initial_board": initial_board,
        "final_board": game.board,
        "steps": steps,
        "final_last_move": final_last_move,
        "final_flipped": final_flipped,
        "interactive": user_can_click,
        "legal_moves": legal_list,
        "default_caption": default_caption,
    }
    return json.dumps(payload, ensure_ascii=False)


def _hint_cell_svg(
    x: float,
    y: float,
    cx: float,
    cy: float,
    cell_size: int,
    score: float,
    best_score: float,
    is_best: bool,
    is_actual: bool,
    is_selected: bool,
) -> str:
    """
    空きマス1つ分の「評価値ヒント」SVG。
    緑のマス＝最善手、金の点線＝実戦で打たれた手、青枠＝選択中の手。数字は固定した目線（あなた／黒）から見た予想最終石差。
    最善より4石以上悪い手の数字は赤みのある色にする。
    """
    parts: List[str] = []
    if is_best:
        parts.append(
            f'<rect x="{x + 2}" y="{y + 2}" width="{cell_size - 4}" height="{cell_size - 4}" rx="4" '
            f'fill="rgba(34, 197, 94, 0.55)" stroke="#bbf7d0" stroke-width="1.6" pointer-events="none" />'
        )
    if is_actual:
        parts.append(
            f'<rect x="{x + 3}" y="{y + 3}" width="{cell_size - 6}" height="{cell_size - 6}" rx="4" '
            f'fill="none" stroke="#facc15" stroke-width="2.4" stroke-dasharray="5,3" pointer-events="none" />'
        )
    if is_selected:
        parts.append(
            f'<rect x="{x + 1.5}" y="{y + 1.5}" width="{cell_size - 3}" height="{cell_size - 3}" rx="5" '
            f'fill="none" stroke="#38bdf8" stroke-width="3.2" pointer-events="none" />'
        )
    label = "±0" if round(score) == 0 else f"{score:+.0f}"
    weak = abs(best_score - score) >= 4.0 and not is_best
    color = "#fecaca" if weak else "#ffffff"
    parts.append(
        f'<text x="{cx}" y="{cy + cell_size * 0.14}" text-anchor="middle" fill="{color}" '
        f'stroke="#052e16" stroke-width="2.4" paint-order="stroke" '
        f'font-family="Arial, sans-serif" font-size="{int(cell_size * 0.36)}" font-weight="bold" pointer-events="none">{label}</text>'
    )
    tag = "最善=実戦" if (is_best and is_actual) else ("最善" if is_best else ("実戦" if is_actual else ""))
    if tag:
        parts.append(
            f'<text x="{cx}" y="{y + cell_size * 0.26}" text-anchor="middle" fill="#fef9c3" '
            f'stroke="#052e16" stroke-width="2" paint-order="stroke" '
            f'font-family="sans-serif" font-size="{max(9, int(cell_size * 0.17))}" font-weight="bold" pointer-events="none">{tag}</text>'
        )
    return "".join(parts)


def _rebase_best(candidates: List[Dict[str, Any]], score_key: str, engine_pick: Optional[Tuple[int, int]]) -> Tuple[Optional[Tuple[int, int]], float]:
    """
    候補手リストの中で評価が最大の手を「最善」とし、各候補の loss / is_best を付け直す。
    同点ならエンジンが局面全体の評価で選んだ手を優先する。戻り値: (最善手, 最善の評価値)
    """
    if not candidates:
        return engine_pick, 0.0
    top = max(c[score_key] for c in candidates)
    # 盤面には整数で表示するので、表示上同じ数字の手は同点（優劣なし）として全部「最善」にする
    tied = [c for c in candidates if round(c[score_key]) == round(top)]
    pick = next((c for c in tied if c["move"] == engine_pick), tied[0])
    tied_moves = {c["move"] for c in tied}
    for c in candidates:
        c["loss"] = 0.0 if c["move"] in tied_moves else round(max(0.0, top - c[score_key]), 1)
        c["is_best"] = c["move"] in tied_moves
    return pick["move"], top


def _opponent_name_from_record(engine_name: str, user_color: int) -> str:
    """対局記録のエンジン名から相手の名前を取り出す（例: 『Wi-Fi対戦 (あなた vs 友達)』→ 友達）"""
    name = engine_name or ""
    if not (name.startswith("対人戦") or name.startswith("Wi-Fi対戦")):
        return name  # AI対戦はエンジン名そのもの
    m = re.search(r"\((.*)\)\s*$", name)
    inner = m.group(1).strip() if m else name
    if inner.startswith("vs "):
        return inner[3:].strip() or "相手"
    if " vs " in inner:
        black_name, white_name = (x.strip() for x in inner.split(" vs ", 1))
        return (white_name if user_color == BLACK else black_name) or "相手"
    return inner or "相手"


def _md_breaks(text: str) -> str:
    """
    解説文の1行改行をそのまま改行として表示する（Markdown は単独の改行を空白扱いにして
    【判定】【局面フェーズ】■主な理由… が1段落に詰まってしまうため）。箇条書き・見出し・空行の前後は触らない。
    """
    lines = (text or "").split("\n")
    out = []
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        keep_plain = (
            not line.strip() or not nxt.strip()
            or nxt.lstrip().startswith(("- ", "* ", "#", "|"))
            or line.lstrip().startswith(("#", "|"))
        )
        out.append(line if keep_plain else line + "  ")
    return "\n".join(out)


def _perspective_pill_html(who: str, mover_is_viewer: bool = True, mover: str = "相手") -> str:
    """盤面の上に出す「数字は誰から見た石差か」の控えめな1行（目線は固定なので、手番が相手のときだけ一言足す）"""
    extra = "" if mover_is_viewer else f"・{mover}番は小さいほど{mover}の好手"
    return (
        '<div style="font-size:12.5px; color:#64748b; margin:0 0 4px 2px;">'
        f'数字＝{who}目線の最終石差{extra}</div>'
    )


def _as_candidate_text(text: str) -> str:
    """
    tactics の解説は「実戦手 vs 最善手」の書き方なので、検討中の“仮の手”の説明として読めるよう言い換える
    （棋譜再生・局面検討タブで、まだ打たれていない候補手を「実戦」と呼ばないため）。
    """
    return (
        text.replace("実戦手：", "選んだ手：")
        .replace("実戦の ", "")
        .replace("実戦 ", "選んだ手 ")
        .replace("実戦は", "選んだ手は")
        .replace("実戦では", "選んだ手では")
    )


def render_html_board(
    board: List[List[int]],
    actual_move: Optional[Tuple[int, int]] = None,
    best_move: Optional[Tuple[int, int]] = None,
    candidate_moves: Optional[List[Tuple[int, int]]] = None,
    legal_moves: Optional[Dict[Tuple[int, int], Any]] = None,
    last_move: Optional[Tuple[int, int]] = None,
    last_move_color: str = "#ef4444",
    flipped: Optional[List[Tuple[int, int]]] = None,
    reply_move: Optional[Tuple[int, int]] = None,
    reply_label: str = "急所",
    cell_size: int = 54,
    interactive: bool = False,
    hint_scores: Optional[Dict[Tuple[int, int], float]] = None,
    hint_higher_is_better: bool = True,
    hint_best: Optional[List[Tuple[int, int]]] = None,
    hint_actual: Optional[Tuple[int, int]] = None,
    hint_selected: Optional[Tuple[int, int]] = None,
) -> str:
    """
    参考画像2に忠実な本格オセロ盤SVGレンダラー。
    - 隙間なしの8x8グリーン盤面 (#2e8540) と黒の細い罫線
    - 4つの星（黒ポッチ: c3, f3, c6, f6 の交点）
    - マスいっぱいに大きく描画されるくっきりした黒石・白石
    - 直前の着手石の中央に赤い「＋」マーカーを表示
    - interactive=True の場合は合法手マスを直接クリックして着手可能
    """
    legal_set = set(legal_moves.keys()) if legal_moves else set()
    cand_set = set(candidate_moves) if candidate_moves else set()
    flipped_set = set(flipped) if flipped else set()
    margin_top = 28
    margin_left = 28
    margin_right = 10
    margin_bottom = 10
    grid_size = cell_size * 8
    total_w = margin_left + grid_size + margin_right
    total_h = margin_top + grid_size + margin_bottom
    stone_r = round(cell_size * 0.415, 1)

    svg = [
        f'<div class="othello-board-wrap">',
        f'<svg width="{total_w}" height="{total_h}" viewBox="0 0 {total_w} {total_h}" '
        f'style="background:#142818; border-radius:8px; box-shadow:0 6px 18px rgba(15,23,42,0.16); display:block; max-width:100%; height:auto;">',
    ]

    # 上部の列ラベル (a ~ h)
    for c in range(8):
        cx = margin_left + c * cell_size + cell_size / 2
        svg.append(
            f'<text x="{cx}" y="{margin_top - 8}" text-anchor="middle" '
            f'fill="#f8fafc" font-family="Arial, sans-serif" font-size="{max(13, int(cell_size*0.30))}" font-weight="bold">{COL_NAMES[c]}</text>'
        )

    # 左側の行ラベル (1 ~ 8)
    for r in range(8):
        cy = margin_top + r * cell_size + cell_size / 2 + 5
        svg.append(
            f'<text x="{margin_left / 2}" y="{cy}" text-anchor="middle" '
            f'fill="#f8fafc" font-family="Arial, sans-serif" font-size="{max(13, int(cell_size*0.30))}" font-weight="bold">{ROW_NAMES[r]}</text>'
        )

    # 8x8 の各マスと石の描画
    for r in range(8):
        for c in range(8):
            x = margin_left + c * cell_size
            y = margin_top + r * cell_size
            cx = x + cell_size / 2
            cy = y + cell_size / 2
            val = board[r][c]
            mv_str = coord_to_str(r, c)
            is_legal = (r, c) in legal_set

            fill_color = "#2e8540"
            if (r, c) == actual_move and (r, c) == best_move:
                fill_color = "#059669"
            elif (r, c) == actual_move:
                fill_color = "#b91c1c"
            elif (r, c) == best_move:
                fill_color = "#0284c7"
            elif (r, c) in cand_set:
                fill_color = "#d97706"

            if interactive and is_legal and val == EMPTY:
                svg.append(
                    f'<rect x="{x}" y="{y}" width="{cell_size}" height="{cell_size}" '
                    f'fill="{fill_color}" stroke="#07190b" stroke-width="1.8" '
                    f'class="legal-cell" data-move="{mv_str}">'
                    f'<title>{mv_str} に着手する</title></rect>'
                )
            else:
                svg.append(
                    f'<rect x="{x}" y="{y}" width="{cell_size}" height="{cell_size}" '
                    f'fill="{fill_color}" stroke="#07190b" stroke-width="1.8" />'
                )

            # 石またはマーカーの描画
            if val == EMPTY and hint_scores is not None and (r, c) in hint_scores:
                # 候補手の評価値（固定した目線から見た予想最終石差）をマスに直接書く
                # 目線の側が手番でないときは、数字が小さい手ほど手番側の好手になる
                svg.append(_hint_cell_svg(
                    x, y, cx, cy, cell_size,
                    score=hint_scores[(r, c)],
                    best_score=max(hint_scores.values()) if hint_higher_is_better else min(hint_scores.values()),
                    is_best=(r, c) in (hint_best or []),
                    is_actual=(r, c) == hint_actual,
                    is_selected=(r, c) == hint_selected,
                ))
            elif val == EMPTY:
                if (r, c) == reply_move:
                    svg.append(
                        f'<rect x="{x + 2}" y="{y + 2}" width="{cell_size - 4}" height="{cell_size - 4}" '
                        f'fill="rgba(245, 158, 11, 0.28)" stroke="#f59e0b" stroke-width="2.2" stroke-dasharray="4,2" rx="4" />'
                    )
                    svg.append(
                        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" fill="#fef3c7" '
                        f'font-family="sans-serif" font-size="{int(cell_size*0.30)}" font-weight="bold">{reply_label}</text>'
                    )
                elif (r, c) == actual_move and (r, c) == best_move:
                    svg.append(
                        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" fill="#ffffff" '
                        f'font-family="sans-serif" font-size="{int(cell_size*0.30)}" font-weight="bold">正解</text>'
                    )
                elif (r, c) == actual_move:
                    svg.append(
                        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" fill="#ffffff" '
                        f'font-family="sans-serif" font-size="{int(cell_size*0.30)}" font-weight="bold">実戦</text>'
                    )
                elif (r, c) == best_move:
                    svg.append(
                        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" fill="#ffffff" '
                        f'font-family="sans-serif" font-size="{int(cell_size*0.30)}" font-weight="bold">最善</text>'
                    )
                elif (r, c) in cand_set:
                    svg.append(
                        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" fill="#ffffff" '
                        f'font-family="sans-serif" font-size="{int(cell_size*0.30)}" font-weight="bold">迷い</text>'
                    )
                elif is_legal:
                    dot_r = round(cell_size * 0.13, 1)
                    svg.append(
                        f'<circle cx="{cx}" cy="{cy}" r="{dot_r}" fill="rgba(255, 255, 255, 0.42)" class="legal-hint" pointer-events="none" />'
                    )
            elif val == BLACK:
                svg.append(
                    f'<circle cx="{cx}" cy="{cy}" r="{stone_r}" fill="#050505" stroke="#000000" stroke-width="1" pointer-events="none" />'
                )
                if (r, c) in flipped_set:
                    svg.append(
                        f'<circle cx="{cx}" cy="{cy}" r="{stone_r + 2.2}" fill="none" stroke="#fbbf24" stroke-width="2.2" pointer-events="none" />'
                    )
                if (r, c) == last_move:
                    plus_len = round(cell_size * 0.14, 1)
                    svg.append(
                        f'<line x1="{cx - plus_len}" y1="{cy}" x2="{cx + plus_len}" y2="{cy}" stroke="{last_move_color}" stroke-width="3.2" stroke-linecap="round" pointer-events="none" />'
                        f'<line x1="{cx}" y1="{cy - plus_len}" x2="{cx}" y2="{cy + plus_len}" stroke="{last_move_color}" stroke-width="3.2" stroke-linecap="round" pointer-events="none" />'
                    )
            elif val == WHITE:
                svg.append(
                    f'<circle cx="{cx}" cy="{cy}" r="{stone_r}" fill="#ffffff" stroke="#1e293b" stroke-width="1" pointer-events="none" />'
                )
                if (r, c) in flipped_set:
                    svg.append(
                        f'<circle cx="{cx}" cy="{cy}" r="{stone_r + 2.2}" fill="none" stroke="#fbbf24" stroke-width="2.2" pointer-events="none" />'
                    )
                if (r, c) == last_move:
                    plus_len = round(cell_size * 0.14, 1)
                    svg.append(
                        f'<line x1="{cx - plus_len}" y1="{cy}" x2="{cx + plus_len}" y2="{cy}" stroke="{last_move_color}" stroke-width="3.2" stroke-linecap="round" pointer-events="none" />'
                        f'<line x1="{cx}" y1="{cy - plus_len}" x2="{cx}" y2="{cy + plus_len}" stroke="{last_move_color}" stroke-width="3.2" stroke-linecap="round" pointer-events="none" />'
                    )
            elif is_legal:
                dot_r = round(cell_size * 0.13, 1)
                svg.append(
                    f'<circle cx="{cx}" cy="{cy}" r="{dot_r}" fill="rgba(255, 255, 255, 0.42)" class="legal-hint" pointer-events="none" />'
                )

    # 4つの星（黒ポッチ: (2,2), (2,6), (6,2), (6,6) の交点）を描画
    star_r = max(3.0, round(cell_size * 0.068, 1))
    for sr in (2, 6):
        for sc in (2, 6):
            sx = margin_left + sc * cell_size
            sy = margin_top + sr * cell_size
            svg.append(
                f'<circle cx="{sx}" cy="{sy}" r="{star_r}" fill="#051208" pointer-events="none" />'
            )

    svg.append("</svg></div>")
    return "".join(svg)


def build_eval_chart(analyses: List[PlyAnalysis], user_color: int) -> go.Figure:
    """
    終局後の形勢グラフ（ユーザー視点の予測最終石差推移）を Plotly で作成する。
    悪手（Blunder）箇所には赤色の強調マーカーを表示する。
    """
    plies = [0] + [a.ply for a in analyses]
    evals = [0.0] + [a.eval_after_user for a in analyses]
    hover_texts = ["開始局面 (0.0)"] + [
        f"{a.ply}手目 ({'あなた' if a.is_user_turn else 'AI'}): {a.move_str}<br>"
        f"評価値(予測石差): {a.eval_after_user:+.1f} 石 (変動 {a.eval_diff_user:+.1f})<br>"
        f"推奨最善手: {a.best_move_str}"
        for a in analyses
    ]

    fig = go.Figure()

    # 0ライン（互角ライン）
    fig.add_hline(y=0, line_dash="dash", line_color="#94a3b8", annotation_text="互角 (0)")

    # 評価値推移ライン
    fig.add_trace(
        go.Scatter(
            x=plies,
            y=evals,
            mode="lines+markers",
            name="形勢（あなた視点の予測石差）",
            line=dict(color="#10b981", width=3),
            marker=dict(size=6, color="#059669"),
            hovertext=hover_texts,
            hoverinfo="text",
            fill="tozeroy",
            fillcolor="rgba(16, 185, 129, 0.12)",
        )
    )

    # 悪手（Blunder）ポイントの強調表示
    blunders = [a for a in analyses if a.is_blunder]
    if blunders:
        fig.add_trace(
            go.Scatter(
                x=[b.ply for b in blunders],
                y=[b.eval_after_user for b in blunders],
                mode="markers+text",
                name="悪手 (Blunder)",
                text=[f"❗{b.ply}手目({b.move_str})" for b in blunders],
                textposition="bottom center",
                marker=dict(size=13, color="#ef4444", symbol="x", line=dict(width=2, color="#ffffff")),
                hovertext=[
                    f"【悪手】{b.ply}手目: 実戦 {b.move_str} (最善 {b.best_move_str})<br>"
                    f"評価値下落: {b.eval_before_user:+.1f} → {b.eval_after_user:+.1f} ({b.eval_diff_user:+.1f}石)"
                    for b in blunders
                ],
                hoverinfo="text",
            )
        )

    fig.update_layout(
        title=f"📈 形勢推移グラフ（{color_name(user_color)}＝あなた視点の最終予測石差）",
        xaxis_title="手数 (Ply)",
        yaxis_title="予測最終石差 (＋があなた有利 / －がAI有利)",
        xaxis=dict(fixedrange=True),
        yaxis=dict(range=[-66, 66], zeroline=True, fixedrange=True),
        dragmode=False,
        height=390,
        margin=dict(l=30, r=30, t=55, b=35),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_white",
    )
    return fig


# ================= 局面検討（Sandbox）用プリセット =================
PRESET_USER_Q1 = [
    [WHITE, WHITE, WHITE, WHITE, WHITE, WHITE, WHITE, BLACK],
    [WHITE, BLACK, BLACK, BLACK, BLACK, WHITE, BLACK, BLACK],
    [WHITE, BLACK, BLACK, BLACK, WHITE, BLACK, BLACK, BLACK],
    [WHITE, BLACK, WHITE, WHITE, BLACK, BLACK, BLACK, BLACK],
    [WHITE, BLACK, WHITE, BLACK, WHITE, WHITE, BLACK, BLACK],
    [WHITE, BLACK, BLACK, BLACK, WHITE, BLACK, BLACK, BLACK],
    [WHITE, BLACK, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
    [EMPTY, BLACK, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
]

PRESET_USER_Q2 = [
    [EMPTY, BLACK, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
    [WHITE, EMPTY, BLACK, BLACK, BLACK, BLACK, EMPTY, BLACK],
    [WHITE, WHITE, BLACK, BLACK, BLACK, BLACK, BLACK, BLACK],
    [WHITE, BLACK, WHITE, BLACK, BLACK, BLACK, BLACK, BLACK],
    [WHITE, BLACK, WHITE, WHITE, WHITE, WHITE, BLACK, BLACK],
    [WHITE, WHITE, BLACK, BLACK, WHITE, BLACK, BLACK, BLACK],
    [WHITE, EMPTY, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
    [EMPTY, BLACK, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
]

PRESET_USER_Q3 = [
    [EMPTY, BLACK, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
    [EMPTY, EMPTY, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
    [EMPTY, EMPTY, BLACK, BLACK, BLACK, BLACK, WHITE, WHITE],
    [BLACK, BLACK, BLACK, BLACK, BLACK, WHITE, WHITE, EMPTY],
    [BLACK, BLACK, WHITE, WHITE, WHITE, WHITE, WHITE, WHITE],
    [BLACK, BLACK, BLACK, BLACK, WHITE, BLACK, BLACK, BLACK],
    [EMPTY, EMPTY, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
    [EMPTY, BLACK, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
]

PRESET_NAKAWARI = [
    [EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY],
    [EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY],
    [EMPTY, EMPTY, EMPTY, BLACK, EMPTY, EMPTY, EMPTY, EMPTY],
    [EMPTY, EMPTY, BLACK, WHITE, WHITE, EMPTY, EMPTY, EMPTY],
    [EMPTY, EMPTY, EMPTY, WHITE, WHITE, EMPTY, EMPTY, EMPTY],
    [EMPTY, EMPTY, EMPTY, BLACK, BLACK, BLACK, EMPTY, EMPTY],
    [EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY],
    [EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY],
]

PRESET_STONER = [
    [EMPTY, BLACK, BLACK, BLACK, BLACK, BLACK, BLACK, EMPTY],
    [BLACK, WHITE, EMPTY, EMPTY, EMPTY, EMPTY, WHITE, BLACK],
    [BLACK, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, BLACK],
    [BLACK, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, BLACK],
    [BLACK, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, BLACK],
    [BLACK, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, BLACK],
    [BLACK, WHITE, EMPTY, EMPTY, EMPTY, EMPTY, WHITE, BLACK],
    [EMPTY, BLACK, BLACK, BLACK, BLACK, BLACK, BLACK, EMPTY],
]

PRESET_YOHANE_P42 = [
    [EMPTY, BLACK, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
    [EMPTY, EMPTY, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
    [EMPTY, EMPTY, BLACK, BLACK, BLACK, BLACK, WHITE, WHITE],
    [EMPTY, BLACK, BLACK, BLACK, BLACK, WHITE, WHITE, EMPTY],
    [EMPTY, BLACK, BLACK, WHITE, WHITE, WHITE, WHITE, WHITE],
    [BLACK, BLACK, BLACK, BLACK, WHITE, BLACK, BLACK, BLACK],
    [EMPTY, EMPTY, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
    [EMPTY, BLACK, BLACK, BLACK, BLACK, BLACK, EMPTY, EMPTY],
]

SANDBOX_PRESETS: Dict[str, Dict[str, Any]] = {
    "q1": {
        "name": "🎯 質問局面 1（終盤残り5マス: 白g7悪手 vs g8最善）",
        "board": PRESET_USER_Q1,
        "turn": WHITE,
        "sim_move": (6, 6),
        "desc": "【ユーザー質問の局面】白21個・黒38個、残り5マス（a8, g7, h7, g8, h8）。赤点のg7は11石返すものの隅h8を献上し、パスに追い込まれる大悪手（-12.0石、損失10.0石）。最善はg8（-2.0石）。",
    },
    "q2": {
        "name": "🎯 質問局面 2（中盤残り11マス: 白g2危険X打ち）",
        "board": PRESET_USER_Q2,
        "turn": WHITE,
        "sim_move": (1, 6),
        "desc": "中盤で白がg2（Xマス）に着手した局面。右上の隅h1を即座に取られてしまう危険な手筋。",
    },
    "q3": {
        "name": "🎯 質問局面 3（序中盤残り17マス: 白b3好手）",
        "board": PRESET_USER_Q3,
        "turn": WHITE,
        "sim_move": (2, 1),
        "desc": "序中盤で白がb3に着手した局面。AIの推奨最善手と完全に一致した好手。",
    },
    "yohane": {
        "name": "⚔️ Yohane戦 42手目（中盤の辺の攻防: 白a5 vs a3）",
        "board": PRESET_YOHANE_P42,
        "turn": WHITE,
        "sim_move": (4, 0),
        "desc": "今日のYohane(黒)戦 42手目の局面（白10個・黒35個、残り19マス）。白a5に着手した実戦手と、最善のa3やg7との違いをシミュレーション・比較できます。",
    },
    "nakawari": {
        "name": "♟️ 典型手筋: 中割り（内側の石だけを返す）",
        "board": PRESET_NAKAWARI,
        "turn": WHITE,
        "sim_move": (2, 3),
        "desc": "相手の周囲に空きマスを作らず、自分の石を内側に潜り込ませる中割り（開放度0〜1）の典型局面。",
    },
    "stoner": {
        "name": "♟️ 典型手筋: ストナーの罠（対角線通し）",
        "board": PRESET_STONER,
        "turn": WHITE,
        "sim_move": (1, 1),
        "desc": "相手の辺の欠陥（Cマス打ち）に対して対角線を通し、付け手で確定石を奪うストナーの罠の局面。",
    },
    "initial": {
        "name": "🏁 初期配置（8x8 標準スタート）",
        "board": create_initial_board(),
        "turn": BLACK,
        "sim_move": (2, 3),
        "desc": "標準の初期配置。黒の初手（d3, c4, f5, e6）から自由に検討を開始できます。",
    },
}


def load_sandbox_preset(preset_key: str) -> None:
    """指定されたプリセットの盤面・手番・推奨着手をセッション状態に即時反映する"""
    if preset_key in SANDBOX_PRESETS:
        p_data = SANDBOX_PRESETS[preset_key]
        st.session_state.sandbox_board = [row[:] for row in p_data["board"]]
        st.session_state.sandbox_turn = p_data["turn"]
        st.session_state.sandbox_sim_move = p_data["sim_move"]
        st.session_state.sandbox_history = []
        st.session_state.sandbox_ai_coach_text = ""
        st.session_state.sandbox_preset_name = p_data["name"]
        st.session_state._last_loaded_preset_key = preset_key
        st.session_state.pop("radio_sandbox_sim", None)


COLOR_RADIO_KEY = "user_color_radio"
COLOR_LABELS = {BLACK: "黒 (●・先手)", WHITE: "白 (○・後手)"}


def _sync_color_radio(user_color: int) -> None:
    """サイドバーの手番ラジオボタンの表示を現在の手番に合わせる（ウィジェット生成前のコールバック内でのみ書き換え）"""
    label = COLOR_LABELS[user_color]
    if st.session_state.get(COLOR_RADIO_KEY) != label:
        st.session_state[COLOR_RADIO_KEY] = label


def _reset_game_state(user_color: int) -> OthelloGame:
    """対局・解析関連のセッション状態をまとめて初期化する"""
    game = OthelloGame()
    st.session_state.game = game
    st.session_state.user_color = user_color
    st.session_state.analysis_done = False
    st.session_state.analyses = []
    st.session_state.coach_summary = ""
    st.session_state.used_gemini = False
    st.session_state.saved_record_id = None
    st.session_state.postgame_job_id = None  # 走り途中の解析は続くが（記録は保存される）、この対局の画面には反映しない
    st.session_state.last_animated_ply = 0
    st.session_state.turn_notices = []
    st.session_state.dilemmas = []
    st.session_state.dilemma_analyses = []
    st.session_state.game_id = st.session_state.get("game_id", 1) + 1
    return game


def init_session_state() -> None:
    """Streamlit セッション状態の初期化"""
    if "db_initialized" not in st.session_state:
        init_db()
        st.session_state.db_initialized = True
    if "game" not in st.session_state:
        st.session_state.game = OthelloGame()
    if "user_color" not in st.session_state:
        st.session_state.user_color = BLACK
    if "analysis_done" not in st.session_state:
        st.session_state.analysis_done = False
    if "analyses" not in st.session_state:
        st.session_state.analyses = []
    if "coach_summary" not in st.session_state:
        st.session_state.coach_summary = ""
    if "used_gemini" not in st.session_state:
        st.session_state.used_gemini = False
    if "saved_record_id" not in st.session_state:
        st.session_state.saved_record_id = None
    if "last_animated_ply" not in st.session_state:
        st.session_state.last_animated_ply = 0
    if "game_id" not in st.session_state:
        st.session_state.game_id = 1
    if "turn_notices" not in st.session_state:
        st.session_state.turn_notices = []
    if "dilemmas" not in st.session_state:
        st.session_state.dilemmas = []
    if "dilemma_analyses" not in st.session_state:
        st.session_state.dilemma_analyses = []
    if COLOR_RADIO_KEY not in st.session_state:
        st.session_state[COLOR_RADIO_KEY] = COLOR_LABELS[st.session_state.user_color]
    if "sandbox_board" not in st.session_state:
        st.session_state.sandbox_board = [row[:] for row in PRESET_USER_Q1]
    if "sandbox_turn" not in st.session_state:
        st.session_state.sandbox_turn = WHITE
    if "sandbox_history" not in st.session_state:
        st.session_state.sandbox_history = []
    if "sandbox_sim_move" not in st.session_state:
        st.session_state.sandbox_sim_move = (6, 6)
    if "sandbox_preset_name" not in st.session_state:
        st.session_state.sandbox_preset_name = "🎯 質問局面 1（終盤残り5マス: 白g7悪手 vs g8最善）"
    if "sandbox_ai_coach_text" not in st.session_state:
        st.session_state.sandbox_ai_coach_text = ""
    if "sandbox_edit_mode" not in st.session_state:
        st.session_state.sandbox_edit_mode = False
    if "_last_loaded_preset_key" not in st.session_state:
        st.session_state._last_loaded_preset_key = "q1"
    if "match_mode" not in st.session_state:
        st.session_state.match_mode = "ai"  # "ai" or "pvp"
    if "pvp_p1_name" not in st.session_state:
        st.session_state.pvp_p1_name = owner_username() or "あなた"
    if "pvp_p2_name" not in st.session_state:
        st.session_state.pvp_p2_name = "対戦相手"
    if "pvp_user_color" not in st.session_state:
        st.session_state.pvp_user_color = BLACK  # 1台の対人戦で、記録をつける本人（あなた）の色
    if "pvp_selected_id" not in st.session_state:
        st.session_state.pvp_selected_id = None
    if "pvp_review_ply" not in st.session_state:
        st.session_state.pvp_review_ply = 60
    if "pvp_coach_text" not in st.session_state:
        st.session_state.pvp_coach_text = ""
    if "diff_auto" not in st.session_state:
        st.session_state.diff_auto = True  # 手動で難易度を選ぶまでは、レーティングに合わせて自動で選ぶ
    if "ai_difficulty_preset" not in st.session_state:
        st.session_state.ai_difficulty_preset = recommended_difficulty() or "🐣 入門 (超初級・ひよこ)"
    if "sidebar_diff_preset" not in st.session_state:
        st.session_state.sidebar_diff_preset = st.session_state.ai_difficulty_preset
    if "tab1_diff_preset_select" not in st.session_state:
        st.session_state.tab1_diff_preset_select = st.session_state.ai_difficulty_preset
    if "online_room_id" not in st.session_state:
        st.session_state.online_room_id = ""
    if "online_player_role" not in st.session_state:
        st.session_state.online_player_role = "host"
    if "online_player_name" not in st.session_state:
        st.session_state.online_player_name = owner_username() or "あなた"


# レーティングで自動選択する難易度（カスタムは除く）。各AIの強さは rating.estimate_opponent_rating（対局の評価と同じ基準）
AUTO_DIFFICULTY_PRESETS = [
    "🐣 入門 (超初級・ひよこ)",
    "🌱 初級 (ルーキー)",
    "🌿 初中級 (アマ級位)",
    "⚔️ 中級 (アマ初段)",
    "🥋 上級 (Egaroucid Lv.2)",
    "🔥 強豪 (Egaroucid Lv.3)",
    "🌟 有段 (Egaroucid Lv.4)",
    "👑 達人 (Egaroucid Lv.5)",
]
# 自分より少し強い相手を選ぶ（勝ったり負けたりしながら伸びる強さ）
DIFFICULTY_CHALLENGE_MARGIN = 50


def current_user_rating() -> Optional[float]:
    """成長タブと同じ計算での最新レーティング（対局がまだ無ければ None）"""
    records = list_match_records(limit=200)
    if not records:
        return None
    return float(compute_sequential_ratings(list(reversed(records)))[-1].get("rating_after", 1200))


def recommended_difficulty(rating: Optional[float] = None) -> Optional[str]:
    """レーティング＋少しの強さに一番近いAIの難易度"""
    if rating is None:
        rating = current_user_rating()
    if rating is None:
        return None
    target = rating + DIFFICULTY_CHALLENGE_MARGIN
    return min(AUTO_DIFFICULTY_PRESETS, key=lambda p: abs(estimate_opponent_rating(p) - target))


def difficulty_goal(rating: float) -> Optional[Dict[str, Any]]:
    """
    いまの相手（おすすめ難易度）の1つ上と、そこへ自動で上がるレート。
    おすすめは「レート＋余裕」に一番近いAIなので、隣り合う2つのAIの強さの真ん中が切り替わり目になる。
    返り値: {"current", "next", "from", "to"}（from〜to が今の段階の幅。最上位なら None）
    """
    cur = recommended_difficulty(rating)
    i = AUTO_DIFFICULTY_PRESETS.index(cur)
    if i + 1 >= len(AUTO_DIFFICULTY_PRESETS):
        return None
    strength = [estimate_opponent_rating(p) for p in AUTO_DIFFICULTY_PRESETS]
    to = (strength[i] + strength[i + 1]) / 2 - DIFFICULTY_CHALLENGE_MARGIN
    frm = (strength[i - 1] + strength[i]) / 2 - DIFFICULTY_CHALLENGE_MARGIN if i > 0 else min(rating, to - 200)
    return {"current": cur, "next": AUTO_DIFFICULTY_PRESETS[i + 1], "from": frm, "to": to}


def _set_difficulty_preset(preset: str) -> None:
    """難易度のセレクトボックス（サイドバー・対局タブ）をそろえて切り替える（コールバックの中で呼ぶ）"""
    st.session_state.ai_difficulty_preset = preset
    st.session_state.sidebar_diff_preset = preset
    st.session_state.tab1_diff_preset_select = preset


def on_diff_auto_restore(engine_holder: Dict[str, OthelloEngine]) -> None:
    """「おすすめに戻す」: 自動選択に戻して新しい対局を始める"""
    st.session_state.diff_auto = True
    start_new_game(st.session_state.user_color, engine_holder.get("engine") or OthelloEngine())


def _create_engine_from_preset(preset: str, cli_path: str = DEFAULT_EGAROUCID_PATH) -> OthelloEngine:
    if preset == "🐣 入門 (超初級・ひよこ)":
        return OthelloEngine(mode="builtin", level=0, cli_path=cli_path)
    elif preset == "🌱 初級 (ルーキー)":
        return OthelloEngine(mode="builtin", level=1, cli_path=cli_path)
    elif preset == "🌿 初中級 (アマ級位)":
        return OthelloEngine(mode="builtin", level=2, cli_path=cli_path)
    elif preset == "⚔️ 中級 (アマ初段)":
        return OthelloEngine(mode="builtin", level=3, cli_path=cli_path)
    elif preset == "🥋 上級 (Egaroucid Lv.2)":
        return OthelloEngine(mode="cli", level=2, cli_path=cli_path)
    elif preset == "🔥 強豪 (Egaroucid Lv.3)":
        return OthelloEngine(mode="cli", level=3, cli_path=cli_path)
    elif preset == "🌟 有段 (Egaroucid Lv.4)":
        return OthelloEngine(mode="cli", level=4, cli_path=cli_path)
    elif preset == "👑 達人 (Egaroucid Lv.5)":
        return OthelloEngine(mode="cli", level=5, cli_path=cli_path)
    elif preset == "⚙️ カスタム (手動Lv指定)":
        c_mode = st.session_state.get("custom_engine_mode", "cli")
        c_lv = int(st.session_state.get("custom_engine_level", 5))
        c_path = st.session_state.get("custom_engine_path", cli_path)
        return OthelloEngine(mode=c_mode, level=c_lv, cli_path=c_path)
    return OthelloEngine(mode="builtin", level=0, cli_path=cli_path)


def _server_port() -> int:
    """Wi-Fi対戦の接続URLに載せる、実際に起動している Streamlit のポート番号（既定 8501）"""
    try:
        return int(st.get_option("server.port"))
    except (TypeError, ValueError):
        return 8501


def get_analysis_engine(play_engine: OthelloEngine) -> OthelloEngine:
    """
    終局後解析・局面検討で使うエンジンを返す。
    対戦相手を入門AIなどに設定していても、評価値・最善手・悪手判定は常に強いエンジンで行う
    （弱い対戦用AIのまま解析すると、ランダム着手や浅い読みで悪手判定が不正確になるため）。
    """
    if play_engine.is_cli_available and play_engine.level >= 5:
        return play_engine
    strong = OthelloEngine(mode="cli", level=5, cli_path=play_engine.cli_path)
    if strong.is_cli_available:
        return strong
    return OthelloEngine(mode="builtin", level=3)


def on_sidebar_diff_change(engine_holder: Dict[str, OthelloEngine]) -> None:
    st.session_state.diff_auto = False  # 手動で選んだら、このセッションでは自動選択しない
    new_diff = st.session_state.sidebar_diff_preset
    st.session_state.ai_difficulty_preset = new_diff
    st.session_state.tab1_diff_preset_select = new_diff
    eng = _create_engine_from_preset(new_diff)
    engine_holder["engine"] = eng
    start_new_game(st.session_state.user_color, eng)


def on_tab1_diff_change(engine_holder: Dict[str, OthelloEngine]) -> None:
    st.session_state.diff_auto = False  # 手動で選んだら、このセッションでは自動選択しない
    new_diff = st.session_state.tab1_diff_preset_select
    st.session_state.ai_difficulty_preset = new_diff
    st.session_state.sidebar_diff_preset = new_diff
    eng = _create_engine_from_preset(new_diff)
    engine_holder["engine"] = eng
    start_new_game(st.session_state.user_color, eng)


def start_new_game(user_color: int, engine: OthelloEngine) -> None:
    """
    新規対局を開始し、AI対戦かつユーザーが白（後手）の場合はAIの初手を自動で打つ。
    難易度が自動選択のときは、最新のレーティングに合わせて相手を選び直す（前の対局でレートが変わっているため）。
    """
    if st.session_state.get("diff_auto", True) and st.session_state.get("match_mode", "ai") == "ai":
        rec = recommended_difficulty()
        if rec and rec != st.session_state.get("ai_difficulty_preset"):
            _set_difficulty_preset(rec)
            engine = _create_engine_from_preset(rec)
    _reset_game_state(user_color)
    _sync_color_radio(user_color)
    if st.session_state.get("match_mode", "ai") == "ai" and user_color == WHITE:
        advance_ai_turns(engine)


def on_match_mode_change(engine_holder: Dict[str, OthelloEngine]) -> None:
    """対戦モード（AI対戦 / 1台対人 / Wi-Fi対戦）を切り替えた瞬間に新規対局を開始するコールバック"""
    chosen = st.session_state.get("match_mode_radio_tab1", "ai")
    if chosen != st.session_state.get("match_mode", "ai"):
        st.session_state.match_mode = chosen
        if chosen != "online":
            start_new_game(BLACK, engine_holder.get("engine") or OthelloEngine())


def on_color_radio_change(engine_holder: Dict[str, OthelloEngine]) -> None:
    """サイドバーで先手(黒)・後手(白)を切り替えた瞬間に新規対局を開始するコールバック"""
    chosen = BLACK if "黒" in str(st.session_state.get(COLOR_RADIO_KEY, "")) else WHITE
    if chosen != st.session_state.user_color:
        start_new_game(chosen, engine_holder.get("engine") or OthelloEngine())


def _make_move_and_collect_notices(game: OthelloGame, r: int, c: int) -> bool:
    """着手し、発生したパス通知を今回のターン分として蓄積する（AIの連続着手で通知が消えないようにする）"""
    ok = game.make_move(r, c)
    if ok and game.pass_notices:
        st.session_state.turn_notices.extend(game.pass_notices)
    return ok


def advance_ai_turns(engine: OthelloEngine) -> None:
    """AIの手番である限り（ユーザーのパスによるAI連続手番を含む）AIが自動で着手する"""
    game: OthelloGame = st.session_state.game
    user_color: int = st.session_state.user_color
    safety_counter = 0
    while not game.game_over and game.current_turn != user_color and safety_counter < 64:
        safety_counter += 1
        ai_move = engine.choose_move(game.board, game.current_turn)
        if ai_move is None or not _make_move_and_collect_notices(game, ai_move[0], ai_move[1]):
            break


def handle_user_and_ai_turn(r: int, c: int, engine: OthelloEngine) -> bool:
    """
    着手を処理する。
    - AI対戦モード: ユーザー着手後にAIの応手を1回のパスでまとめて処理
    - 対人戦モード: 現在手番のプレイヤーの着手を処理し、手番を交代（AIは動作しない）
    戻り値: 着手が実際に盤面へ反映されたかどうか
    """
    game: OthelloGame = st.session_state.game
    match_mode = st.session_state.get("match_mode", "ai")

    if game.game_over:
        return False
    if match_mode == "ai" and game.current_turn != st.session_state.user_color:
        return False
    # 非合法手（処理済みクリックの再送など）では前回のパス通知を消さない
    if (r, c) not in get_legal_moves(game.board, game.current_turn):
        return False
    st.session_state.turn_notices = []

    # ユーザーが迷い手としてマークした候補手を保存
    current_ply = len(game.history)
    hesitated_raw = st.session_state.get(f"hesitated_pills_{current_ply}", [])
    actual_str = coord_to_str(r, c)
    candidates = list(hesitated_raw)
    if candidates:
        if actual_str not in candidates:
            candidates.insert(0, actual_str)
        alternatives = [m for m in candidates if m != actual_str]
        if alternatives:
            if "dilemmas" not in st.session_state:
                st.session_state.dilemmas = []
            st.session_state.dilemmas.append(
                {
                    "ply": current_ply + 1,
                    "player": game.current_turn,
                    "board_before": [row[:] for row in game.board],
                    "actual_move": (r, c),
                    "actual_move_str": actual_str,
                    "hesitated_moves": alternatives,
                    "all_candidates": candidates,
                }
            )

    if not _make_move_and_collect_notices(game, r, c):
        return False

    # AI対戦モードの場合のみAIの応手を自動実行
    if match_mode == "ai":
        advance_ai_turns(engine)

    return True


def handle_board_click_callback(engine: OthelloEngine) -> None:
    """SVG盤面クリック時のコールバック: 画面描画前にユーザー着手＋AI応手を完了させる"""
    comp_state = st.session_state.get("main_othello_board")
    clicked_val = comp_state.get("clicked") if isinstance(comp_state, dict) else getattr(comp_state, "clicked", None)
    if clicked_val:
        coord = str_to_coord(str(clicked_val))
        if coord is not None:
            handle_user_and_ai_turn(coord[0], coord[1], engine)


def resign_and_analyze() -> None:
    """途中局面で終局扱いにして解析へ進むコールバック"""
    st.session_state.game.game_over = True


def load_demo_sample_game(engine: OthelloEngine) -> None:
    """
    ワンクリックで終局後解析パイプライン（悪手抽出・特徴量算出・Gemini解説・履歴保存）を
    テストできるよう、典型的な開放度ミスやX打ちを含む実戦デモ棋譜を再生して終局状態にする。
    """
    game = _reset_game_state(BLACK)
    _sync_color_radio(BLACK)

    # 序盤〜中盤の典型的な手順（黒が途中でX打ち g2 や高開放度の手を打ち隅 h1 を奪われる実戦サンプル）
    scripted_moves = [
        "f5", "d6", "c3", "d3", "c4", "f4", "f6", "f3",
        "e6", "e7", "g2", "e3", "g4", "g6", "g5", "h1",
    ]
    for mv_str in scripted_moves:
        if game.game_over:
            break
        coord = str_to_coord(mv_str)
        legal = get_legal_moves(game.board, game.current_turn)
        if coord and coord in legal:
            game.make_move(coord[0], coord[1])
        else:
            fallback_mv = engine.choose_move(game.board, game.current_turn)
            if fallback_mv:
                game.make_move(fallback_mv[0], fallback_mv[1])

    # 終局または解析に十分な手数まで進行し、終局フラグを立てて即時解析可能にする
    game.game_over = True
    st.session_state.last_animated_ply = len(game.history)

    # デモ用の迷い手データを1件注入（11手目: ユーザーが危険なX打ちg2と好手中割りe3で迷った局面）
    if len(game.history) >= 11:
        rec11 = game.history[10]
        st.session_state.dilemmas = [
            {
                "ply": 11,
                "player": BLACK,
                "board_before": [row[:] for row in rec11.board_before],
                "actual_move": rec11.move,
                "actual_move_str": rec11.move_str,
                "hesitated_moves": ["e3"],
                "all_candidates": ["g2", "e3"],
            }
        ]


def _postgame_compute(
    report,
    history: List[Any],
    board: List[List[int]],
    user_color: int,
    analyzer: OthelloEngine,
    blunder_threshold: float,
    api_key: str,
    raw_dilemmas: List[Any],
    saved_engine_name: str,
    record_id: Optional[int],
    kifu_str: str,
) -> Dict[str, Any]:
    """
    終局後の解析本体。st.* には触らないので、別スレッド（bg_jobs）からも呼べる。
    1. 全着手の評価値（最終石差）と最善手を算出
    2. 悪手（Blunder）を抽出し、各悪手の解説を生成（Gemini または内蔵ルール）
    3. 迷い手の判定・対局全体の総括を生成
    4. SQLite に保存し、latest_game_review.md / player_growth_log.md を更新
    """
    report("全手の評価値（最終石差）を計算中")
    analyses = analyze_game_history(
        history=history,
        user_color=user_color,
        engine=analyzer,
        blunder_threshold=blunder_threshold,
    )
    blunders = [a for a in analyses if a.is_blunder]
    any_gemini_used = False

    # 各悪手について戦術解説を生成
    for i, b in enumerate(blunders, start=1):
        report(f"悪手の解説を作成中（{i} / {len(blunders)}）")
        comment, used_api = generate_blunder_explanation(b, api_key=api_key)
        b.coach_comment = comment
        if used_api:
            any_gemini_used = True

    # 迷い手（選択肢の比較）の解析
    report("迷い手を判定中")
    dilemma_analyses = analyze_all_dilemmas(
        dilemmas_list=raw_dilemmas,
        user_color=user_color,
        engine=analyzer,
        api_key=api_key,
    )
    dilemmas_json = json.dumps([d.to_dict() for d in dilemma_analyses], ensure_ascii=False)

    # 対局全体を通した弱点の癖・改善アドバイスを生成
    report("対局全体の総括を作成中")
    b_score, w_score, _ = count_stones(board)
    # 「終局扱い」ボタンやデモ棋譜で打ち切った対局は、盤面上はまだ打てる手が残っている
    interrupted = not is_game_over(board)
    summary, used_api_sum = generate_overall_coaching_summary(
        analyses=analyses,
        blunders=blunders,
        user_color=user_color,
        black_score=b_score,
        white_score=w_score,
        kifu_str=kifu_str,
        api_key=api_key,
        dilemmas=dilemma_analyses,
    )
    if used_api_sum:
        any_gemini_used = True

    # SQLiteへ保存（棋力成長指標・弱点タグも自動算出して記録）。再解析時は同じレコードを上書きする
    report("保存中")
    rec_id = save_match_record(
        user_color=user_color,
        engine_name=saved_engine_name,
        black_score=b_score,
        white_score=w_score,
        kifu=kifu_str,
        analyses=analyses,
        coach_summary=summary,
        dilemmas_json=dilemmas_json,
        record_id=record_id,
        interrupted=interrupted,
    )

    # APIキーの有無にかかわらず latest_game_review.md と長期カルテ player_growth_log.md を自動更新
    review_md_path = export_latest_game_review_md(
        analyses=analyses,
        blunders=blunders,
        user_color=user_color,
        engine_name=saved_engine_name,
        black_score=b_score,
        white_score=w_score,
        kifu_str=kifu_str,
        blunder_threshold=blunder_threshold,
        record_id=rec_id,
        interrupted=interrupted,
        dilemmas=dilemma_analyses,
    )
    export_player_growth_md(list_match_records())

    return {
        "analyses": analyses,
        "summary": summary,
        "used_gemini": any_gemini_used,
        "dilemma_analyses": dilemma_analyses,
        "record_id": rec_id,
        "review_md_path": review_md_path,
        "plies": len(kifu_str) // 2,
    }


def _swap_pvp_names() -> None:
    """1台の対人戦の黒・白の名前を入れ替える（入力欄の値も。残っていると描画時に元の名前へ戻ってしまうため）"""
    st.session_state.pvp_p1_name, st.session_state.pvp_p2_name = (
        st.session_state.get("pvp_p2_name", "対戦相手"),
        st.session_state.get("pvp_p1_name", "あなた"),
    )
    st.session_state.input_pvp_p1_field = st.session_state.pvp_p1_name
    st.session_state.input_pvp_p2_field = st.session_state.pvp_p2_name


def _on_pvp_user_color_change() -> None:
    """「あなたはどっち？」を変えたら、自分の名前も選んだ色の側へ移す"""
    new = st.session_state.pvp_user_color_radio
    if new != st.session_state.get("pvp_user_color", BLACK):
        st.session_state.pvp_user_color = new
        _swap_pvp_names()


def _postgame_inputs(
    engine: OthelloEngine,
    blunder_threshold: float,
    api_key: str,
    analysis_engine: Optional[OthelloEngine],
) -> Dict[str, Any]:
    """いまの対局から解析に必要なものを写し取る（スレッドが画面の状態に触らずに済むように）"""
    game: OthelloGame = st.session_state.game
    match_mode = st.session_state.get("match_mode", "ai")
    p1 = st.session_state.get("pvp_p1_name", "あなた")
    p2 = st.session_state.get("pvp_p2_name", "対戦相手")
    return {
        "history": list(game.history),
        "board": [row[:] for row in game.board],
        "user_color": st.session_state.get("pvp_user_color", BLACK) if match_mode == "pvp" else st.session_state.user_color,
        # engine は対戦相手（保存時のエンジン名に使う）、解析は強いエンジンで行う
        "analyzer": analysis_engine or get_analysis_engine(engine),
        "blunder_threshold": blunder_threshold,
        "api_key": api_key,
        "raw_dilemmas": list(st.session_state.get("dilemmas", [])),
        "saved_engine_name": f"対人戦 ({p1} vs {p2})" if match_mode == "pvp" else engine.name,
        "record_id": st.session_state.saved_record_id,
        "kifu_str": game.get_kifu_string(),
    }


def _apply_postgame_result(res: Dict[str, Any]) -> None:
    st.session_state.analyses = res["analyses"]
    st.session_state.coach_summary = res["summary"]
    st.session_state.used_gemini = res["used_gemini"]
    st.session_state.dilemma_analyses = res["dilemma_analyses"]
    st.session_state.saved_record_id = res["record_id"]
    st.session_state.review_md_path = res["review_md_path"]
    st.session_state.analysis_done = True


def run_postgame_pipeline(
    engine: OthelloEngine,
    blunder_threshold: float,
    api_key: str,
    analysis_engine: Optional[OthelloEngine] = None,
) -> None:
    """終局後の解析をその場で（画面の描画中に）実行する。自動解析は ensure_postgame_job を使う"""
    if not st.session_state.game.history:
        return
    with st.spinner("🔍 エンジンによる全手評価値（最終石差）算出と、AIコーチによる弱点解析を実行中..."):
        res = _postgame_compute(report=lambda _msg: None, **_postgame_inputs(engine, blunder_threshold, api_key, analysis_engine))
    _apply_postgame_result(res)


def ensure_postgame_job(
    engine: OthelloEngine,
    blunder_threshold: float,
    api_key: str,
    analysis_engine: Optional[OthelloEngine] = None,
) -> str:
    """
    終局後の解析を別スレッドで始める（すでに走っていれば何もしない）。
    画面の描画中に解析すると、スマホの通信切れや再実行で途中で止まり、再読み込みが必要になるため。
    """
    job_id = st.session_state.get("postgame_job_id")
    if not job_id or bg_jobs.get_job(job_id) is None:
        job_id = f"postgame-{secrets.token_hex(8)}"
        st.session_state.postgame_job_id = job_id
        bg_jobs.start_job(job_id, _postgame_compute, **_postgame_inputs(engine, blunder_threshold, api_key, analysis_engine))
    return job_id


def _job_progress_html(job: Dict[str, Any], what: str) -> str:
    elapsed = int(time.time() - job["started"])
    step = job.get("progress") or "準備中"
    return (
        '<div style="background:#eef2ff; border:1px solid #c7d2fe; border-radius:12px; padding:12px 16px; margin:6px 0 10px 0;">'
        f'<div style="font-weight:800; color:#3730a3;">⏳ {what}（{elapsed}秒）</div>'
        f'<div style="font-size:13px; color:#4338ca; margin-top:2px;">いま: {step}</div>'
        '<div style="font-size:12px; color:#64748b; margin-top:4px;">終わると自動で振り返りが開きます。再読み込みは不要です。</div>'
        '</div>'
    )


@st.fragment(run_every=1.5)
def render_postgame_progress(job_id: str) -> None:
    """解析ジョブの進み具合を数秒おきに見に行き、終わったら結果を反映して棋譜再生タブを開く"""
    job = bg_jobs.get_job(job_id)
    if job is None:
        st.session_state.postgame_job_id = None
        st.rerun(scope="app")
        return
    if job["status"] == "running":
        st.markdown(_job_progress_html(job, "対局を解析中です"), unsafe_allow_html=True)
        return
    if job["status"] == "error":
        st.error(f"解析中にエラーが起きました: {job['error']}")
        if st.button("🔁 もう一度解析する", key=f"btn_retry_{job_id}"):
            bg_jobs.forget_job(job_id)
            st.session_state.postgame_job_id = None
            st.rerun(scope="app")
        return
    res = job["result"]
    bg_jobs.forget_job(job_id)
    st.session_state.postgame_job_id = None
    _apply_postgame_result(res)
    request_review_jump(res["record_id"], res["plies"])
    st.rerun(scope="app")


def _online_postgame_compute(
    report,
    history: List[Any],
    user_color: int,
    analyzer: OthelloEngine,
    blunder_threshold: float,
    api_key: str,
    black_score: int,
    white_score: int,
    kifu_str: str,
    engine_name: str,
    dilemmas_json: str,
    interrupted: bool,
) -> Dict[str, Any]:
    """Wi-Fi対戦の終局後解析（別スレッドで動く。st.* には触らない）"""
    report("全手の評価値（最終石差）を計算中")
    analyses = analyze_game_history(
        history=history,
        user_color=user_color,
        engine=analyzer,
        blunder_threshold=blunder_threshold,
    )
    blunders = [a for a in analyses if a.is_blunder]
    report("対局全体の総括を作成中")
    coach_text, _ = generate_overall_coaching_summary(
        analyses=analyses,
        blunders=blunders,
        user_color=user_color,
        black_score=black_score,
        white_score=white_score,
        kifu_str=kifu_str,
        api_key=api_key,
    )
    report("保存中")
    saved_id = save_match_record(
        user_color=user_color,
        engine_name=engine_name,
        black_score=black_score,
        white_score=white_score,
        kifu=kifu_str,
        analyses=analyses,
        coach_summary=coach_text,
        dilemmas_json=dilemmas_json,
        interrupted=interrupted,
    )
    return {"analyses": analyses, "summary": coach_text, "record_id": saved_id, "plies": len(kifu_str) // 2}


@dataclass
class MoveBranchVariation:
    move: Tuple[int, int]
    move_str: str
    player: int
    board_after: List[List[int]]
    flipped: List[Tuple[int, int]]
    eval_user: float
    openness: int
    opp_mobility_count: int
    opp_reply: Optional[Tuple[int, int]]
    opp_reply_str: str
    is_nakawari: bool
    is_danger: bool
    danger_label: str


def compute_move_branch(
    board_before: List[List[int]],
    move: Optional[Tuple[int, int]],
    player: int,
    user_color: int,
    engine: Optional[OthelloEngine] = None,
    known_eval_user: Optional[float] = None,
) -> Optional[MoveBranchVariation]:
    if move is None:
        return None
    board_after, flipped = apply_move(board_before, move[0], move[1], player)
    opp = -player
    opp_legal = get_legal_moves(board_after, opp)
    opp_reply = engine.choose_move(board_after, opp) if (engine and opp_legal) else None
    opp_reply_str = (
        coord_to_str(*opp_reply)
        if opp_reply
        else ("パス" if not is_game_over(board_after) else "終局")
    )

    if known_eval_user is not None:
        eval_user = known_eval_user
    elif engine:
        opp_eval = engine.evaluate_position(board_after, opp)
        eval_user = opp_eval.score_for_black if user_color == BLACK else -opp_eval.score_for_black
    else:
        eval_user = 0.0

    f = extract_move_features(board_before, player, move)
    openness = f.openness_total if f else 0
    opp_mobility_count = len(opp_legal)
    is_nakawari = f.is_nakawari if f else False
    is_danger = (f.is_x_move or f.is_c_move) if f else False
    danger_label = "X打ち" if (f and f.is_x_move) else ("C打ち" if (f and f.is_c_move) else "安全マス")

    return MoveBranchVariation(
        move=move,
        move_str=coord_to_str(*move),
        player=player,
        board_after=board_after,
        flipped=flipped,
        eval_user=round(eval_user, 1),
        openness=openness,
        opp_mobility_count=opp_mobility_count,
        opp_reply=opp_reply,
        opp_reply_str=opp_reply_str,
        is_nakawari=is_nakawari,
        is_danger=is_danger,
        danger_label=danger_label,
    )


def render_dilemma_card(
    d: DilemmaAnalysis,
    idx: int,
    engine: Optional[OthelloEngine] = None,
    user_color: int = BLACK,
) -> None:
    """迷った手1件の比較・正解判定カードを描画（局面分岐ビュー対応）"""
    if d.verdict == "CORRECT":
        verdict_badge = "🌟 大正解！"
    elif d.verdict == "BETTER_ALT":
        verdict_badge = "💡 迷った手が好手！"
    elif d.verdict == "THIRD_BEST":
        verdict_badge = "⚠️ 第三の手が最善！"
    else:
        verdict_badge = "🤝 互角の選択！"

    alts_str = ", ".join(a.move_str for a in d.alternative_evals)
    header_title = (
        f"⚖️ 迷い手 #{idx}: 第{d.ply}手目 実戦 `{d.actual_move_str}` vs 迷い `{alts_str}` 【{verdict_badge}】"
    )

    actual_branch = compute_move_branch(
        board_before=d.board_before,
        move=d.actual_move,
        player=d.player,
        user_color=user_color,
        engine=engine,
        known_eval_user=d.actual_eval.eval_user,
    )
    alt_branches = [
        compute_move_branch(
            board_before=d.board_before,
            move=a.move,
            player=d.player,
            user_color=user_color,
            engine=engine,
            known_eval_user=a.eval_user,
        )
        for a in d.alternative_evals
    ]

    with st.expander(header_title, expanded=(idx <= 3)):
        col_board, col_detail = st.columns([1, 1.55])
        with col_board:
            tab_labels = ["📌 着手前"]
            if actual_branch:
                tab_labels.append(f"🔴 実戦 ({d.actual_move_str})")
            for ab in alt_branches:
                if ab:
                    tab_labels.append(f"🟠 迷い ({ab.move_str})")

            d_tabs = st.tabs(tab_labels)
            with d_tabs[0]:
                st.caption("着手前の局面（緑＝正解 / 赤＝実戦 / 橙＝迷った手 / 青＝AI最善）")
                board_html = render_html_board(
                    board=d.board_before,
                    actual_move=d.actual_move,
                    best_move=d.best_move,
                    candidate_moves=[a.move for a in d.alternative_evals],
                    cell_size=34,
                )
                st.markdown(board_html, unsafe_allow_html=True)
                st.caption(f"着手前形勢: **{d.best_eval_user:+.1f} 石**（最善手基準）")

            t_idx = 1
            if actual_branch and t_idx < len(d_tabs):
                with d_tabs[t_idx]:
                    st.caption(
                        f"🔴 あなたが選んだ `{d.actual_move_str}` 直後（金輪＝裏返った石 ／ 橙＝相手の次の一手「{actual_branch.opp_reply_str}」）"
                    )
                    b_html = render_html_board(
                        board=actual_branch.board_after,
                        last_move=d.actual_move,
                        last_move_color="#ef4444",
                        flipped=actual_branch.flipped,
                        reply_move=actual_branch.opp_reply,
                        reply_label="相手",
                        cell_size=34,
                    )
                    st.markdown(b_html, unsafe_allow_html=True)
                    st.caption(
                        f"評価値: **{actual_branch.eval_user:+.1f} 石** ｜ 相手手数: **{actual_branch.opp_mobility_count} 箇所**"
                    )
                t_idx += 1

            for ab in alt_branches:
                if ab and t_idx < len(d_tabs):
                    with d_tabs[t_idx]:
                        st.caption(
                            f"🟠 迷った候補手 `{ab.move_str}` 直後（金輪＝裏返った石 ／ 橙＝相手の次の一手「{ab.opp_reply_str}」）"
                        )
                        b_html = render_html_board(
                            board=ab.board_after,
                            last_move=ab.move,
                            last_move_color="#d97706",
                            flipped=ab.flipped,
                            reply_move=ab.opp_reply,
                            reply_label="相手",
                            cell_size=34,
                        )
                        st.markdown(b_html, unsafe_allow_html=True)
                        st.caption(
                            f"評価値: **{ab.eval_user:+.1f} 石** ｜ 相手手数: **{ab.opp_mobility_count} 箇所**"
                        )
                    t_idx += 1

        with col_detail:
            # 判定バナー
            if d.verdict == "CORRECT":
                st.success(f"**{d.verdict_title}**\n\n{d.summary_text}")
            elif d.verdict == "BETTER_ALT":
                st.warning(f"**{d.verdict_title}**\n\n{d.summary_text}")
            elif d.verdict == "THIRD_BEST":
                st.error(f"**{d.verdict_title}**\n\n{d.summary_text}")
            else:
                st.info(f"**{d.verdict_title}**\n\n{d.summary_text}")

            st.markdown("##### 📊 候補手どうしの戦術比較")
            table_rows = []
            table_rows.append(
                f"| 実戦手 | `{d.actual_eval.move_str}` | **{d.actual_eval.eval_user:+.1f} 石** | {d.actual_eval.openness} | {d.actual_eval.opp_mobility} 箇所 | {'⭕' if d.actual_eval.is_nakawari else '❌'} | {d.actual_eval.danger_label} |"
            )
            for a in d.alternative_evals:
                table_rows.append(
                    f"| 迷った手 | `{a.move_str}` | **{a.eval_user:+.1f} 石** | {a.openness} | {a.opp_mobility} 箇所 | {'⭕' if a.is_nakawari else '❌'} | {a.danger_label} |"
                )
            if d.actual_move != d.best_move and all(a.move != d.best_move for a in d.alternative_evals):
                table_rows.append(
                    f"| AI最善手 | `{d.best_move_str}` | **{d.best_eval_user:+.1f} 石** | - | - | - | 最善 |"
                )

            st.markdown(
                "| 候補手 | 選択 | 予測石差 | 開放度 | 相手手数 | 中割り | 危険判定 |\n"
                "| :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n"
                + "\n".join(table_rows)
            )

            st.markdown("##### 🤖 AIコーチによるアドバイス")
            st.markdown(_md_breaks(d.coach_comment))

        # ⚔️ 局面の分岐（実戦 vs 迷った手）を並べて比較
        if actual_branch and alt_branches and alt_branches[0]:
            primary_alt = alt_branches[0]
            with st.expander("⚔️ 【局面の分岐を並べて比較】 実戦手 vs 迷った手", expanded=False):
                st.caption("あなたが選んだ手と、迷っていた候補手の着手後局面を左右で比較できます。")
                c_act_col, c_alt_col = st.columns(2)
                with c_act_col:
                    st.markdown(f"**🔴 実戦手 `{d.actual_move_str}` 直後（石差: {actual_branch.eval_user:+.1f}石）**")
                    b_html = render_html_board(
                        board=actual_branch.board_after,
                        last_move=d.actual_move,
                        last_move_color="#ef4444",
                        flipped=actual_branch.flipped,
                        reply_move=actual_branch.opp_reply,
                        reply_label="相手",
                        cell_size=30,
                    )
                    st.markdown(b_html, unsafe_allow_html=True)
                    st.markdown(
                        f"- **相手の次の一手**: `{actual_branch.opp_reply_str}`\n"
                        f"- **直後の相手手数**: **{actual_branch.opp_mobility_count} 箇所**\n"
                        f"- **開放度**: **{actual_branch.openness}** ({'中割り' if actual_branch.is_nakawari else '外壁開放'})"
                    )
                with c_alt_col:
                    st.markdown(f"**🟠 迷った手 `{primary_alt.move_str}` 直後（石差: {primary_alt.eval_user:+.1f}石）**")
                    b_html = render_html_board(
                        board=primary_alt.board_after,
                        last_move=primary_alt.move,
                        last_move_color="#d97706",
                        flipped=primary_alt.flipped,
                        reply_move=primary_alt.opp_reply,
                        reply_label="相手",
                        cell_size=30,
                    )
                    st.markdown(b_html, unsafe_allow_html=True)
                    st.markdown(
                        f"- **相手の次の一手**: `{primary_alt.opp_reply_str}`\n"
                        f"- **直後の相手手数**: **{primary_alt.opp_mobility_count} 箇所**\n"
                        f"- **開放度**: **{primary_alt.openness}** ({'中割り' if primary_alt.is_nakawari else '外壁開放'})"
                    )


def render_blunder_card(
    b: PlyAnalysis,
    idx: int,
    engine: Optional[OthelloEngine] = None,
    user_color: int = BLACK,
) -> None:
    """悪手1件の詳細カード（局面分岐タブ・盤面比較・特徴量バッジ・Gemini解説）を描画"""
    f = b.features
    tactical_tags = getattr(b, "tactical_tags", [])
    tag_prefix = f"【{' / '.join(tactical_tags[:2])}】 " if tactical_tags else ""

    # 実戦手とAI最善手の分岐（展開）を算出
    actual_branch = compute_move_branch(
        board_before=b.board_before,
        move=b.move,
        player=b.player,
        user_color=user_color,
        engine=engine,
        known_eval_user=b.eval_after_user,
    )
    best_branch = compute_move_branch(
        board_before=b.board_before,
        move=b.best_move,
        player=b.player,
        user_color=user_color,
        engine=engine,
        known_eval_user=b.eval_before_user,
    )

    with st.expander(
        f"🚨 悪手 #{idx}: {tag_prefix}{b.ply}手目 実戦 `{b.move_str}` （推奨最善手: `{b.best_move_str}` | 評価値変動: {b.eval_diff_user:+.1f} 石）",
        expanded=(idx <= 3),
    ):
        col_board, col_detail = st.columns([1, 1.55])
        with col_board:
            tab_names = ["📌 着手前局面"]
            if actual_branch:
                tab_names.append(f"🔴 実戦手 ({b.move_str}) の分岐")
            if best_branch:
                tab_names.append(f"🔵 最善手 ({b.best_move_str}) の分岐")

            tabs = st.tabs(tab_names)
            with tabs[0]:
                st.caption(f"着手前（赤＝実戦手 `{b.move_str}` ／ 青＝最善手 `{b.best_move_str}`）")
                board_html = render_html_board(
                    board=b.board_before,
                    actual_move=b.move,
                    best_move=b.best_move,
                    cell_size=34,
                )
                st.markdown(board_html, unsafe_allow_html=True)
                st.caption(f"着手前形勢: **{b.eval_before_user:+.1f} 石**")

            if actual_branch and len(tabs) > 1:
                with tabs[1]:
                    st.caption(
                        f"🔴 実戦 `{b.move_str}` 着手後（金輪＝裏返った{len(actual_branch.flipped)}石 ／ 橙＝相手の次の一手「{actual_branch.opp_reply_str}」）"
                    )
                    board_html_act = render_html_board(
                        board=actual_branch.board_after,
                        last_move=b.move,
                        last_move_color="#ef4444",
                        flipped=actual_branch.flipped,
                        reply_move=actual_branch.opp_reply,
                        reply_label="急所",
                        cell_size=34,
                    )
                    st.markdown(board_html_act, unsafe_allow_html=True)
                    st.caption(
                        f"形勢: **{actual_branch.eval_user:+.1f} 石**（下落: **{b.eval_diff_user:+.1f} 石**）｜ 相手手数: **{actual_branch.opp_mobility_count} 箇所**"
                    )

            if best_branch and len(tabs) > 2:
                with tabs[2]:
                    st.caption(
                        f"🔵 最善 `{b.best_move_str}` 着手後（金輪＝裏返った{len(best_branch.flipped)}石 ／ 橙＝相手の応手「{best_branch.opp_reply_str}」）"
                    )
                    board_html_best = render_html_board(
                        board=best_branch.board_after,
                        last_move=b.best_move,
                        last_move_color="#0284c7",
                        flipped=best_branch.flipped,
                        reply_move=best_branch.opp_reply,
                        reply_label="応手",
                        cell_size=34,
                    )
                    st.markdown(board_html_best, unsafe_allow_html=True)
                    st.caption(
                        f"形勢: **{best_branch.eval_user:+.1f} 石**（維持）｜ 相手手数: **{best_branch.opp_mobility_count} 箇所**"
                    )

        with col_detail:
            if tactical_tags:
                st.markdown(f"**🏷️ 検出された戦術パターン**: {' '.join(f'`#{t}`' for t in tactical_tags)}")
            # 局面フェーズ（序盤/中盤前半/中盤後半/終盤）と、そのフェーズで重視すべき観点・主な原因
            empties_b = count_stones(b.board_before)[2]
            phase_b = get_phase(empties_b)
            factor_b = getattr(b, "tactical_factor", "")
            st.caption(
                f"🧭 局面フェーズ: **{phase_b['name']}**（空き{empties_b}マス）｜重視点: {phase_b['focus']}・{phase_b['advice']}"
                f"｜主な原因: {FACTOR_LABELS.get(factor_b, '読みの差（形の差は小さい）')}"
            )
            st.markdown("##### 📐 算出された盤面特徴量")
            if f:
                m1, m2, m3, m4 = st.columns(4)
                # 戦術判定（tactics.py）は重複なしの空きマス数（集合型開放度）を使うので、それを主に表示する
                m1.metric(
                    "打った手の開放度",
                    f"{f.openness_unique}（延べ{f.openness_total}）",
                    f"返した石 {f.flipped_count}個",
                )
                m2.metric(
                    "直後の相手着手可能数",
                    f"{f.opp_mobility_after} 箇所",
                    f"{f.opp_mobility_delta:+d} (最善手比)",
                    delta_color="inverse",
                )
                m3.metric("中割り判定", "⭕ 中割り" if f.is_nakawari else "❌ 非中割り")
                danger_label = (
                    "⚠️ X打ち" if f.is_x_move else ("⚠️ C打ち" if f.is_c_move else "安全マス")
                )
                m4.metric("危険マス判定", danger_label)
                st.info(f"**局面特徴**: {f.danger_detail}")

            st.markdown("##### 🤖 AIコーチによる戦術解説（オセロ戦術推論エンジン連携）")
            st.markdown(_md_breaks(b.coach_comment))

        # ⚔️ 局面の分岐（実戦 vs 最善）を並べて比較
        if actual_branch and best_branch:
            with st.expander("⚔️ 【局面の分岐を並べて比較】 実戦手 vs AI推奨最善手", expanded=False):
                st.caption("着手後の2つの盤面を並べて比較できます。石のめくられ方や相手の急所の違いを確認しましょう。")
                c_act_col, c_best_col = st.columns(2)
                with c_act_col:
                    st.markdown(f"**🔴 実戦手 `{b.move_str}` の分岐（石差: {actual_branch.eval_user:+.1f}石）**")
                    b_html = render_html_board(
                        board=actual_branch.board_after,
                        last_move=b.move,
                        last_move_color="#ef4444",
                        flipped=actual_branch.flipped,
                        reply_move=actual_branch.opp_reply,
                        reply_label="急所",
                        cell_size=30,
                    )
                    st.markdown(b_html, unsafe_allow_html=True)
                    st.markdown(
                        f"- **相手の次の一手（急所）**: `{actual_branch.opp_reply_str}`\n"
                        f"- **相手の着手可能数**: **{actual_branch.opp_mobility_count} 箇所** に拡大\n"
                        f"- **開放度**: **{actual_branch.openness}** ({'中割り' if actual_branch.is_nakawari else '外壁開放'})\n"
                        f"- **評価値変動**: **{b.eval_diff_user:+.1f} 石**"
                    )
                with c_best_col:
                    st.markdown(f"**🔵 推奨最善手 `{b.best_move_str}` の分岐（石差: {best_branch.eval_user:+.1f}石）**")
                    b_html = render_html_board(
                        board=best_branch.board_after,
                        last_move=b.best_move,
                        last_move_color="#0284c7",
                        flipped=best_branch.flipped,
                        reply_move=best_branch.opp_reply,
                        reply_label="応手",
                        cell_size=30,
                    )
                    st.markdown(b_html, unsafe_allow_html=True)
                    st.markdown(
                        f"- **相手の次の一手（応手）**: `{best_branch.opp_reply_str}`\n"
                        f"- **相手の着手可能数**: **{best_branch.opp_mobility_count} 箇所** に抑制\n"
                        f"- **開放度**: **{best_branch.openness}** ({'中割り' if best_branch.is_nakawari else '外壁開放'})\n"
                        f"- **評価値変動**: **±0.0 石（最善手）**"
                    )

                st.markdown(
                    f"""
                    ```text
                    【分岐ツリー】
                    {b.ply}手目 (あなた) ─┬─ 🔴 実戦手 {b.move_str} ──> 相手急所 {actual_branch.opp_reply_str} （評価値: {actual_branch.eval_user:+.1f}石 / 相手手数: {actual_branch.opp_mobility_count}箇所）
                                       └─ 🔵 最善手 {b.best_move_str} ──> 相手応手 {best_branch.opp_reply_str} （評価値: {best_branch.eval_user:+.1f}石 / 相手手数: {best_branch.opp_mobility_count}箇所）
                    ```
                    """
                )


def get_board_at_ply(
    kifu_str: str,
    target_ply: int,
) -> Tuple[List[List[int]], int, Optional[Tuple[int, int]], List[Tuple[int, int]], str]:
    """
    棋譜文字列から target_ply 手目の盤面、手番、直前の着手、裏返った石、着手文字列を再現する。
    """
    game = OthelloGame()
    moves = [kifu_str[i:i+2] for i in range(0, len(kifu_str), 2)]
    last_mv = None
    last_flipped: List[Tuple[int, int]] = []
    last_str = "-"
    for idx, ms in enumerate(moves[:target_ply], 1):
        r, c = str_to_coord(ms)
        last_mv = (r, c)
        last_str = ms
        game.make_move(r, c)
        if game.history:
            last_flipped = game.history[-1].flipped
    return [row[:] for row in game.board], game.current_turn, last_mv, last_flipped, last_str


def build_pvp_eval_chart(eval_history: List[dict], user_color: int, opp_name: str, current_ply: int = 0) -> go.Figure:
    """棋譜再生用の全手形勢評価推移グラフを作成する（AI戦・対人戦共通）"""
    plies = [0] + [a["ply"] for a in eval_history]
    evals = [0.0] + [a["eval_after_user"] for a in eval_history]
    hover = ["開始局面 (0.0)"] + [
        f"{a['ply']}手目: {a['move_str']}<br>あなたの予測石差: {a['eval_after_user']:+.1f}石 (変動 {a['eval_diff_user']:+.1f})<br>推奨手: {a['best_move_str']}"
        for a in eval_history
    ]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=plies,
        y=evals,
        mode="lines+markers",
        name="あなたの形勢 (予測最終石差)",
        line=dict(color="#6366f1", width=2.8),
        marker=dict(size=5, color="#818cf8"),
        text=hover,
        hoverinfo="text",
    ))

    # 悪手（Blunder）の強調表示
    blunder_plies = [a["ply"] for a in eval_history if a.get("is_blunder") and a.get("is_user_turn")]
    blunder_evals = [a["eval_after_user"] for a in eval_history if a.get("is_blunder") and a.get("is_user_turn")]
    if blunder_plies:
        fig.add_trace(go.Scatter(
            x=blunder_plies,
            y=blunder_evals,
            mode="markers",
            name="あなたの悪手 (Blunder)",
            marker=dict(color="#ef4444", size=10, symbol="x"),
            hoverinfo="none",
        ))

    # 現在選択されている手数のマーカー
    if 0 <= current_ply < len(plies):
        fig.add_trace(go.Scatter(
            x=[current_ply],
            y=[evals[current_ply]],
            mode="markers",
            name="表示中の手",
            marker=dict(color="#f59e0b", size=13, symbol="star"),
            hoverinfo="none",
        ))

    fig.add_hline(y=0, line_dash="dash", line_color="#94a3b8")
    # スマホでもグラフと盤面が同じ画面に収まるよう、高さ・余白・凡例を抑える
    fig.update_layout(
        title=dict(text=f"形勢（あなた vs {opp_name}・あなた視点の予想石差）", font=dict(size=13)),
        xaxis_title=None,
        yaxis_title=None,
        height=190,
        margin=dict(l=30, r=10, t=28, b=18),
        showlegend=False,
        plot_bgcolor="rgba(248, 250, 252, 0.8)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(fixedrange=True),
        yaxis=dict(fixedrange=True),
        dragmode=False,
    )
    return fig


REVIEW_TAB_LABEL = REVIEW_TAB


def request_review_jump(record_id: int, ply: int) -> None:
    """
    棋譜再生タブで指定の対局・手数を開く予約をする。
    タブの選択状態はウィジェットなので、作られる前（main の st.tabs の直前）に反映する。
    """
    st.session_state._review_jump = {"id": record_id, "ply": ply}


def _apply_review_jump() -> None:
    jump = st.session_state.pop("_review_jump", None)
    if not jump:
        return
    st.session_state.main_tab = REVIEW_TAB_LABEL
    st.session_state.pvp_selected_id = jump["id"]
    st.session_state.pvp_rec_selector = jump["id"]
    st.session_state.pvp_review_ply = jump["ply"]


def _swap_record_names(engine_name: str) -> str:
    """『対人戦 (A vs B)』（黒 vs 白）の名前を入れ替える。名前の付いていない記録はそのまま"""
    m = re.match(r"^(.*?)\((.+) vs (.+)\)\s*$", engine_name or "")
    if not m:
        return engine_name
    return f"{m.group(1)}({m.group(3).strip()} vs {m.group(2).strip()})"


def _start_recolor_job(rec: Dict[str, Any], engine: OthelloEngine, blunder_threshold: float, api_key: str) -> None:
    """
    記録した自分の色が逆だった対局を、反対の色として解析し直して同じ記録に上書きする（別スレッド）。
    悪手・評価値・総括はすべて自分の視点で作られているので、色だけ書き換えずに解析からやり直す。
    """
    game = OthelloGame()
    kifu = rec["kifu"]
    for i in range(0, len(kifu), 2):
        rc = str_to_coord(kifu[i:i + 2])
        if rc is None or rc not in get_legal_moves(game.board, game.current_turn):
            break
        game.make_move(*rc)
    job_id = f"recolor-{rec['id']}-{secrets.token_hex(4)}"
    bg_jobs.start_job(
        job_id,
        _postgame_compute,
        history=list(game.history),
        board=[row[:] for row in game.board],
        user_color=WHITE if rec["user_color"] == BLACK else BLACK,
        analyzer=engine,
        blunder_threshold=blunder_threshold,
        api_key=api_key,
        raw_dilemmas=[],
        saved_engine_name=_swap_record_names(rec["engine_name"]),
        record_id=rec["id"],
        kifu_str=game.get_kifu_string(),
    )
    st.session_state.recolor_job_id = job_id


@st.fragment(run_every=1.5)
def render_recolor_progress(job_id: str) -> None:
    """色の直し（解析し直し）の進み具合。終わったら画面全体を描き直して新しい記録を出す"""
    job = bg_jobs.get_job(job_id)
    if job is not None and job["status"] == "running":
        st.markdown(_job_progress_html(job, "自分の色を直して解析し直しています"), unsafe_allow_html=True)
        return
    if job is not None and job["status"] == "error":
        st.session_state._recolor_error = job["error"]
    bg_jobs.forget_job(job_id)
    st.session_state.recolor_job_id = None
    st.rerun(scope="app")


def render_pvp_tab(engine: OthelloEngine, blunder_threshold: float = -4.0, api_key: str = "") -> None:
    """
    📖 棋譜再生 ＆ 振り返り タブ（AI戦・対人戦・Wi-Fi対戦の共通画面）
    - 保存された全対局の棋譜を読み込み、1手ごとの盤面再生・AI形勢判断
    - 形勢推移グラフ（予測石差推移）と悪手（急所）マーカー
    - スライダー/ボタンによる1手送り・戻し
    - 「🎯 次の対局までに」: この対局の弱点に効く記事（学習タブ）と、次の対局への導線
    - 対局の一覧・削除（旧「過去の対局履歴」タブ）
    - 外部棋譜（テキスト貼り付け）のインポート＆AI自動解析
    """
    st.caption("AI戦・対人戦・Wi-Fi対戦のどの対局も、グラフと盤面で1手ずつ振り返れます。下の「🎯 次の対局までに」で読む記事を選べます。")

    # 棋譜インポート直後: 取り込んだ対局と最終手へジャンプ（ウィジェット生成前に反映する）
    pending_jump = st.session_state.pop("_pvp_pending_jump", None)
    if pending_jump:
        st.session_state.pvp_selected_id = pending_jump["id"]
        st.session_state.pvp_rec_selector = pending_jump["id"]
        st.session_state.pvp_review_ply = pending_jump["ply"]
        st.success(f"🎉 棋譜のインポートと全手AI解析が完了しました！（レコード ID: {pending_jump['id']}）")
        if pending_jump.get("skipped"):
            st.warning(
                f"⚠️ 棋譜の {pending_jump['ply'] + 1} 手目以降の {pending_jump['skipped']} 手は合法手でなかったため読み込んでいません。棋譜テキストを確認してください。"
            )

    # AI戦と対人戦を分けず、保存されている全対局を新しい順に並べる（既定は最新の対局）
    pvp_records = list_match_records(limit=200)
    if st.session_state.pvp_selected_id is None and pvp_records:
        st.session_state.pvp_selected_id = pvp_records[0]["id"]

    rec_options = [r["id"] for r in pvp_records]
    def format_rec_label(rid: int) -> str:
        r = next((x for x in pvp_records if x["id"] == rid), None)
        if not r:
            return f"ID {rid}"
        date_str = r["played_at"][5:16] if r.get("played_at") else ""
        opp = _opponent_name_from_record(r["engine_name"], r["user_color"])
        return f"#{r['id']} {date_str} vs {opp}｜{r['result_label']}（黒{r['black_score']}-白{r['white_score']}）"

    # 対戦レコード選択UI
    with st.container():
        cur_rec_id = st.session_state.pvp_selected_id if st.session_state.pvp_selected_id in rec_options else (rec_options[0] if rec_options else None)
        # 選択状態はウィジェットのキーで持つ（index と併用すると、インポート直後の切り替えがフロント側の値に負ける）
        if st.session_state.get("pvp_rec_selector") not in rec_options:
            st.session_state.pvp_rec_selector = cur_rec_id
        chosen_rec_id = st.selectbox(
            "振り返る対局を選択",
            options=rec_options,
            format_func=format_rec_label,
            key="pvp_rec_selector",
        )
        st.session_state.pvp_selected_id = chosen_rec_id

    selected_rec = next((r for r in pvp_records if r["id"] == chosen_rec_id), None)
    if not selected_rec:
        st.info("保存された対局がありません。対局を終えるか、下のインポート欄から棋譜を読み込んでください。")
        return

    kifu = selected_rec["kifu"]
    moves_list = [kifu[i:i+2] for i in range(0, len(kifu), 2)]
    total_plies = len(moves_list)
    user_col = selected_rec["user_color"]
    my_view_name = "あなた（⚫黒）" if user_col == BLACK else "あなた（⚪白）"
    opp_name = _opponent_name_from_record(selected_rec["engine_name"], selected_rec["user_color"])
    eval_hist = selected_rec.get("eval_history", [])
    b_score = selected_rec["black_score"]
    w_score = selected_rec["white_score"]

    def _pvp_set_ply(target_ply: int) -> None:
        st.session_state.pvp_review_ply = target_ply

    def _pvp_step(delta: int, max_val: int) -> None:
        cur = st.session_state.get("pvp_review_ply", 0)
        st.session_state.pvp_review_ply = max(0, min(max_val, cur + delta))

    # クランプ処理
    cur_ply = st.session_state.get("pvp_review_ply", total_plies)
    if cur_ply > total_plies:
        cur_ply = total_plies
        st.session_state.pvp_review_ply = total_plies
    elif cur_ply < 0:
        cur_ply = 0
        st.session_state.pvp_review_ply = 0

    # 対局サマリーバナー
    black_player = opp_name if user_col == WHITE else "あなた"
    white_player = "あなた" if user_col == WHITE else opp_name
    winner_str = f"⚫ {black_player} の勝利" if b_score > w_score else (f"⚪ {white_player} の勝利" if w_score > b_score else "引き分け")

    st.markdown(
        f"""
        <div style="background:linear-gradient(135deg, rgba(238, 242, 255, 0.96), rgba(253, 242, 248, 0.96)); border:2px solid #a5b4fc; border-radius:14px; padding:14px 20px; margin-bottom:14px; box-shadow:0 4px 14px rgba(139,148,214,0.12);">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                <div>
                    <div style="font-size:19px; font-weight:800; color:#1e1b4b;">
                        ⚔️ 対戦カード：⚫ {black_player} ({b_score}石) vs ⚪ {white_player} ({w_score}石)
                    </div>
                    <div style="font-size:14px; color:#4338ca; font-weight:600; margin-top:3px;">
                        結果：{winner_str}（石差 {abs(b_score - w_score)}石） ｜ 手数：{total_plies}手完局 ｜ 対局日時：{selected_rec.get('played_at', '-')}
                    </div>
                </div>
                <span style="background:#e0e7ff; color:#3730a3; padding:5px 14px; border-radius:20px; font-size:13px; font-weight:700;">
                    保存済み (ID {selected_rec['id']})
                </span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 1台の対人戦は、記録した自分の色が実際と逆になっていることがある（色を選ばずに後手を持った場合など）
    recolor_job = st.session_state.get("recolor_job_id")
    if recolor_job:
        render_recolor_progress(recolor_job)
    elif (selected_rec.get("engine_name") or "").startswith("対人戦"):
        err = st.session_state.pop("_recolor_error", None)
        if err:
            st.error(f"解析し直し中にエラーが起きました: {err}")
        other = "⚪ 白" if user_col == BLACK else "⚫ 黒"
        st.button(
            f"🔄 自分の色が逆？ {other}として解析し直す",
            key=f"btn_recolor_{selected_rec['id']}",
            type="tertiary",
            help="悪手・形勢グラフ・勝敗・レーティングを、反対の色のあなたの視点で作り直します。",
            on_click=_start_recolor_job,
            args=(selected_rec, engine, blunder_threshold, api_key),
        )

    # 1. 形勢推移グラフ
    if eval_hist:
        fig_eval = build_pvp_eval_chart(eval_hist, user_col, opp_name, st.session_state.pvp_review_ply)
        # クリックで選んだ点以外が薄くならないようにする
        fig_eval.update_traces(unselected=dict(marker=dict(opacity=1.0)))

        def _on_eval_chart_click() -> None:
            """グラフの点をクリックしたら、その手数の局面へ移動する（スライダー生成前に走るコールバック）"""
            event = st.session_state.get("pvp_eval_chart")
            try:
                points = event["selection"]["points"]
            except (KeyError, TypeError):
                points = getattr(getattr(event, "selection", None), "points", None) or []
            if points and points[0].get("x") is not None:
                st.session_state.pvp_review_ply = max(0, min(total_plies, int(round(float(points[0]["x"])))))

        st.plotly_chart(
            fig_eval,
            width='stretch',
            config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
            key="pvp_eval_chart",
            on_select=_on_eval_chart_click,
            selection_mode="points",
        )
        st.caption("📍 点タップでその手へ ／ ★表示中 ／ ✖あなたの悪手")

    # 2. 1手ごとの棋譜送り・盤面再生
    # 手数ナビゲーション: 矢印ボタンだけを横一列に並べる（スマホでも縦に積まれない横並びコンテナ）
    cur_ply = st.session_state.get("pvp_review_ply", total_plies)
    with st.container(horizontal=True, wrap=False, horizontal_alignment="center", vertical_alignment="center", gap="small"):
        st.button("⏮", key="btn_pvp_start", disabled=(cur_ply == 0), help="初手へ", on_click=_pvp_set_ply, args=(0,))
        st.button("◀", key="btn_pvp_prev", disabled=(cur_ply == 0), help="1手戻る", on_click=_pvp_step, args=(-1, total_plies))
        st.markdown(f"**{cur_ply} / {total_plies}手**", width="content")
        st.button("▶", key="btn_pvp_next", disabled=(cur_ply == total_plies), help="1手進む", on_click=_pvp_step, args=(1, total_plies))
        st.button("⏭", key="btn_pvp_end", disabled=(cur_ply == total_plies), help="最終手へ", on_click=_pvp_set_ply, args=(total_plies,))

    # 該当手数の盤面生成（スライダー操作またはボタン操作後の確定値）
    cur_ply = st.session_state.get("pvp_review_ply", total_plies)
    b_ply, turn_ply, last_mv, flipped, last_str = get_board_at_ply(kifu, cur_ply)

    # 自由検討・分岐ステートの管理（スライダー手数変更時は自動で実戦棋譜に同期）
    if st.session_state.get("pvp_branch_origin_ply") != cur_ply:
        st.session_state.pvp_branch_origin_ply = cur_ply
        st.session_state.pvp_branch_board = None
        st.session_state.pvp_branch_turn = None
        st.session_state.pvp_branch_history = []
        st.session_state.pvp_branch_sim_move = None

    is_branching = (st.session_state.get("pvp_branch_board") is not None)
    if is_branching:
        active_board = st.session_state.pvp_branch_board
        active_turn = st.session_state.pvp_branch_turn
        branch_history = st.session_state.get("pvp_branch_history", [])
        last_branch_move = branch_history[-1][2] if branch_history else None
        last_branch_flipped = branch_history[-1][3] if branch_history else []
    else:
        active_board = [row[:] for row in b_ply]
        active_turn = turn_ply
        last_branch_move = last_mv
        last_branch_flipped = flipped

    b_cnt, w_cnt, e_cnt = count_stones(active_board)
    legal_moves_dict = get_legal_moves(active_board, active_turn)
    legal_moves = list(legal_moves_dict.keys())
    game_finished = is_game_over(active_board)
    turn_name = "⚫ 黒" if active_turn == BLACK else "⚪ 白"
    opp_turn_name = "⚪ 白" if active_turn == BLACK else "⚫ 黒"

    # 実戦の次の手（正規棋譜での次手）
    actual_next_str = moves_list[cur_ply] if cur_ply < total_plies else None
    actual_next_coord = str_to_coord(actual_next_str) if actual_next_str else None

    # 次の手の候補手評価
    candidates_info = []
    best_move = None
    best_eval = 0.0

    if not game_finished and legal_moves:
        curr_eval = engine.evaluate_position(active_board, active_turn)
        best_move = curr_eval.best_move
        best_eval = curr_eval.score_for_player

        for mv in legal_moves:
            b_next, flipped_mv = apply_move(active_board, mv[0], mv[1], active_turn)
            ev_opp = engine.evaluate_position(b_next, -active_turn)
            score_turn = -ev_opp.score_for_player
            loss = max(0.0, best_eval - score_turn)
            opp_best = ev_opp.best_move
            tag = "✨最善手" if mv == best_move else ("⚠️大悪手" if loss >= 6.0 else ("⚠️悪手" if loss >= 2.5 else "普通手"))
            score_user = score_turn if active_turn == user_col else -score_turn
            mv_str = coord_to_str(*mv)
            is_actual = (actual_next_str == mv_str) and (not is_branching)
            candidates_info.append({
                "move": mv,
                "move_str": mv_str,
                "score_turn": score_turn,
                "score_user": score_user,
                "loss": loss,
                "flipped_cnt": len(flipped_mv),
                "flipped": flipped_mv,
                "opp_best": opp_best,
                "opp_best_str": coord_to_str(*opp_best) if opp_best else "パス",
                "tag": tag,
                "is_best": (mv == best_move),
                "is_actual": is_actual,
                "b_next": b_next,
            })
        candidates_info.sort(key=lambda x: x["score_turn"], reverse=True)
        # 最善手は盤面に書く数字と矛盾しないよう、候補手ごとの評価の最大で決め直す
        # （局面全体の評価と各候補の評価は読みの深さが違い、数字が小さい手が「最善」になることがあるため）
        best_move, best_eval = _rebase_best(candidates_info, "score_turn", best_move)
        for c in candidates_info:
            c["tag"] = "✨最善手" if c["is_best"] else ("⚠️大悪手" if c["loss"] >= 6.0 else ("⚠️悪手" if c["loss"] >= 2.5 else "普通手"))

    # 選択されているシミュレーション候補手
    sim_info = None
    if candidates_info:
        cur_sim = st.session_state.get("pvp_branch_sim_move")
        if cur_sim in [c["move"] for c in candidates_info]:
            sim_info = next(c for c in candidates_info if c["move"] == cur_sim)
        else:
            actual_cand = next((c for c in candidates_info if c["is_actual"]), None)
            sim_info = actual_cand if actual_cand else candidates_info[0]
            st.session_state.pvp_branch_sim_move = sim_info["move"]

    # 盤面描画対象
    ply_eval_info = next((a for a in eval_hist if a["ply"] == cur_ply), None)
    is_blunder_ply = ply_eval_info.get("is_blunder", False) if ply_eval_info else False

    # 次に打つ側（候補手の数字はこの人から見た予想最終石差）
    if is_branching:
        next_mover = turn_name
    else:
        next_mover = f"{turn_name}（{black_player if active_turn == BLACK else white_player}）"
    best_cand = next((c for c in candidates_info if c["is_best"]), candidates_info[0] if candidates_info else None)
    actual_cand = next((c for c in candidates_info if c["is_actual"]), None)

    show_after = False
    if sim_info is not None and not game_finished:
        show_after = st.session_state.get("pvp_show_after", False)

    if show_after:
        board_render = sim_info["b_next"]
        last_move_render = sim_info["move"]
        last_color_render = "#f59e0b"  # 金色 (着手シミュレーション)
        flipped_render = sim_info["flipped"]
        reply_render = sim_info["opp_best"]
        reply_label = "相手最善"
        hint_scores = None
        caption_sub = (
            f"👀 **{turn_name} {sim_info['move_str']} を打った後の盤面**を表示中（金の輪＝裏返る {sim_info['flipped_cnt']} 枚 ／ "
            f"点線の枠＝そのあと相手が打つ最善の手 {sim_info['opp_best_str']}）"
        )
    else:
        board_render = active_board
        last_move_render = last_branch_move
        last_color_render = "#ef4444" if is_blunder_ply else "#0284c7"
        flipped_render = last_branch_flipped
        reply_render = None
        reply_label = ""
        # 数字は常に「あなた」目線で固定（手番ごとに目線が入れ替わると読みにくいため）。グラフも同じ目線
        hint_scores = {c["move"]: c["score_user"] for c in candidates_info} if not game_finished else None
        last_note = f"＋印＝直前の手（{cur_ply}手目 {last_str}）" if (cur_ply > 0 and not is_branching) else "＋印＝直前の手"
        if hint_scores:
            caption_sub = f"🟩 緑＝最善手 ／ 金の点線＝実戦の手 ／ 青枠＝選んでいる手 ／ {last_note}"
        else:
            caption_sub = f"💡 {cur_ply} / {total_plies}手目 ｜ ⚫ 黒: {b_cnt}個 ｜ ⚪ 白: {w_cnt}個 ｜ 残り: {e_cnt}マス"

    # 盤面と詳細情報の2カラム
    col_board, col_detail = st.columns([1.05, 1.25])

    with col_board:
        if is_branching:
            branch_step_cnt = len(st.session_state.get("pvp_branch_history", []))
            st.markdown(
                f"""
                <div style="background:#fef3c7; border:2px solid #f59e0b; border-radius:10px; padding:8px 14px; margin-bottom:10px; box-shadow:0 2px 8px rgba(245,158,11,0.12);">
                    <div style="font-size:15px; font-weight:800; color:#92400e;">
                        🧪 【{cur_ply}手目からの自由検討・分岐中】（{branch_step_cnt}手進行）
                    </div>
                    <div style="font-size:13px; color:#b45309;">
                        手番：<b>{turn_name}番</b> ｜ 残り：<b>{e_cnt}マス</b> ｜ 実戦の次手は「<b>{actual_next_str or '-'}</b>」でした
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        if hint_scores:
            # 数字が誰から見た値かを、盤面のすぐ上にはっきり出す（説明文だけだと見落とすため）
            st.markdown(_perspective_pill_html(my_view_name, active_turn == user_col), unsafe_allow_html=True)
        board_html = render_html_board(
            board=board_render,
            last_move=last_move_render,
            last_move_color=last_color_render,
            flipped=flipped_render,
            reply_move=reply_render,
            reply_label=reply_label,
            cell_size=52,
            hint_scores=hint_scores,
            hint_higher_is_better=(active_turn == user_col),
            hint_best=[c["move"] for c in candidates_info if c["is_best"]] if hint_scores else None,
            hint_actual=actual_cand["move"] if (hint_scores and actual_cand) else None,
            hint_selected=sim_info["move"] if (hint_scores and sim_info) else None,
        )
        st.markdown(board_html, unsafe_allow_html=True)
        st.caption(caption_sub)

        # 矢印以外の操作は普段は畳んでおく（スマホで画面がボタンで埋まらないように）
        with st.expander("🧪 自由検討（候補手を打ってみる・1手戻す・盤面編集）", expanded=is_branching):
            if sim_info is not None and not game_finished:
                st.checkbox(
                    f"👀 右で選んだ手（{turn_name} {sim_info['move_str']}）を打った後の盤面を見る",
                    key="pvp_show_after",
                )

            # 自由検討・分岐アクションボタン
            if sim_info is not None and not game_finished:
                opp_badge = opp_turn_name
                ctl1, ctl2 = st.columns(2)
                with ctl1:
                    if st.button(f"▶️ {turn_name} {sim_info['move_str']} を打って検討を続ける", key="btn_pvp_play_sim", type="primary", width='stretch'):
                        if "pvp_branch_history" not in st.session_state:
                            st.session_state.pvp_branch_history = []
                        st.session_state.pvp_branch_history.append((
                            [row[:] for row in active_board],
                            active_turn,
                            sim_info["move"],
                            sim_info["flipped"],
                        ))
                        st.session_state.pvp_branch_board = sim_info["b_next"]
                        st.session_state.pvp_branch_turn = -active_turn
                        st.session_state.pvp_branch_sim_move = None
                        st.rerun()
                with ctl2:
                    can_reply = sim_info["opp_best"] is not None
                    if st.button(
                        f"⏩ {turn_name} {sim_info['move_str']} → {opp_badge} {sim_info['opp_best_str']}（相手の最善）まで打つ",
                        key="btn_pvp_play_both", disabled=not can_reply, width='stretch',
                    ):
                        if "pvp_branch_history" not in st.session_state:
                            st.session_state.pvp_branch_history = []
                        st.session_state.pvp_branch_history.append((
                            [row[:] for row in active_board],
                            active_turn,
                            sim_info["move"],
                            sim_info["flipped"],
                        ))
                        b_both, f_both = apply_move(sim_info["b_next"], sim_info["opp_best"][0], sim_info["opp_best"][1], -active_turn)
                        st.session_state.pvp_branch_history.append((
                            [row[:] for row in sim_info["b_next"]],
                            -active_turn,
                            sim_info["opp_best"],
                            f_both,
                        ))
                        st.session_state.pvp_branch_board = b_both
                        st.session_state.pvp_branch_turn = active_turn
                        st.session_state.pvp_branch_sim_move = None
                        st.rerun()

            # 分岐操作・リセットボタン
            b_c1, b_c2 = st.columns(2)
            with b_c1:
                can_undo = len(st.session_state.get("pvp_branch_history", [])) > 0
                if st.button("↩️ 1手戻す", key="btn_pvp_undo_branch", disabled=not can_undo, width='stretch'):
                    prev_b, prev_t, prev_m, prev_f = st.session_state.pvp_branch_history.pop()
                    st.session_state.pvp_branch_board = prev_b
                    st.session_state.pvp_branch_turn = prev_t
                    st.session_state.pvp_branch_sim_move = None
                    if not st.session_state.pvp_branch_history and prev_b == b_ply:
                        st.session_state.pvp_branch_board = None
                    st.rerun()
            with b_c2:
                if st.button("🔄 実戦の手順に戻す", key="btn_pvp_reset_branch", disabled=not is_branching, width='stretch'):
                    st.session_state.pvp_branch_board = None
                    st.session_state.pvp_branch_turn = None
                    st.session_state.pvp_branch_history = []
                    st.session_state.pvp_branch_sim_move = None
                    st.rerun()

            # 自由配置・盤面直接編集モード（エディター）
            st.markdown("**🛠️ 自由配置・マス目エディター**")
            with st.container():
                st.caption("オセロ盤上の任意のマス目を直接指定して、黒石・白石・空マスに自由に変更できます。")
                ed_c1, ed_c2, ed_c3, ed_c4 = st.columns([1, 1, 1.2, 1.2])
                with ed_c1:
                    ed_row = st.selectbox("行 (1~8)", options=list(range(8)), format_func=lambda r: ROW_NAMES[r], key="ed_pvp_row")
                with ed_c2:
                    ed_col = st.selectbox("列 (a~h)", options=list(range(8)), format_func=lambda c: COL_NAMES[c], key="ed_pvp_col")
                with ed_c3:
                    ed_val = st.selectbox("配置する石", options=[EMPTY, BLACK, WHITE], format_func=lambda v: "🟩 空マス" if v == EMPTY else ("⚫ 黒石" if v == BLACK else "⚪ 白石"), key="ed_pvp_val")
                with ed_c4:
                    st.write("")
                    st.write("")
                    if st.button("マス目を変更", key="btn_apply_pvp_cell", width='stretch'):
                        if st.session_state.pvp_branch_board is None:
                            st.session_state.pvp_branch_board = [row[:] for row in active_board]
                            st.session_state.pvp_branch_turn = active_turn
                        st.session_state.pvp_branch_board[ed_row][ed_col] = ed_val
                        st.session_state.pvp_branch_sim_move = None
                        st.rerun()

                ed_b1, ed_b2 = st.columns(2)
                with ed_b1:
                    if st.button(f"🔃 手番反転 ({opp_turn_name}番へ)", key="btn_flip_pvp_turn", width='stretch'):
                        if st.session_state.pvp_branch_board is None:
                            st.session_state.pvp_branch_board = [row[:] for row in active_board]
                        st.session_state.pvp_branch_turn = -active_turn
                        st.session_state.pvp_branch_sim_move = None
                        st.rerun()
                with ed_b2:
                    if st.button("🏁 この局面を初期化 (実戦盤面)", key="btn_reset_pvp_edit", width='stretch'):
                        st.session_state.pvp_branch_board = None
                        st.session_state.pvp_branch_turn = None
                        st.session_state.pvp_branch_history = []
                        st.session_state.pvp_branch_sim_move = None
                        st.rerun()

    with col_detail:
        # ① 直前の手（盤面の＋印）: 最善手との比較だけを出す
        if is_branching:
            st.markdown(
                f"""
                <div style="background:#fef3c7; border:1.5px solid #f59e0b; border-radius:10px; padding:8px 14px; margin-bottom:10px; font-size:14px; color:#92400e;">
                    🧪 <b>{cur_ply}手目からの自由検討中</b>（{len(st.session_state.pvp_branch_history)}手進行）｜ 黒 {b_cnt} ・ 白 {w_cnt} ・ 残り {e_cnt}マス
                </div>
                """,
                unsafe_allow_html=True,
            )
        elif cur_ply == 0:
            st.info("🏁 初期配置です。「▶️ 次の手」かスライダーで1手ずつ再生できます。")
        else:
            # パスがあると手数の偶奇と手番がずれるため、直前局面の手番から着手者を求める
            mover_color = get_board_at_ply(kifu, cur_ply - 1)[1]
            mover_name = black_player if mover_color == BLACK else white_player
            mover_badge = "⚫ 黒" if mover_color == BLACK else "⚪ 白"
            if ply_eval_info and is_blunder_ply:
                blunder_obj = next((b for b in selected_rec.get("blunders", []) if b["ply"] == cur_ply), None)
                if blunder_obj and blunder_obj.get("coach_comment"):
                    with st.expander(
                        f"📖 {cur_ply}手目 {mover_badge}（{mover_name}）{last_str} が悪かった理由",
                        expanded=True,
                    ):
                        st.markdown(_md_breaks(blunder_obj["coach_comment"]))

        # ② 次の手: 最善手と実戦手（または選んだ手）の比較
        if not game_finished and candidates_info and sim_info:
            next_ply = cur_ply + 1 + (len(st.session_state.get("pvp_branch_history", [])) if is_branching else 0)
            # ほかの手と比べたいときだけ選ぶ（盤面の青枠と連動）
            sel_key = f"sel_pvp_cand_{cur_ply}_{len(st.session_state.get('pvp_branch_history', []))}"
            sim_options = [c["move_str"] for c in candidates_info]

            def _on_pick_change() -> None:
                picked = st.session_state.get(sel_key)
                if picked:
                    st.session_state.pvp_branch_sim_move = str_to_coord(picked)

            def _pick_label(ms: str) -> str:
                c = next(x for x in candidates_info if x["move_str"] == ms)
                n_best = sum(1 for x in candidates_info if x["is_best"])
                note = ("最善" if n_best == 1 else "最善・同点") if c["is_best"] else f"{c['loss']:.0f}石損"
                return f"{ms}（あなた目線 {c['score_user']:+.0f}石・{note}）" + ("　★実戦" if c["is_actual"] else "")

            st.selectbox(
                "ほかの手と比べる（盤面の青枠）",
                options=sim_options,
                index=sim_options.index(sim_info["move_str"]) if sim_info["move_str"] in sim_options else 0,
                format_func=_pick_label,
                key=sel_key,
                on_change=_on_pick_change,
            )

            if not sim_info["is_best"]:
                tac_res = analyze_tactical_patterns(
                    board_before=active_board,
                    actual_move=sim_info["move"],
                    best_move=best_move,
                    eval_loss=sim_info["loss"],
                    player=active_turn,
                )
                with st.expander(
                    f"📖 {next_ply}手目 {next_mover}：{sim_info['move_str']} が最善 {best_cand['move_str']} より悪い理由",
                    expanded=True,
                ):
                    st.markdown(_md_breaks(_as_candidate_text(tac_res.commentary)))

        elif game_finished:
            st.success(f"🏁 終局（黒 {b_cnt}石 - 白 {w_cnt}石）")

    # 3. 勝敗を分けた急所・悪手一覧テーブル（普段は畳む）
    with st.expander("📊 勝敗を分けた急所・悪手一覧", expanded=False):
        my_badge = "⚪ あなた（白）" if user_col == WHITE else "⚫ あなた（黒）"
        opp_badge = f"⚫ {opp_name}（黒）" if user_col == WHITE else f"⚪ {opp_name}（白）"
        t_w_tab, t_b_tab = st.tabs([f"{my_badge}の急所・悪手", f"{opp_badge}の急所・悪手"])

        with t_w_tab:
            user_blunders = [b for b in selected_rec.get("blunders", [])]
            if user_blunders:
                for b in user_blunders:
                    b_c1, b_c2, b_c3 = st.columns([1, 4, 1.2])
                    with b_c1:
                        st.write(f"**{b['ply']}手目**")
                    with b_c2:
                        st.write(f"着手: **`{b['move_str']}`** ➔ 最善手: **`{b['best_move_str']}`** (損失: `{b['eval_diff_user']:+.1f}石`)")
                    with b_c3:
                        st.button(
                            "盤面を見る",
                            key=f"btn_jump_w_{b['ply']}",
                            width='stretch',
                            on_click=_pvp_set_ply,
                            args=(b["ply"],),
                        )
            else:
                st.info("あなたの目立った悪手は検出されませんでした！")

        with t_b_tab:
            black_blunder_plies = [a for a in eval_hist if not a.get("is_user_turn") and a.get("eval_diff_user", 0) >= 4.0]
            if black_blunder_plies:
                for a in black_blunder_plies:
                    b_c1, b_c2, b_c3 = st.columns([1, 4, 1.2])
                    with b_c1:
                        st.write(f"**{a['ply']}手目**")
                    with b_c2:
                        loss_for_opp = a["eval_diff_user"]
                        st.write(f"着手: **`{a['move_str']}`** ➔ 最善手: **`{a['best_move_str']}`** (相手の損失: `{-loss_for_opp:+.1f}石`)")
                    with b_c3:
                        st.button(
                            "盤面を見る",
                            key=f"btn_jump_b_{a['ply']}",
                            width='stretch',
                            on_click=_pvp_set_ply,
                            args=(a["ply"],),
                        )
            else:
                st.info(f"{opp_name} の目立った悪手は検出されませんでした！")

    # 振り返り → 学習 → 対局: この対局の弱点に効く記事と、次の対局へのボタン
    render_next_steps(selected_rec)


    # 4. 対局全体のAIコーチングアドバイスサマリー
    st.markdown("#### 📋 AIコーチによる対戦総括レビュー")
    summary_text = selected_rec.get("coach_summary", "")
    if summary_text:
        st.markdown(summary_text)

    # 5. 外部棋譜の新規インポート
    with st.expander("📥 外部の棋譜を読み込んでAI解析＆保存する", expanded=False):
        imp_c1, imp_c2 = st.columns([2, 1])
        with imp_c1:
            new_opp_name = st.text_input("対戦相手の名前", value="Yohane", key="input_pvp_opp_name")
        with imp_c2:
            new_user_color_str = st.selectbox("あなたの手番", options=["白 (後手 ○)", "黒 (先手 ●)"], key="sel_pvp_user_color")
            new_user_color = WHITE if "白" in new_user_color_str else BLACK

        new_kifu_raw = st.text_area(
            "対戦棋譜テキスト（形式自由: `1. ●f5 2. ○d6...` や `f5d6c3...`）",
            value="",
            height=120,
            key="input_pvp_kifu_text",
            placeholder="1. ●f5 2. ○d6 3. ●c3 4. ○d3 ...",
        )
        if st.button("🚀 棋譜を読み込んでAI解析＆保存する", key="btn_run_import_pvp", type="primary"):
            if not new_kifu_raw.strip():
                st.error("棋譜テキストを入力してください。")
            else:
                with st.spinner("棋譜を解析し、全手評価値と悪手を計算中..."):
                    try:
                        moves_str = re.findall(r'([a-hA-H][1-8])', new_kifu_raw)
                        if not moves_str:
                            st.error("棋譜から着手座標（a1〜h8）を検出できませんでした。")
                        else:
                            temp_game = OthelloGame()
                            for m in moves_str:
                                r, c = str_to_coord(m)
                                if (r, c) not in get_legal_moves(temp_game.board, temp_game.current_turn):
                                    break
                                temp_game.make_move(r, c)
                            kifu_cleaned = temp_game.get_kifu_string()
                            b_c, w_c, _ = count_stones(temp_game.board)
                            analyses = analyze_game_history(temp_game.history, new_user_color, engine, blunder_threshold=-4.0)
                            blunders = [a for a in analyses if a.is_blunder]
                            coach_sum, _ = generate_overall_coaching_summary(
                                analyses=analyses,
                                blunders=blunders,
                                user_color=new_user_color,
                                black_score=b_c,
                                white_score=w_c,
                                kifu_str=kifu_cleaned,
                            )
                            new_id = save_match_record(
                                user_color=new_user_color,
                                engine_name=f"対人戦 (vs {new_opp_name.strip() or 'Human'})",
                                black_score=b_c,
                                white_score=w_c,
                                kifu=kifu_cleaned,
                                analyses=analyses,
                                coach_summary=coach_sum,
                                antigravity_review=f"対人戦 (vs {new_opp_name}) のインポート棋譜解析。",
                                # 終局まで入っていない棋譜は途中局面の石数で勝敗をつけない
                                interrupted=not is_game_over(temp_game.board),
                            )
                            # スライダー等のウィジェット生成後は値を直接変更できないため、次回実行の冒頭で反映する
                            st.session_state._pvp_pending_jump = {
                                "id": new_id,
                                "ply": len(temp_game.history),
                                "skipped": len(moves_str) - len(temp_game.history),
                            }
                            st.rerun()
                    except Exception as ex:
                        st.error(f"棋譜の解析中にエラーが発生しました: {ex}")

    # 6. 対局の一覧と削除（旧「過去の対局履歴」タブ。対局の選択は上のセレクトボックスで行う）
    with st.expander("🗂️ 対局の一覧・削除", expanded=False):
        st.dataframe(
            [
                {
                    "ID": f"#{r['id']}",
                    "日時": r["played_at"],
                    "相手": _opponent_name_from_record(r["engine_name"], r["user_color"]),
                    "あなた": color_name(r["user_color"]),
                    "勝敗": r["result_label"],
                    "黒-白": f"{r['black_score']}-{r['white_score']}",
                    "悪手": r["blunder_count"],
                }
                for r in pvp_records
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(f"表示中の対局：#{selected_rec['id']}（{selected_rec.get('played_at', '-')}）")
        sure = st.checkbox("この対局の記録を削除する（元に戻せません）", key=f"del_confirm_{selected_rec['id']}")
        if st.button("🗑️ 削除", key=f"del_{selected_rec['id']}", disabled=not sure):
            delete_match_record(selected_rec["id"])
            st.session_state.pvp_selected_id = None
            st.rerun()


# 局面検討タブは廃止（自由検討は振り返りタブの中で行う）。関数は参考のため残している
def render_sandbox_tab(engine: OthelloEngine, api_key: str = "") -> None:
    """
    🔬 局面検討 ＆ AIアナライザー タブ
    - 任意局面（プリセット / 対戦コピー / 手動編集）のリアルタイム評価
    - 候補手ごとの予測最終石差、評価損、相手返し手数、裏返り石数の一覧
    - 「もし〜に打ったら？」シミュレーション（着手石＋返却石のハイライト、相手の最善応手予告）
    - ルールベース戦術推論エンジン（tactics.py）による悪手・好手・手止まり・偶数理論の判定
    - Gemini によるプロ目線ポジションコーチング
    - 盤面の一人プレイ・手番反転・Undo/Redo・自由編集モード
    """
    st.subheader("🔬 局面検討 ＆ AIアナライザー (What-if 思考＆盤面研究)")
    st.caption(
        "対局中の気になる局面やユーザー質問の盤面を読み込み、候補手ごとの評価値・石損・相手の返し手・裏返る石をリアルタイムでシミュレーションできます。"
    )

    # 1. プリセット選択バー
    c_p1, c_p2, c_p3 = st.columns([3.2, 1.2, 1.6])
    preset_keys = list(SANDBOX_PRESETS.keys())

    def _on_sandbox_preset_change() -> None:
        key = st.session_state.get("sandbox_preset_selector")
        if key in SANDBOX_PRESETS:
            load_sandbox_preset(key)

    with c_p1:
        preset_choice = st.selectbox(
            "検討する局面プリセットを選択",
            options=preset_keys,
            format_func=lambda k: SANDBOX_PRESETS[k]["name"],
            key="sandbox_preset_selector",
            on_change=_on_sandbox_preset_change,
        )

    # 画面リロード時や初回アクセス時の自動同期（外部コピー盤面 custom 以外）
    cur_loaded = st.session_state.get("_last_loaded_preset_key")
    if cur_loaded != preset_choice:
        if cur_loaded != "custom":
            load_sandbox_preset(preset_choice)

    with c_p2:
        if st.button("🔄 局面をリセット", key="btn_load_sandbox_preset", width='stretch', help="このプリセットの初期配置に戻します"):
            load_sandbox_preset(preset_choice)
            st.rerun()
    with c_p3:
        if st.button("📋 対戦盤面をコピー", key="btn_copy_from_game", width='stretch'):
            st.session_state.sandbox_board = [row[:] for row in st.session_state.game.board]
            st.session_state.sandbox_turn = st.session_state.game.current_turn
            st.session_state.sandbox_sim_move = None
            st.session_state.sandbox_history = []
            st.session_state.sandbox_ai_coach_text = ""
            st.session_state.sandbox_preset_name = "対戦タブの盤面コピー"
            st.session_state._last_loaded_preset_key = "custom"
            st.session_state.pop("radio_sandbox_sim", None)
            st.rerun()

    # 局面概要
    if st.session_state.get("_last_loaded_preset_key") == "custom":
        st.info(f"📋 **外部読込局面**: {st.session_state.sandbox_preset_name} を表示中（プリセットを選択するか「🔄 局面をリセット」を押すとプリセット局面に戻ります）")
    elif preset_choice in SANDBOX_PRESETS:
        st.info(f"💡 **局面解説**: {SANDBOX_PRESETS[preset_choice]['desc']}")

    cur_board = st.session_state.sandbox_board
    cur_turn = st.session_state.sandbox_turn
    b_cnt, w_cnt, e_cnt = count_stones(cur_board)
    legal_moves_dict = get_legal_moves(cur_board, cur_turn)
    legal_moves = list(legal_moves_dict.keys())
    game_finished = is_game_over(cur_board)
    turn_name = "⚫ 黒" if cur_turn == BLACK else "⚪ 白"
    opp_turn_name = "⚪ 白" if cur_turn == BLACK else "⚫ 黒"

    # 盤面編集モードトグル
    edit_mode = st.checkbox("🛠️ 盤面を直接編集する (マス目を黒・白・空に変更)", value=st.session_state.sandbox_edit_mode, key="chk_sandbox_edit")
    st.session_state.sandbox_edit_mode = edit_mode

    if edit_mode:
        with st.expander("🛠️ 盤面エディター操作パネル", expanded=True):
            ed_c1, ed_c2, ed_c3, ed_c4 = st.columns([1, 1, 1.2, 1.2])
            with ed_c1:
                ed_row = st.selectbox("行 (1~8)", options=list(range(8)), format_func=lambda r: ROW_NAMES[r], key="ed_row_sel")
            with ed_c2:
                ed_col = st.selectbox("列 (a~h)", options=list(range(8)), format_func=lambda c: COL_NAMES[c], key="ed_col_sel")
            with ed_c3:
                ed_val = st.selectbox("配置する石", options=[EMPTY, BLACK, WHITE], format_func=lambda v: "🟩 空マス" if v == EMPTY else ("⚫ 黒石" if v == BLACK else "⚪ 白石"), key="ed_val_sel")
            with ed_c4:
                st.write("")
                st.write("")
                if st.button("マスを変更", key="btn_apply_cell"):
                    st.session_state.sandbox_board[ed_row][ed_col] = ed_val
                    st.session_state.sandbox_sim_move = None
                    st.rerun()

            ed_btn_c1, ed_btn_c2, ed_btn_c3 = st.columns(3)
            with ed_btn_c1:
                if st.button("🗑️ 盤面を全消去", key="btn_clear_board", width='stretch'):
                    st.session_state.sandbox_board = [[EMPTY]*8 for _ in range(8)]
                    st.session_state.sandbox_sim_move = None
                    st.rerun()
            with ed_btn_c2:
                if st.button("🏁 初期配置に戻す", key="btn_reset_initial", width='stretch'):
                    st.session_state.sandbox_board = create_initial_board()
                    st.session_state.sandbox_turn = BLACK
                    st.session_state.sandbox_sim_move = (2, 3)
                    st.rerun()
            with ed_btn_c3:
                if st.button(f"🔃 手番を {opp_turn_name} に変更", key="btn_toggle_turn", width='stretch'):
                    st.session_state.sandbox_turn = -cur_turn
                    st.session_state.sandbox_sim_move = None
                    st.rerun()

    # 候補手の評価計算
    candidates_info = []
    best_move = None
    best_eval = 0.0

    if not game_finished and legal_moves:
        eval_curr = engine.evaluate_position(cur_board, cur_turn)
        best_move = eval_curr.best_move
        best_eval = eval_curr.score_for_player

        for mv in legal_moves:
            mv_str = coord_to_str(*mv)
            b_next, flipped = apply_move(cur_board, mv[0], mv[1], cur_turn)
            opp_legals = get_legal_moves(b_next, -cur_turn)
            opp_res = engine.evaluate_position(b_next, -cur_turn)
            move_eval = -opp_res.score_for_player
            loss = max(0.0, round(best_eval - move_eval, 1))
            opp_best_reply = opp_res.best_move

            tag = "✨最善手" if mv == best_move else ""
            if loss >= 8.0:
                tag = "❌大悪手"
            elif loss >= 4.0:
                tag = "⚠️悪手"
            elif loss >= 2.0:
                tag = "⚡疑問手"
            elif mv != best_move and loss < 1.0:
                tag = "🟢好手"

            candidates_info.append({
                "move": mv,
                "move_str": mv_str,
                "eval": move_eval,
                "eval_b": move_eval if cur_turn == BLACK else -move_eval,  # 表示用（黒目線で固定）
                "loss": loss,
                "flipped_cnt": len(flipped),
                "flipped": flipped,
                "opp_mob": len(opp_legals),
                "opp_best": opp_best_reply,
                "opp_best_str": coord_to_str(*opp_best_reply) if opp_best_reply else "パス",
                "tag": tag,
                "is_best": (mv == best_move),
                "b_next": b_next,
            })
        candidates_info.sort(key=lambda x: x["eval"], reverse=True)
        # 最善手は盤面に書く数字と矛盾しないよう、候補手ごとの評価の最大で決め直す
        best_move, best_eval = _rebase_best(candidates_info, "eval", best_move)
        for c in candidates_info:
            loss = c["loss"]
            if c["is_best"]:
                c["tag"] = "✨最善手"
            elif loss >= 8.0:
                c["tag"] = "❌大悪手"
            elif loss >= 4.0:
                c["tag"] = "⚠️悪手"
            elif loss >= 2.0:
                c["tag"] = "⚡疑問手"
            elif loss < 1.0:
                c["tag"] = "🟢好手"
            else:
                c["tag"] = ""

    # 選択されているシミュレーション着手
    sim_info = None
    if candidates_info:
        if st.session_state.sandbox_sim_move in [c["move"] for c in candidates_info]:
            sim_info = next(c for c in candidates_info if c["move"] == st.session_state.sandbox_sim_move)
        else:
            sim_info = candidates_info[0]
            st.session_state.sandbox_sim_move = sim_info["move"]

    # 2カラムレイアウト
    col_board, col_analysis = st.columns([1.05, 1.25])

    with col_board:
        # 手番・石数ステータスバー
        st.markdown(
            f"""
            <div style="background:linear-gradient(135deg, rgba(240, 249, 255, 0.95), rgba(238, 242, 255, 0.95)); border:2px solid #93c5fd; border-radius:12px; padding:10px 16px; margin-bottom:12px; box-shadow:0 3px 10px rgba(59,130,246,0.08);">
                <div style="font-size:17px; font-weight:800; color:#1e293b;">
                    手番：<span style="color:#2563eb;">{turn_name}番</span> ｜ ⚫ 黒: {b_cnt} 個 ｜ ⚪ 白: {w_cnt} 個 ｜ 残り: {e_cnt} マス
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if game_finished:
            st.warning(f"🏁 終局状態です（黒 {b_cnt}石 - 白 {w_cnt}石 / 差 {b_cnt - w_cnt:+d}石）")
            board_html = render_html_board(board=cur_board, cell_size=52)
            st.markdown(board_html, unsafe_allow_html=True)
        elif not legal_moves:
            st.warning(f"⚠️ {turn_name}番は着手可能なマスがありません（パス）")
            board_html = render_html_board(board=cur_board, cell_size=52)
            st.markdown(board_html, unsafe_allow_html=True)
            if st.button(f"⏭️ パスして {opp_turn_name}番に進める", key="btn_pass_sandbox", type="primary", width='stretch'):
                st.session_state.sandbox_history.append(([row[:] for row in cur_board], cur_turn))
                st.session_state.sandbox_turn = -cur_turn
                st.session_state.sandbox_sim_move = None
                st.rerun()
        else:
            # 既定は「今の局面」に各候補手の評価値を書き込んで表示し、チェック時だけ選んだ手を打った後の盤面を出す
            if st.session_state.get("sandbox_show_after", False):
                board_html = render_html_board(
                    board=sim_info["b_next"],
                    last_move=sim_info["move"],
                    last_move_color="#f59e0b",
                    flipped=sim_info["flipped"],
                    reply_move=sim_info["opp_best"],
                    reply_label="相手最善",
                    cell_size=52,
                )
                caption_text = (
                    f"👀 **{turn_name} {sim_info['move_str']} を打った後の盤面**を表示中（金の輪＝裏返る {sim_info['flipped_cnt']} 枚 ／ "
                    f"点線の枠＝そのあと相手が打つ最善の手 {sim_info['opp_best_str']}）"
                )
            else:
                st.markdown(_perspective_pill_html("⚫ 黒", cur_turn == BLACK, "⚪ 白"), unsafe_allow_html=True)
                board_html = render_html_board(
                    board=cur_board,
                    cell_size=52,
                    hint_scores={c["move"]: c["eval_b"] for c in candidates_info},
                    hint_higher_is_better=(cur_turn == BLACK),
                    hint_best=[c["move"] for c in candidates_info if c["is_best"]],
                    hint_selected=sim_info["move"],
                )
                caption_text = f"手番：{turn_name} ／ 🟩 緑＝最善手 ／ 青枠＝右で選んでいる手"
            st.markdown(board_html, unsafe_allow_html=True)
            st.caption(caption_text)
            st.checkbox(
                f"👀 右で選んだ手（{turn_name} {sim_info['move_str']}）を打った後の盤面を見る",
                key="sandbox_show_after",
            )

            # 操作ボタン
            ctl_c1, ctl_c2 = st.columns(2)
            with ctl_c1:
                if st.button(f"▶️ {turn_name} {sim_info['move_str']} を打って検討を続ける", key="btn_play_sim_move", type="primary", width='stretch'):
                    st.session_state.sandbox_history.append(([row[:] for row in cur_board], cur_turn))
                    st.session_state.sandbox_board = sim_info["b_next"]
                    st.session_state.sandbox_turn = -cur_turn
                    st.session_state.sandbox_sim_move = None
                    st.rerun()
            with ctl_c2:
                can_reply = sim_info["opp_best"] is not None
                if st.button(f"⏩ {turn_name} {sim_info['move_str']} → {opp_turn_name} {sim_info['opp_best_str']}（相手の最善）まで打つ", key="btn_play_both", disabled=not can_reply, width='stretch'):
                    st.session_state.sandbox_history.append(([row[:] for row in cur_board], cur_turn))
                    b_both, _ = apply_move(sim_info["b_next"], sim_info["opp_best"][0], sim_info["opp_best"][1], -cur_turn)
                    st.session_state.sandbox_board = b_both
                    st.session_state.sandbox_turn = cur_turn
                    st.session_state.sandbox_sim_move = None
                    st.rerun()

        # 履歴戻し・初期化ボタン
        sub_c1, sub_c2, sub_c3 = st.columns(3)
        with sub_c1:
            has_history = len(st.session_state.sandbox_history) > 0
            if st.button("↩️ 1手戻す", key="btn_undo_sandbox", disabled=not has_history, width='stretch'):
                prev_b, prev_t = st.session_state.sandbox_history.pop()
                st.session_state.sandbox_board = prev_b
                st.session_state.sandbox_turn = prev_t
                st.session_state.sandbox_sim_move = None
                st.rerun()
        with sub_c2:
            if st.button("🔄 局面を初期化", key="btn_reset_sandbox", width='stretch'):
                if st.session_state.get("_last_loaded_preset_key") in SANDBOX_PRESETS:
                    load_sandbox_preset(st.session_state._last_loaded_preset_key)
                elif preset_choice in SANDBOX_PRESETS:
                    load_sandbox_preset(preset_choice)
                st.rerun()
        with sub_c3:
            if st.button(f"🔃 手番反転 ({opp_turn_name})", key="btn_flip_turn_quick", width='stretch'):
                st.session_state.sandbox_turn = -cur_turn
                st.session_state.sandbox_sim_move = None
                st.rerun()

    with col_analysis:
        if candidates_info and sim_info:
            st.markdown("#### 🎯 候補手シミュレーション切り替え")
            sim_options = [c["move_str"] for c in candidates_info]

            def sim_label(ms: str) -> str:
                c = next(x for x in candidates_info if x["move_str"] == ms)
                tag_p = f"[{c['tag']}] " if c["tag"] else ""
                loss_p = f" (損失 {c['loss']:.1f}石)" if c["loss"] > 0 else " (最善)"
                return f"{tag_p}{ms}: 黒目線 {c['eval_b']:+.1f}石{loss_p} / 返却 {c['flipped_cnt']}個"

            cur_sim_idx = sim_options.index(sim_info["move_str"]) if sim_info["move_str"] in sim_options else 0
            chosen_sim_str = st.radio(
                "手を選ぶと、その手の解説が下に出ます（盤面では青枠）",
                options=sim_options,
                format_func=sim_label,
                index=cur_sim_idx,
                key="radio_sandbox_sim",
                horizontal=False,
            )
            if chosen_sim_str != sim_info["move_str"]:
                st.session_state.sandbox_sim_move = str_to_coord(chosen_sim_str)
                st.rerun()

            # ルールベース戦術解説の取得
            tac_res = analyze_tactical_patterns(
                board_before=cur_board,
                actual_move=sim_info["move"],
                best_move=best_move,
                eval_loss=sim_info["loss"],
                player=cur_turn,
            )

            # 戦術解説カード
            status_color = "#10b981" if sim_info["is_best"] else ("#ef4444" if sim_info["loss"] >= 8.0 else "#f59e0b")
            st.markdown(
                f"""
                <div style="background:white; border:2px solid {status_color}; border-radius:12px; padding:14px 18px; margin-top:12px; margin-bottom:14px; box-shadow:0 3px 12px rgba(15,23,42,0.06);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                        <span style="font-size:17px; font-weight:800; color:#1e293b;">🔍 {turn_name} が {sim_info['move_str']} に打った場合</span>
                        <span style="background:{status_color}; color:white; padding:3px 10px; border-radius:8px; font-weight:700; font-size:13px;">{sim_info['tag'] or '検討'}</span>
                    </div>
                    <div style="font-size:14px; color:#475569; line-height:1.6;">
                        <b>予測最終石差</b>: <span style="font-weight:700; color:#0f172a;">{sim_info['eval']:+.1f} 石</span> 
                        （最善手比: <span style="color:{'#ef4444' if sim_info['loss'] > 0 else '#10b981'}; font-weight:700;">{-sim_info['loss']:+.1f} 石</span>）<br>
                        <b>返却石数</b>: {sim_info['flipped_cnt']} 個 ｜ <b>相手の合法手数</b>: {sim_info['opp_mob']} 箇所 ｜ <b>AI予想返答</b>: <code>{sim_info['opp_best_str']}</code>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # タグ一覧
            if tac_res.tags:
                tag_badges = " ".join([f"<span style='background:#f1f5f9; color:#334155; border:1px solid #cbd5e1; padding:3px 8px; border-radius:6px; font-size:12px; font-weight:600;'>#{t}</span>" for t in tac_res.tags])
                st.markdown(tag_badges, unsafe_allow_html=True)

            # 詳細解説テキスト
            st.markdown(_md_breaks(_as_candidate_text(tac_res.commentary)))

            # 候補手一覧の比較テーブル
            st.markdown("##### 📊 候補手一覧の比較表")
            table_rows = []
            table_rows.append("| 順位 | 着手 | 予測石差（黒目線） | 評価損 | 返却石 | 相手手数 | 予想応手 | 判定 |")
            table_rows.append("| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
            for idx, c in enumerate(candidates_info, 1):
                loss_disp = "最善" if c["loss"] == 0 else f"-{c['loss']:.1f}石"
                table_rows.append(f"| {idx} | **`{c['move_str']}`** | {c['eval_b']:+.1f}石 | {loss_disp} | {c['flipped_cnt']}個 | {c['opp_mob']}箇所 | `{c['opp_best_str']}` | {c['tag'] or '-'} |")
            st.markdown("\n".join(table_rows))

            # Gemini AIコーチ質問ボタン
            st.divider()
            if st.button("🤖 AIコーチにこの局面を深く質問する", key="btn_ask_gemini_sandbox", type="secondary", width='stretch'):
                with st.spinner("AIコーチが局面の戦術と要点を分析中..."):
                    coach_text, used_g = generate_sandbox_position_coaching(
                        cur_board,
                        cur_turn,
                        candidates_info,
                        # サイドバーで入力されたキーを優先（未入力なら関数内で環境変数を参照）
                        api_key=api_key,
                    )
                    st.session_state.sandbox_ai_coach_text = coach_text

            if st.session_state.sandbox_ai_coach_text:
                st.markdown(
                    f"""
                    <div style="background:#f8fafc; border-left:4px solid #6366f1; border-radius:8px; padding:12px 16px; margin-top:12px;">
                        <div style="font-weight:800; color:#4338ca; margin-bottom:6px;">📋 AIコーチによる局面診断アドバイス</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.markdown(st.session_state.sandbox_ai_coach_text)


@st.fragment(run_every=1)
def render_online_room_fragment(
    room_id: str,
    engine: Optional[OthelloEngine],
    blunder_threshold: float,
    api_key_input: str,
    can_save: bool = True,
) -> None:
    room = room_manager.get_room(room_id)
    if not room:
        st.warning("⚠️ 部屋が見つかりません。解散されたか期限切れです。")
        return

    my_role = st.session_state.get("online_player_role", "host")
    if my_role == "host":
        my_name = room.host_name
        my_color = room.host_color
        opp_name = room.guest_name or "友達（参加待機中...）"
        opp_color = room.guest_color
    else:
        my_name = room.guest_name or "ゲスト"
        my_color = room.guest_color
        opp_name = room.host_name
        opp_color = room.host_color

    # 合言葉入りの招待URL（友達が開くとゲスト画面でこの部屋がすぐ表示される）
    conn_url = room_invite_url(room.room_id)
    qr_url = "https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=" + urllib.parse.quote(conn_url)

    # 1. 待機中画面
    if room.status == "waiting":
        if room.color_mode == "random":
            color_wait_sub = "🎲 手番設定: <b>ランダム（おまかせ）</b> — 友達が参加した瞬間にコイントスで黒・白が決定します！"
        else:
            h_str = "⚫ 黒（先手）" if room.host_color == BLACK else "⚪ 白（後手）"
            color_wait_sub = f"手番設定: <b>{h_str}</b>（ホスト指定）"

        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, rgba(238, 242, 255, 0.95), rgba(254, 243, 199, 0.9)); border: 2px solid #818cf8; border-radius: 14px; padding: 20px 24px; text-align: center; margin-bottom: 20px;">
                <div style="font-size: 20px; font-weight: 800; color: #3730a3;">
                    ⏳ 友達の接続を待機中...（合言葉: <span style="color: #4338ca; text-decoration: underline;">{room.room_id}</span>）
                </div>
                <div style="font-size: 13.5px; color: #4b5563; margin-top: 8px;">
                    同じWi-Fiに接続している友達に、下のQRコードを読み取ってもらうか、URLを開いて合言葉【<b>{room.room_id}</b>】で参加してもらってください。<br>
                    友達が参加すると自動的に対局画面へ切り替わります！<br>
                    <span style="display:inline-block; margin-top:6px; color:#4f46e5; font-weight:600;">{color_wait_sub}</span>
                </div>
                <div style="margin-top: 14px; display: inline-block;">
                    <img src="{qr_url}" alt="QR Code" style="border-radius: 10px; border: 2px solid #a5b4fc; box-shadow: 0 4px 12px rgba(99, 102, 241, 0.15);" width="160" height="160" />
                </div>
                <div style="margin-top: 10px;">
                    <span style="font-size: 12px; color: #6b7280;">接続URL: </span>
                    <span style="background: #ffffff; padding: 4px 10px; border-radius: 6px; border: 1px solid #c7d2fe; font-size: 14px; font-weight: 700; color: #312e81;">{conn_url}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        render_share_buttons(conn_url, key=f"share_url_wait_{room.room_id}", room_id=room.room_id)
        return

    # 2. 対局中または終局画面
    b_cnt, w_cnt, e_cnt = count_stones(room.game.board)
    p_black_name = room.host_name if room.host_color == BLACK else room.guest_name
    p_white_name = room.guest_name if room.host_color == BLACK else room.host_name
    is_my_turn = (room.game.current_turn == my_color and not room.game.game_over)

    # 上部ターンバナー
    if room.game.game_over:
        if b_cnt > w_cnt:
            res_msg = f"🎉 終局！ {p_black_name}（黒）の勝利です！"
        elif w_cnt > b_cnt:
            res_msg = f"🎉 終局！ {p_white_name}（白）の勝利です！"
        else:
            res_msg = "🤝 終局！ 引き分けです！"
        st.success(f"{res_msg} （黒 {b_cnt} - 白 {w_cnt} / 差 {abs(b_cnt - w_cnt)}石）")
    else:
        if is_my_turn:
            st.success(f"👉 **あなたの手番です！（{color_name(my_color)}・{my_name}）** 盤面または下の候補手ボタンをタップして着手してください")
        else:
            st.info(f"⏳ **相手（{opp_name}）が着手を考えています...**（現在の手番: {color_name(room.game.current_turn)}）")

    if room.turn_notices:
        for t_notice in room.turn_notices:
            st.info(t_notice)

    if room.pass_event_notice:
        st.warning(f"⏭️ 自動パス通知: {room.pass_event_notice}")

    col_left, col_right = st.columns([1.15, 1.0])
    with col_left:
        st.subheader("🟩 8×8 オセロ対戦盤")
        board_payload_json = build_animated_board_payload(
            game=room.game,
            user_color=my_color,
            last_animated_ply=st.session_state.get(f"onl_last_anim_{room.room_id}", 0),
            # 対局ごとに変わる番号（room.version は1手ごとに増えるので使わない。使うと盤面が自分の手を
            # 「別の対局」とみなし、打った手のアニメーションを打つ前の盤面から2回目として流し直してしまう）
            game_id=id(room.game) % 1_000_000_007,
            cell_size=55,
            match_mode="online",
            p1_name=p_black_name,
            p2_name=p_white_name,
        )
        st.session_state[f"onl_last_anim_{room.room_id}"] = len(room.game.history)

        comp_res = OTHELLO_BOARD_COMPONENT(
            data=board_payload_json,
            on_clicked_change=lambda: None,
            key=f"online_board_{room.room_id}",
        )
        if comp_res and getattr(comp_res, "clicked", None):
            clicked_coord = str_to_coord(str(comp_res.clicked))
            if clicked_coord and is_my_turn:
                hesitated = st.session_state.get(f"onl_hesitated_pills_{len(room.game.history)}", [])
                ok, msg = room_manager.apply_move(room.room_id, my_color, clicked_coord[0], clicked_coord[1], hesitated_moves=hesitated)
                if ok:
                    st.rerun()

        # 盤面のすぐ真下に「迷い手メモ」と「クイック着手ボタン」
        legal_moves = get_legal_moves(room.game.board, room.game.current_turn)
        if is_my_turn and legal_moves:
            legal_list = sorted(legal_moves.keys(), key=lambda rc: coord_to_str(*rc))
            candidate_labels = [coord_to_str(*rc) for rc in legal_list]
            cur_ply_num = len(room.game.history)

            st.markdown(
                """
                <div style="background: rgba(238, 242, 255, 0.85); border: 1.5px dashed #818cf8; border-radius: 10px; padding: 10px 14px; margin-top: 10px; margin-bottom: 6px;">
                    <div style="font-size: 13.5px; font-weight: 700; color: #3730a3;">
                        🤔 迷い手メモ（盤面直下で選択・終局時にAIが正解を徹底判定！）
                    </div>
                    <div style="font-size: 12px; color: #4c5175; margin-top: 2px;">
                        迷っている候補手をタップして選択した状態で着手すると、終局後にAIコーチが判定してくれます。
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            selected_pills = st.pills(
                "迷っている候補手をマーク（複数選択可）",
                options=candidate_labels,
                selection_mode="multi",
                key=f"onl_hesitated_pills_{cur_ply_num}",
                label_visibility="collapsed",
            )
            if selected_pills:
                st.info(f"💡 **「{' / '.join(selected_pills)}」で迷い中！**")

            st.markdown(f"**🎯 クイック着手ボタン（{color_name(my_color)}・{my_name}）:**")
            q_cols = st.columns(min(6, len(legal_list)))
            for idx_mv, rc in enumerate(legal_list):
                mv_label = coord_to_str(*rc)
                if q_cols[idx_mv % len(q_cols)].button(
                    f"{mv_label} ({len(legal_moves[rc])})",
                    key=f"onl_quick_{mv_label}_{cur_ply_num}",
                    width="stretch",
                ):
                    ok, msg = room_manager.apply_move(room.room_id, my_color, rc[0], rc[1], hesitated_moves=selected_pills)
                    if ok:
                        st.rerun()

    with col_right:
        st.subheader("📊 対局ステータス")
        m_b, m_w, m_e = st.columns(3)
        m_b.metric(f"⚫ 黒石 ({p_black_name})", f"{b_cnt} 石", f"{b_cnt - w_cnt:+d}")
        m_w.metric(f"⚪ 白石 ({p_white_name})", f"{w_cnt} 石", f"{w_cnt - b_cnt:+d}")
        m_e.metric("進行 / 残り", f"{len(room.game.history)} 手", f"{e_cnt} マス")

        st.markdown("##### 📝 棋譜 (Kifu)")
        st.code(room.game.get_kifu_string() or "(まだ着手はありません)", language="text")

        if not room.game.game_over and len(room.game.history) >= 4:
            if st.button("🏳️ 途中終了してAI解析へ進む", key="btn_onl_resign", width="stretch"):
                room_manager.resign_room(room.room_id, my_color)
                st.rerun()

        if room.game.game_over:
            c_re1, c_re2, c_re3 = st.columns([1.1, 1.1, 0.9])
            with c_re1:
                if st.button("🔄 再戦 (先後交代)", key="btn_onl_rematch_swap", width="stretch", type="primary"):
                    room_manager.restart_room(room.room_id, mode="swap")
                    st.rerun()
            with c_re2:
                if st.button("🎲 再戦 (ランダム手番)", key="btn_onl_rematch_random", width="stretch"):
                    room_manager.restart_room(room.room_id, mode="random")
                    st.rerun()
            with c_re3:
                if st.button("🚪 退出", key="btn_onl_leave_fin", width="stretch"):
                    st.session_state.online_room_id = ""
                    st.rerun()

    # ゲストの画面では解析・保存・レーティングを出さない（対局記録はオーナーの視点で1件だけ残す）
    if room.game.game_over and room.game.history and not can_save:
        st.divider()
        st.info(f"🎉 対局が終わりました！ 対局の解析と記録はホスト（{room.host_name}）の画面で行われます。上の「🔄 再戦」でもう一局遊べます。")
        return

    # 終局後のAIコーチング解析
    if room.game.game_over and room.game.history:
        st.divider()
        st.header("🧠 終局後の自動解析 ＆ Gemini AIコーチング")
        # 未保存なら解析を別スレッドで走らせる（2台の画面が同時に終局を検知しても1件だけ保存されるよう、先に解析権を確保する）
        # この画面は1秒ごとに更新されるので、終わったら結果を反映して棋譜再生タブを開く
        if not room.saved_db_id:
            onl_kifu = room.game.get_kifu_string()
            onl_job_id = f"online-{room.room_id}-{hashlib.sha1(onl_kifu.encode('utf-8')).hexdigest()[:12]}"
            onl_job = bg_jobs.get_job(onl_job_id)
            if onl_job is None and room_manager.claim_analysis(room.room_id):
                onl_job = bg_jobs.start_job(
                    onl_job_id,
                    _online_postgame_compute,
                    history=list(room.game.history),
                    user_color=my_color,
                    analyzer=engine,
                    blunder_threshold=blunder_threshold,
                    api_key=api_key_input,
                    black_score=b_cnt,
                    white_score=w_cnt,
                    kifu_str=onl_kifu,
                    engine_name=f"Wi-Fi対戦 ({p_black_name} vs {p_white_name})",
                    dilemmas_json=json.dumps(room.dilemmas, ensure_ascii=False),
                    interrupted=not is_game_over(room.game.board),
                )
            if onl_job is None or onl_job["status"] == "running":
                if onl_job is not None:
                    st.markdown(_job_progress_html(onl_job, "対局を解析中です"), unsafe_allow_html=True)
                else:
                    st.info("⏳ 対局を解析中です。終わると自動で表示されます。")
                return
            if onl_job["status"] == "error":
                st.error(f"解析中にエラーが起きました: {onl_job['error']}")
                if st.button("🔁 もう一度解析する", key=f"btn_retry_{onl_job_id}"):
                    bg_jobs.forget_job(onl_job_id)
                    room_manager.release_analysis(room.room_id)
                    st.rerun()
                return
            res = onl_job["result"]
            bg_jobs.forget_job(onl_job_id)
            room.saved_db_id = res["record_id"]
            st.session_state.analyses = res["analyses"]
            st.session_state.coach_summary = res["summary"]
            request_review_jump(res["record_id"], res["plies"])
            st.rerun(scope="app")

        if can_save and room.saved_db_id:
            if st.button("📖 振り返る（グラフ＋盤面で1手ずつ）", key=f"btn_open_review_room_{room.room_id}", type="primary", width="stretch"):
                request_review_jump(room.saved_db_id, len(room.game.history))
                st.rerun(scope="app")

        # レーティング診断カード
        all_recs = list_match_records(limit=200)
        rated_chron = compute_sequential_ratings(all_recs)
        cur_rated = rated_chron[-1] if rated_chron else {}
        cur_rating = cur_rated.get("rating_after", 1200)
        cur_delta = cur_rated.get("rating_delta", 0)
        cur_perf = cur_rated.get("perf_rating", 1200)
        cur_rank_name = cur_rated.get("rank_name", "🌿 初中級")
        cur_rank_color = cur_rated.get("rank_color", "#06b6d4")
        cur_rank_desc = cur_rated.get("rank_desc", "")
        delta_str = f"+{cur_delta} pt 📈" if cur_delta > 0 else (f"{cur_delta} pt 📉" if cur_delta < 0 else "±0 pt")
        delta_color = "#10b981" if cur_delta > 0 else ("#ef4444" if cur_delta < 0 else "#6b7280")

        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, rgba(238, 242, 255, 0.95), rgba(254, 243, 199, 0.85)); border: 2px solid #818cf8; border-radius: 14px; padding: 16px 20px; margin-top: 14px; margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                    <div style="font-size: 16px; font-weight: 800; color: #1e1b4b;">
                        🏆 Wi-Fi対戦 棋力レーティング診断 ＆ 段級位カルテ
                    </div>
                    <div style="background: {cur_rank_color}; color: #ffffff; padding: 4px 14px; border-radius: 9999px; font-size: 13px; font-weight: 700;">
                        称号: {cur_rank_name}
                    </div>
                </div>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; margin-top: 12px;">
                    <div style="background: #ffffff; padding: 10px 14px; border-radius: 10px; border: 1px solid #e0e7ff;">
                        <div style="font-size: 11px; color: #6b7280; font-weight: 600;">現在の推定レート</div>
                        <div style="font-size: 22px; font-weight: 900; color: #3730a3; margin-top: 2px;">{cur_rating} <span style="font-size: 13px; color: {delta_color}; font-weight: 700;">({delta_str})</span></div>
                    </div>
                    <div style="background: #ffffff; padding: 10px 14px; border-radius: 10px; border: 1px solid #e0e7ff;">
                        <div style="font-size: 11px; color: #6b7280; font-weight: 600;">今局のパフォーマンス</div>
                        <div style="font-size: 22px; font-weight: 900; color: #059669; margin-top: 2px;">{cur_perf} pt</div>
                    </div>
                    <div style="background: #ffffff; padding: 10px 14px; border-radius: 10px; border: 1px solid #e0e7ff;">
                        <div style="font-size: 11px; color: #6b7280; font-weight: 600;">対戦相手</div>
                        <div style="font-size: 14px; font-weight: 800; color: #1f2937; margin-top: 6px;">{opp_name}</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_online_match_section(
    engine: OthelloEngine,
    blunder_threshold: float,
    api_key_input: str,
) -> None:
    curr_room_id = st.session_state.get("online_room_id", "")
    room = room_manager.get_room(curr_room_id) if curr_room_id else None
    if curr_room_id and room is None:
        st.session_state.online_room_id = ""
        curr_room_id = ""

    if not curr_room_id:
        lan_ip = get_lan_ip()
        conn_url = get_connection_url(_server_port())
        qr_url = "https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=" + urllib.parse.quote(conn_url)

        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, rgba(238, 242, 255, 0.95), rgba(240, 253, 250, 0.95)); border: 2px solid #818cf8; border-radius: 14px; padding: 16px 20px; margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 14px;">
                    <div style="flex: 1; min-width: 260px;">
                        <div style="font-size: 17px; font-weight: 800; color: #1e1b4b;">
                            📶 同一Wi-Fiオンライン対戦ガイド（2台のスマホ / PCで対戦）
                        </div>
                        <div style="font-size: 12.5px; color: #374151; margin-top: 6px; line-height: 1.6;">
                            ① 友達のスマホやPCを<b>同じWi-Fiルーター（またはスマホのテザリング）</b>に接続します。<br>
                            ② 友達のスマホカメラで右の<b>QRコードを読み取る</b>か、下のURLを開いてもらいます。<br>
                            ③ ホストが「部屋を作成」し、友達が「合言葉」を入力（または下の待機部屋からタップ）すると、2台の端末でリアルタイムに対局がスタートします！
                        </div>
                        <div style="margin-top: 10px;">
                            <span style="font-size: 11.5px; color: #4f46e5; font-weight: 700;">友達に伝えるURL:</span><br>
                            <span style="background: #ffffff; padding: 6px 14px; border-radius: 8px; border: 1.5px solid #818cf8; font-size: 17px; font-weight: 900; color: #312e81; display: inline-block; margin-top: 4px;">{conn_url}</span>
                        </div>
                    </div>
                    <div style="text-align: center;">
                        <div style="font-size: 11px; font-weight: 700; color: #6366f1; margin-bottom: 4px;">📷 スマホカメラでスキャン</div>
                        <img src="{qr_url}" alt="QR Code" style="border-radius: 10px; border: 2px solid #818cf8; box-shadow: 0 4px 10px rgba(99, 102, 241, 0.15);" width="130" height="130" />
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        render_share_buttons(conn_url, key="share_url_lobby")
        st.caption("💡 部屋を作ると、合言葉入りの招待URL（例: …/?room=777）とQRコードが出ます。友達はそれを開くだけで部屋に入れます。")

        # 画面を更新して部屋から外れてしまったとき、自分が作った対局中の部屋にホストとして戻れるようにする
        playing_rooms = [r for r in room_manager.list_rooms() if r["status"] != "waiting"]
        for pr in playing_rooms:
            if st.button(
                f"🔁 部屋【{pr['room_id']}】（{pr['host_name']} vs {pr['guest_name'] or '-'}）にホストとして戻る",
                key=f"btn_owner_rejoin_{pr['room_id']}",
                width="stretch",
            ):
                st.session_state.online_room_id = pr["room_id"]
                st.session_state.online_player_role = "host"
                st.session_state.online_player_name = pr["host_name"]
                st.rerun()

        active_rooms = [r for r in room_manager.list_rooms() if r["status"] == "waiting"]
        if active_rooms:
            st.markdown("##### 🟢 現在待機中の部屋（ワンタップで参加可能）:")
            for ar in active_rooms:
                c_info, c_btn = st.columns([3, 1])
                c_col = ar.get("color_label", "🎲 ランダム")
                c_info.info(f"🏠 部屋合言葉: **【{ar['room_id']}】** ｜ ホスト: **{ar['host_name']}**（手番: {c_col}） ｜ 待機開始: {ar['created_at']}")
                if c_btn.button(f"🚪 部屋【{ar['room_id']}】に入る", key=f"btn_quick_join_{ar['room_id']}", width="stretch", type="primary"):
                    # ゲスト名は「部屋に入る」タブの名前欄を使う（初期値「あなた」のままだと両者が同名になる）
                    quick_name = (st.session_state.get("join_guest_name_field") or "友達").strip() or "友達"
                    ok, msg, joined = room_manager.join_room(ar['room_id'], quick_name)
                    if ok and joined:
                        st.session_state.online_room_id = joined.room_id
                        st.session_state.online_player_role = "guest"
                        st.session_state.online_player_name = quick_name
                        st.rerun()

        tab_create, tab_join = st.tabs(["🏠 部屋を作る (ホスト)", "🚪 部屋に入る (ゲスト)"])
        with tab_create:
            col_c1, col_c2, col_c3 = st.columns([1.5, 1.5, 1.5])
            with col_c1:
                r_code = st.text_input("部屋の合言葉（部屋番号）", value="777", key="create_room_id_field")
            with col_c2:
                h_name = st.text_input("あなたの名前", value=st.session_state.get("online_player_name", "あなた"), key="create_host_name_field")
            with col_c3:
                h_col = st.radio(
                    "あなたの手番",
                    ["🎲 ランダム (おまかせ)", "⚫ 黒（先手）", "⚪ 白（後手）"],
                    key="create_host_col_radio",
                    index=0,
                )

            if st.button("✨ この設定で部屋を作成して待機する", key="btn_create_room_action", width="stretch", type="primary"):
                if "ランダム" in h_col:
                    color_val = BLACK
                    c_mode = "random"
                elif "黒" in h_col:
                    color_val = BLACK
                    c_mode = "manual"
                else:
                    color_val = WHITE
                    c_mode = "manual"
                new_room = room_manager.create_room(r_code, h_name, color_val, color_mode=c_mode)
                st.session_state.online_room_id = new_room.room_id
                st.session_state.online_player_role = "host"
                st.session_state.online_player_name = h_name
                st.rerun()

        with tab_join:
            col_j1, col_j2 = st.columns(2)
            with col_j1:
                default_j = active_rooms[0]["room_id"] if active_rooms else "777"
                j_code = st.text_input("参加する合言葉（部屋番号）", value=default_j, key="join_room_id_field")
            with col_j2:
                g_name = st.text_input("あなたの名前", value="友達", key="join_guest_name_field")

            if st.button("🚀 部屋に参加して対戦開始", key="btn_join_room_action", width="stretch", type="primary"):
                ok, msg, joined = room_manager.join_room(j_code, g_name)
                if ok and joined:
                    st.session_state.online_room_id = joined.room_id
                    st.session_state.online_player_role = "guest"
                    st.session_state.online_player_name = g_name
                    st.rerun()
                else:
                    st.error(msg)
    else:
        # 部屋入室中
        col_hdr1, col_hdr2 = st.columns([3, 1])
        with col_hdr1:
            my_role = st.session_state.get("online_player_role", "host")
            if room.status == "waiting" and room.color_mode == "random":
                my_c_name = "🎲 ランダム（相手参加時に決定）"
            else:
                my_c_name = "⚫ 黒（先手）" if (room.host_color == BLACK if my_role == "host" else room.guest_color == BLACK) else "⚪ 白（後手）"
            st.markdown(f"### 📡 Wi-Fi対戦部屋 【合言葉: `{room.room_id}`】")
            st.caption(f"プレイヤー: **{st.session_state.get('online_player_name', 'あなた')}**（{my_c_name}）")
        with col_hdr2:
            st.write("")
            if st.button("🚪 部屋を退出する", key="btn_exit_online_room", width="stretch"):
                st.session_state.online_room_id = ""
                st.rerun()

        render_online_room_fragment(room.room_id, engine, blunder_threshold, api_key_input)


# ================= オーナー（このアプリの持ち主）とゲスト（Wi-Fi対戦の友達）の判定 =================
OWNER_AUTH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "owner_auth.json")


def _normalize_ip(ip: Optional[str]) -> Optional[str]:
    """'::ffff:192.168.1.2' のような IPv4 埋め込み表記を '192.168.1.2' にそろえる"""
    if ip and ip.startswith("::ffff:"):
        return ip[len("::ffff:"):]
    return ip


# 中継サーバー（nginx などのリバースプロキシ）を通ったアクセスに付くヘッダー
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host", "cf-connecting-ip", "true-client-ip")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def auto_owner_enabled() -> bool:
    """
    このPCから開いたら自動でオーナーにするか。サーバーに置いて公開するときは、環境変数
    OTHELLO_AUTO_OWNER=0 で切る（オーナーはパスワードでログインする）
    """
    return os.environ.get("OTHELLO_AUTO_OWNER", "1").strip().lower() not in ("0", "false", "no", "off")


def _is_local_request(ip: Optional[str], url_host: str, headers: Dict[str, str], lan_ip: str) -> bool:
    """
    アクセス元の情報から、アプリを動かしているPC自身からのアクセスかを判定する（st.* に触らないのでテストできる）。
    - 中継サーバーを通ったアクセスは、手元のPCからでも違うとみなす。中継サーバーからの接続は
      アクセス元が 127.0.0.1 に見えるので、IP だけで判定すると、サイトを開いた人が全員オーナーになってしまうため
    - Host（開いたアドレス）が localhost・127.0.0.1・このPCの LAN IP 以外（ドメイン名など）なら違うとみなす
    """
    lower = {k.lower(): v for k, v in (headers or {}).items()}
    if any(h in lower for h in PROXY_HEADERS):
        return False
    allowed_hosts = set(LOCAL_HOSTS) | {lan_ip}
    host_header = lower.get("host", "")
    if host_header:
        # 'localhost:5000' → 'localhost'、'[::1]:5000' → '::1'
        name = host_header.rsplit(":", 1)[0] if not host_header.startswith("[") else host_header[1:].split("]", 1)[0]
        if name not in allowed_hosts:
            return False
    if ip is None:
        # Streamlit は localhost からの接続では IP を None にする
        return url_host in LOCAL_HOSTS
    return ip in ("127.0.0.1", "::1") or ip == lan_ip


def is_local_client() -> bool:
    """アプリを動かしているPC自身からのアクセスか（localhost、または自分のLAN IPで開いた場合）"""
    if not auto_owner_enabled():
        return False
    try:
        ip = _normalize_ip(st.context.ip_address)
        url_host = urllib.parse.urlparse(st.context.url or "").hostname or ""
        headers = dict(st.context.headers or {})
    except Exception:
        return False
    return _is_local_request(ip, url_host, headers, get_lan_ip())


def _load_owner_auth() -> Optional[Dict[str, str]]:
    try:
        with open(OWNER_AUTH_PATH, encoding="utf-8") as fp:
            data = json.load(fp)
        return data if data.get("salt") and data.get("hash") else None
    except (OSError, ValueError):
        return None


def set_owner_password(password: str, username: str) -> None:
    """オーナーのユーザー名と、ログイン用パスワードのソルト付きハッシュ（PBKDF2）を保存する（平文は保存しない）"""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    # sessions（記憶済みのログイン）は書き直しで消える＝パスワードを変えると他の端末は全部ログアウトになる
    _save_owner_auth({"username": username.strip(), "salt": salt.hex(), "hash": digest.hex(), "sessions": []})


def owner_username() -> str:
    """オーナー名（未設定なら空文字）。Wi-Fi対戦でホストになったときの既定の名前にも使う"""
    auth = _load_owner_auth()
    return (auth or {}).get("username", "")


def check_owner_password(password: str, username: str) -> bool:
    auth = _load_owner_auth()
    if not auth or not password:
        return False
    # ユーザー名は大文字・小文字と前後の空白を区別しない
    if auth.get("username") and username.strip().lower() != auth["username"].strip().lower():
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(auth["salt"]), 200_000)
    return hmac.compare_digest(digest.hex(), auth["hash"])


OWNER_COOKIE = "othello_owner"
REMEMBER_DAYS = 90


def _save_owner_auth(data: Dict[str, Any]) -> None:
    with open(OWNER_AUTH_PATH, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_owner_token() -> str:
    """ログインを記憶するためのトークンを発行する（サーバーにはハッシュだけ保存し、期限切れは掃除する）"""
    auth = _load_owner_auth() or {}
    token = secrets.token_urlsafe(32)
    now = time.time()
    sessions = [x for x in auth.get("sessions", []) if x.get("exp", 0) > now]
    sessions.append({"h": _token_hash(token), "exp": now + REMEMBER_DAYS * 86400})
    auth["sessions"] = sessions[-20:]
    _save_owner_auth(auth)
    return token


def owner_token_valid(token: str) -> bool:
    auth = _load_owner_auth()
    if not auth or not token:
        return False
    h = _token_hash(token)
    now = time.time()
    return any(hmac.compare_digest(x.get("h", ""), h) and x.get("exp", 0) > now for x in auth.get("sessions", []))


def revoke_owner_token(token: str) -> None:
    auth = _load_owner_auth()
    if not auth or not token:
        return
    h = _token_hash(token)
    auth["sessions"] = [x for x in auth.get("sessions", []) if x.get("h") != h]
    _save_owner_auth(auth)


# ブラウザに Cookie を書く小さなコンポーネント（Streamlit の Python 側からは Cookie を設定できないため）
COOKIE_WRITER_COMPONENT = st.components.v2.component(
    "owner_cookie_writer",
    html="<div></div>",
    js="""
    export default function(component) {
        const { data } = component;
        if (!data) return;
        let p = null;
        try {
            p = typeof data === 'string' ? JSON.parse(data) : data;
        } catch (e) {
            return;
        }
        if (!p || !p.name) return;
        document.cookie = [
            p.name + '=' + encodeURIComponent(p.value || ''),
            'path=/',
            'SameSite=Lax',
            'max-age=' + (p.max_age || 0),
        ].join('; ');
    }
    """,
)


def _write_owner_cookie(value: str, max_age: int, key: str) -> None:
    COOKIE_WRITER_COMPONENT(
        data=json.dumps({"name": OWNER_COOKIE, "value": value, "max_age": max_age}),
        key=key,
    )


def is_owner_session() -> bool:
    """
    オーナー用の全機能ページを出すか。
    - このPCから開いた場合は自動でオーナー（?view=guest を付けるとゲスト画面を確認できる）
    - 別の端末（自分のスマホ等）はゲスト画面の「オーナーログイン」でパスワードを入れるとオーナーになる
    """
    # パスワードでログイン済みなら、URL に ?view=guest が残っていてもオーナー画面にする
    if st.session_state.get("owner_login", False):
        return True
    # このブラウザでログインを記憶している（Cookie のトークンが有効）
    try:
        token = (st.context.cookies or {}).get(OWNER_COOKIE, "")
    except Exception:
        token = ""
    if token and owner_token_valid(token):
        st.session_state.owner_login = True
        st.session_state.owner_token = token
        return True
    if st.query_params.get("view") == "guest":
        return False
    return is_local_client()


def room_invite_url(room_id: str) -> str:
    """合言葉入りの招待URL（開くとゲスト画面でその部屋が表示される）"""
    return f"{get_connection_url(_server_port())}/?room={urllib.parse.quote(room_id)}"


def render_owner_settings() -> None:
    """サイドバー: 自分のスマホなど別端末からオーナーとして使うためのパスワード設定・ログアウト"""
    with st.expander("🔑 オーナーログイン設定（自分のスマホから使う用）", expanded=False):
        if is_local_client():
            st.caption(
                "このPCから開くと自動でこの画面（オーナー用）になります。友達がURLを開くと、対局だけができるゲスト用の画面になります。"
                "自分のスマホなど別の端末でオーナー画面を使いたいときは、ここでパスワードを決めておき、"
                "ゲスト画面の下にある「オーナーとしてログイン」に入力してください。"
            )
            st.caption(
                "現在: " + (f"✅ 設定済み（オーナー名: {owner_username() or '未設定'}）" if _load_owner_auth() else "⚠️ パスワード未設定")
            )
            with st.form("owner_pw_form", clear_on_submit=True):
                name_new = st.text_input("オーナー名", value=owner_username() or "Keitaro", key="owner_name_new")
                pw1 = st.text_input("新しいパスワード（4文字以上）", type="password", key="owner_pw_new", autocomplete="new-password")
                pw2 = st.text_input("もう一度入力", type="password", key="owner_pw_new2", autocomplete="new-password")
                save_clicked = st.form_submit_button("💾 オーナー名とパスワードを保存", width="stretch")
            if save_clicked:
                if not name_new.strip():
                    st.error("オーナー名を入力してください。")
                elif len(pw1) < 4:
                    st.error("4文字以上で入力してください。")
                elif pw1 != pw2:
                    st.error("2つの入力が一致しません。")
                else:
                    set_owner_password(pw1, name_new)
                    st.success("保存しました。別の端末からはこのパスワードでログインできます。")
        else:
            st.caption("別の端末からオーナーとしてログイン中です。")
            if st.button("🚪 ログアウト（ゲスト画面に戻る）", key="owner_logout", width="stretch"):
                revoke_owner_token(st.session_state.get("owner_token", ""))
                st.session_state.owner_login = False
                st.session_state.owner_token = ""
                st.session_state._owner_cookie_clear = True
                st.rerun()


def render_owner_login_form() -> None:
    """ゲスト画面の下: 持ち主が自分のスマホからオーナー画面に入るためのログイン"""
    with st.expander("🔑 このアプリの持ち主の方はこちら（オーナーとしてログイン）", expanded=False):
        if not _load_owner_auth():
            st.caption("オーナー用パスワードがまだ設定されていません。アプリを動かしているPCで、サイドバーの「🔑 オーナーログイン設定」から設定してください。")
            return
        # フォームにすると、入力欄を確定（Enter・フォーカス移動）していなくても送信時に全項目がまとめて送られる
        with st.form("owner_login_form", clear_on_submit=False):
            name = st.text_input("オーナー名", key="owner_login_name", autocomplete="username")
            pw = st.text_input("パスワード", type="password", key="owner_login_pw", autocomplete="current-password")
            remember = st.checkbox(f"このブラウザでログインを記憶する（{REMEMBER_DAYS}日間）", value=True, key="owner_login_remember")
            submitted = st.form_submit_button("ログイン")
        if submitted:
            if check_owner_password(pw, name):
                st.session_state.owner_login = True
                if remember:
                    token = issue_owner_token()
                    st.session_state.owner_token = token
                    st.session_state._owner_cookie_pending = token
                if "view" in st.query_params:
                    del st.query_params["view"]
                st.rerun()
            else:
                time.sleep(1.0)  # 総当たりを遅らせる
                st.error("オーナー名またはパスワードが違います。")


def _join_room_as_guest(room_id: str, name: str) -> None:
    ok, msg, joined = room_manager.join_room(room_id, name)
    if ok and joined:
        st.session_state.online_room_id = joined.room_id
        st.session_state.online_player_role = "guest"
        st.session_state.online_player_name = name.strip() or "ゲスト"
        st.query_params["room"] = joined.room_id
        st.rerun()
    else:
        st.error(msg)


def render_guest_page() -> None:
    """友達（ゲスト）用ページ: 招待された部屋に入って対局するだけの画面。設定・履歴・解析タブは出さない"""
    st.markdown(
        "<style>section[data-testid='stSidebar'], [data-testid='stSidebarCollapsedControl'], "
        "[data-testid='stExpandSidebarButton'] {display: none !important;}</style>",
        unsafe_allow_html=True,
    )
    st.session_state.match_mode = "online"
    if st.session_state.pop("_owner_cookie_clear", False):
        _write_owner_cookie("", 0, key="owner_cookie_clear")
    st.title("⚫⚪ オセロ Wi-Fi対戦")

    room_id = st.session_state.get("online_room_id", "")
    room = room_manager.get_room(room_id) if room_id else None
    if room_id and room is None:
        st.session_state.online_room_id = ""
        room = None

    if room is not None:
        # 入室中: 対局画面
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"### 📡 部屋【{room.room_id}】 ／ ホスト: {room.host_name}")
            st.caption(f"あなた: **{st.session_state.get('online_player_name', 'ゲスト')}**")
        with c2:
            st.write("")
            if st.button("🚪 部屋を退出する", key="guest_exit_room", width="stretch"):
                st.session_state.online_room_id = ""
                st.rerun()
        render_online_room_fragment(room.room_id, None, -4.0, "", can_save=False)
        render_owner_login_form()
        return

    st.caption("友達に招待されたオセロの対局ページです。部屋に入ると、すぐに対局が始まります。")
    invited = (st.query_params.get("room") or "").strip()
    inv_room = room_manager.get_room(invited) if invited else None
    show_manual = True

    if invited and inv_room is not None and inv_room.status == "waiting":
        show_manual = False
        if inv_room.color_mode == "random":
            color_note = "黒・白は参加した瞬間にコイントスで決まります。"
        else:
            color_note = "あなたは " + ("⚪ 白（後手）" if inv_room.host_color == BLACK else "⚫ 黒（先手）") + " です。"
        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, rgba(238, 242, 255, 0.95), rgba(240, 253, 250, 0.95)); border: 2px solid #818cf8; border-radius: 14px; padding: 18px 22px; margin: 10px 0 16px 0;">
                <div style="font-size: 20px; font-weight: 800; color: #1e1b4b;">🎮 {inv_room.host_name} さんの部屋【{invited}】に招待されています</div>
                <div style="font-size: 14px; color: #374151; margin-top: 6px;">名前を入れて「参加する」を押すと対局が始まります。{color_note}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.form("guest_join_invited_form"):
            name = st.text_input("あなたの名前", value="友達", key="guest_name_invited")
            go_join = st.form_submit_button(f"🚪 部屋【{invited}】に参加して対局を始める", type="primary", width="stretch")
        if go_join:
            _join_room_as_guest(invited, name)
    elif invited and inv_room is not None:
        st.warning(f"部屋【{invited}】はすでに対局中です（{inv_room.host_name} vs {inv_room.guest_name}）。")
        if inv_room.guest_name:
            st.caption("画面を更新して対局から外れてしまった場合は、元のプレイヤーとして戻れます。")
            if st.button(f"🔁 {inv_room.guest_name} として対局に戻る", key="guest_rejoin", width="stretch"):
                st.session_state.online_room_id = inv_room.room_id
                st.session_state.online_player_role = "guest"
                st.session_state.online_player_name = inv_room.guest_name
                st.rerun()
    elif invited:
        st.warning(
            f"合言葉『{invited}』の部屋が見つかりません。ホストがまだ部屋を作っていないか、URLが古い可能性があります。"
            "ホストに部屋を作ってもらってから、もう一度URLを開いてください。"
        )

    if show_manual:
        with st.form("guest_join_manual_form"):
            st.markdown("#### 🔑 合言葉を入力して参加")
            g1, g2 = st.columns(2)
            with g1:
                code = st.text_input("合言葉（部屋番号）", value=invited, key="guest_code_manual")
            with g2:
                name_m = st.text_input("あなたの名前", value="友達", key="guest_name_manual")
            go_manual = st.form_submit_button("🚪 参加する", type="primary", width="stretch")
        if go_manual:
            _join_room_as_guest(code, name_m)
        waiting = [r for r in room_manager.list_rooms() if r["status"] == "waiting"]
        if waiting:
            st.markdown("##### 🟢 いま参加できる部屋")
            for ar in waiting:
                w1, w2 = st.columns([3, 1])
                w1.info(f"部屋【{ar['room_id']}】 ／ ホスト: {ar['host_name']}（手番: {ar['color_label']}）")
                if w2.button(f"🚪 【{ar['room_id']}】に入る", key=f"guest_quick_{ar['room_id']}", width="stretch"):
                    _join_room_as_guest(ar["room_id"], st.session_state.get("guest_name_manual") or "友達")
    else:
        with st.expander("別の合言葉で参加する", expanded=False):
            with st.form("guest_join_other_form"):
                code2 = st.text_input("合言葉（部屋番号）", key="guest_code_other")
                name2 = st.text_input("あなたの名前", value="友達", key="guest_name_other")
                go_other = st.form_submit_button("🚪 参加する", width="stretch")
            if go_other:
                _join_room_as_guest(code2, name2)

    render_owner_login_form()


def main() -> None:
    init_session_state()

    # 友達（ゲスト）には対局用の画面だけを出す。このPC・ログイン済みの持ち主は全機能
    if not is_owner_session():
        render_guest_page()
        return
    pending_token = st.session_state.pop("_owner_cookie_pending", None)
    if pending_token:
        _write_owner_cookie(pending_token, REMEMBER_DAYS * 86400, key="owner_cookie_set")

    # ================= サイドバー設定 =================
    # ラジオボタンのコールバック（次回実行の冒頭で呼ばれる）から、その時点のエンジン設定を参照するための入れ物
    engine_holder: Dict[str, OthelloEngine] = {}
    with st.sidebar:
        st.title("⚙️ 対局＆解析設定")

        st.subheader("1. プレイヤー・エンジン設定")
        match_mode_side = st.session_state.get("match_mode", "ai")
        if match_mode_side == "ai":
            st.radio(
                "あなたの手番（石の色）",
                options=[COLOR_LABELS[BLACK], COLOR_LABELS[WHITE]],
                key=COLOR_RADIO_KEY,
                on_change=on_color_radio_change,
                args=(engine_holder,),
            )
            chosen_user_color = st.session_state.user_color
        elif match_mode_side == "online":
            r_id = st.session_state.get("online_room_id", "")
            r_str = f"部屋: **【{r_id}】**" if r_id else "ロビー待機中"
            p_name = st.session_state.get("online_player_name", "あなた")
            p_role = "ホスト" if st.session_state.get("online_player_role", "host") == "host" else "ゲスト"
            st.success(f"🌐 **Wi-Fiオンライン対戦モード**\n\n{r_str}\n\nプレイヤー: **{p_name}**（{p_role}）\n\n（タブ1上部で合言葉やQRコードを確認できます）")
            chosen_user_color = BLACK
        else:
            p1_s = st.session_state.get("pvp_p1_name", "あなた")
            p2_s = st.session_state.get("pvp_p2_name", "対戦相手")
            st.info(f"👥 **対人戦モード実行中**\n\n⚫ **{p1_s}** vs ⚪ **{p2_s}**\n\n（タブ1の画面上部でモード切替や名前の変更が可能です）")
            chosen_user_color = BLACK

        DIFFICULTY_PRESETS = [
            "🐣 入門 (超初級・ひよこ)",
            "🌱 初級 (ルーキー)",
            "🌿 初中級 (アマ級位)",
            "⚔️ 中級 (アマ初段)",
            "🥋 上級 (Egaroucid Lv.2)",
            "🔥 強豪 (Egaroucid Lv.3)",
            "🌟 有段 (Egaroucid Lv.4)",
            "👑 達人 (Egaroucid Lv.5)",
            "⚙️ カスタム (手動Lv指定)",
        ]
        cur_preset = st.session_state.get("ai_difficulty_preset", "🐣 入門 (超初級・ひよこ)")
        if cur_preset not in DIFFICULTY_PRESETS:
            cur_preset = "🐣 入門 (超初級・ひよこ)"

        selected_preset = st.selectbox(
            "🎯 AI難易度プリセット",
            options=DIFFICULTY_PRESETS,
            key="sidebar_diff_preset",
            on_change=on_sidebar_diff_change,
            args=(engine_holder,),
        )

        cli_path = DEFAULT_EGAROUCID_PATH
        if selected_preset == "🐣 入門 (超初級・ひよこ)":
            engine_mode = "builtin"
            engine_level = 0
            st.caption("🐣 初心者でも楽しく勝てる接待AI（角を譲ったりミス多め・最多石取りグリーディー）")
        elif selected_preset == "🌱 初級 (ルーキー)":
            engine_mode = "builtin"
            engine_level = 1
            st.caption("🌱 初級AI（単純な石取り重視。中割りを意識すればしっかり勝てる難易度）")
        elif selected_preset == "🌿 初中級 (アマ級位)":
            engine_mode = "builtin"
            engine_level = 2
            st.caption("🌿 初中級AI（1手読み＋開放度ペナルティ。角の攻防を楽しめる）")
        elif selected_preset == "⚔️ 中級 (アマ初段)":
            engine_mode = "builtin"
            engine_level = 3
            st.caption("⚔️ 中級AI（AlphaBeta 2〜3手読み。手筋重視の手応えあるAI）")
        elif selected_preset == "🥋 上級 (Egaroucid Lv.2)":
            engine_mode = "cli"
            engine_level = 2
            st.caption("🥋 上級AI（本格エンジンの浅い読み。隅・辺の取り合いで手を抜かない）")
        elif selected_preset == "🔥 強豪 (Egaroucid Lv.3)":
            engine_mode = "cli"
            engine_level = 3
            st.caption("🔥 強豪AI（中盤の手数管理と終盤の読みが正確）")
        elif selected_preset == "🌟 有段 (Egaroucid Lv.4)":
            engine_mode = "cli"
            engine_level = 4
            st.caption("🌟 有段AI（定石と中盤の判断がほぼ崩れない）")
        elif selected_preset == "👑 達人 (Egaroucid Lv.5)":
            engine_mode = "cli"
            engine_level = 5
            st.caption("👑 世界最強クラスの本格SIMD探索エンジン")
        else:
            engine_mode_label = st.selectbox(
                "エンジン種別",
                options=[
                    "Egaroucid for Console 7.8.1 (標準搭載・SIMD高速版)",
                    "Built-in AlphaBeta Engine (Python軽量版)",
                ],
                index=0,
            )
            engine_mode = "cli" if "Egaroucid" in engine_mode_label else "builtin"
            max_lv = 15 if engine_mode == "cli" else 4
            engine_level = st.slider(
                "エンジン思考レベル (Lv)",
                min_value=0,
                max_value=max_lv,
                value=5 if engine_mode == "cli" else 1,
            )
            if engine_mode == "cli":
                cli_path = st.text_input(
                    "Egaroucid / Edax 実行ファイルパス",
                    value=DEFAULT_EGAROUCID_PATH,
                    help="プロジェクト内の Egaroucid_for_Console_7_8_1_SIMD.exe が自動設定されています。",
                )
            st.session_state.custom_engine_mode = engine_mode
            st.session_state.custom_engine_level = engine_level
            st.session_state.custom_engine_path = cli_path

        engine = OthelloEngine(mode=engine_mode, level=engine_level, cli_path=cli_path)
        engine_holder["engine"] = engine
        engine.warm_up()
        # 対戦相手の難易度とは別に、終局後解析・局面検討用の強いエンジンを用意する
        analysis_engine = get_analysis_engine(engine)
        analysis_engine.warm_up()
        if engine.is_cli_available and engine.level >= 1:
            st.caption(f"✅ **{engine.name}** 連携中（定石Book＋高精度評価関数）")
        else:
            st.caption(f"ℹ️ **{engine.name}** で動作中")

        st.divider()
        st.subheader("2. 悪手抽出＆Gemini API設定")
        blunder_threshold = float(
            st.slider(
                "悪手 (Blunder) 判定閾値（評価値低下石数）",
                min_value=-12,
                max_value=-2,
                value=-4,
                step=1,
                help="ユーザーの着手で前回評価値よりこの石数以上悪化した手を悪手として自動抽出します。",
            )
        )

        env_api_key = os.environ.get("GEMINI_API_KEY", "")
        # ブラウザ（Chrome / Edge 等）のパスワードマネージャーがセレクトボックスをユーザー名入力欄と誤認するのを防ぐ対策
        st.html(
            """
            <div style="display:none;" aria-hidden="true">
                <input type="text" name="fake_username_trap" autocomplete="username" tabindex="-1" style="display:none;" />
                <input type="password" name="fake_password_trap" autocomplete="current-password" tabindex="-1" style="display:none;" />
            </div>
            <script>
            (function() {
                function preventPasswordManagerOnSelects() {
                    document.querySelectorAll('input[role="combobox"]').forEach(function(el) {
                        el.setAttribute('autocomplete', 'off');
                        el.setAttribute('data-lpignore', 'true');
                        el.setAttribute('data-1p-ignore', 'true');
                        el.setAttribute('data-form-type', 'other');
                    });
                }
                preventPasswordManagerOnSelects();
                if (window.MutationObserver) {
                    var obs = new MutationObserver(preventPasswordManagerOnSelects);
                    obs.observe(document.body, { childList: true, subtree: true });
                }
            })();
            </script>
            """
        )
        api_key_input = st.text_input(
            "Gemini API Key (`gemini-2.5-flash`)",
            value=env_api_key,
            type="password",
            autocomplete="new-password",
            placeholder="AIzaSy...",
            help="google-genai SDK で gemini-2.5-flash を呼び出します。未入力でも内蔵オセロ戦術ルール解説で一連の動作を確認できます。",
        )

        st.divider()
        # 手番ラジオ（COLOR_RADIO_KEY）を書き換えるため、ウィジェット生成前に走る on_click コールバックで処理する
        st.button(
            "🔄 新規対局を開始",
            width="stretch",
            type="primary",
            on_click=start_new_game,
            args=(chosen_user_color, engine),
        )
        st.button(
            "🧪 デモ棋譜で即解析",
            width="stretch",
            help="典型的な悪手を含む対局データを読み込み、終局後の自動解析・グラフ・Gemini解説・DB保存を即座にテストします。",
            on_click=load_demo_sample_game,
            args=(engine,),
        )
        st.divider()
        render_owner_settings()

    # ================= メイン画面ヘッダー =================
    st.title("⚫⚪ オセロ AIコーチ")
    st.caption("🎮 対局 → 📖 振り返り → 📚 学習 → 🎮 対局 のサイクルで強くなろう")

    _apply_review_jump()
    apply_quest_jump()
    apply_tab_jump()
    # 対局 → 振り返り → 学習 → 対局 の流れに沿った4つのタブ（nav.MAIN_TABS）。
    # key 付き・on_change="rerun" にすると、選択中のタブをコードから切り替えられる（終局後に振り返りを開くなど）
    tab_play, tab_pvp, tab_quests, tab_growth = st.tabs(MAIN_TABS, key="main_tab", on_change="rerun")

    # ================= タブ1: 対戦 ＆ AIコーチング解析 =================
    with tab_play:
        game: OthelloGame = st.session_state.game
        user_color: int = st.session_state.user_color
        match_mode = st.session_state.get("match_mode", "ai")
        p1_name = st.session_state.get("pvp_p1_name", "あなた")
        p2_name = st.session_state.get("pvp_p2_name", "対戦相手")

        # 学習 → 対局: 学習タブで選んだ「今回意識すること」
        render_focus_card()

        # ---- 対戦モード切替バー（AI対戦 ⇄ 1台で対人戦 ⇄ 🌐 Wi-Fi対戦）----
        mode_c1, mode_c2 = st.columns([1.6, 2.4])
        with mode_c1:
            chosen_mode = st.radio(
                "🎮 対戦モード切替",
                options=["ai", "pvp", "online"],
                format_func=lambda m: (
                    "🤖 AI対戦 (あなた vs AI)" if m == "ai"
                    else ("👥 対人戦 (1台で対戦)" if m == "pvp"
                    else "🌐 Wi-Fi対戦 (2台の端末)")
                ),
                index=0 if match_mode == "ai" else (1 if match_mode == "pvp" else 2),
                key="match_mode_radio_tab1",
                horizontal=True,
                # 新規対局でサイドバーの手番ラジオを書き換えるため、ウィジェット生成前に走るコールバックで処理する
                on_change=on_match_mode_change,
                args=(engine_holder,),
            )

        with mode_c2:
            if match_mode == "online":
                lan_url = get_connection_url(_server_port())
                st.markdown(
                    f"""
                    <div style="background: rgba(238,242,255,0.7); border: 1.5px dashed #818cf8; border-radius: 8px; padding: 6px 12px; font-size: 13px; color: #3730a3; margin-top: 10px;">
                        📶 <b>接続先URL:</b> <code style="font-size:14px; font-weight:800; color:#1e1b4b;">{lan_url}</code><br>
                        友達のスマホカメラでQRスキャンまたはURL入力で繋がります！
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            elif match_mode == "pvp":
                name_c1, name_c2 = st.columns(2)
                with name_c1:
                    st.session_state.setdefault("input_pvp_p1_field", p1_name)  # 先後交代では入力欄の値ごと入れ替える
                    p1_val = st.text_input("⚫ 黒プレイヤー名", key="input_pvp_p1_field")
                    if p1_val != p1_name:
                        st.session_state.pvp_p1_name = p1_val
                with name_c2:
                    st.session_state.setdefault("input_pvp_p2_field", p2_name)  # 先後交代では入力欄の値ごと入れ替える
                    p2_val = st.text_input("⚪ 白プレイヤー名", key="input_pvp_p2_field")
                    if p2_val != p2_name:
                        st.session_state.pvp_p2_name = p2_val
                # 振り返り・レーティングは本人の視点で記録するので、どちらの色を持っているかを選ぶ。
                # 選んだ色は pvp_user_color に持つ（ラジオのキーは描画されない回に消えることがあるため）
                me = owner_username() or "あなた"
                st.session_state.setdefault("pvp_user_color_radio", st.session_state.get("pvp_user_color", BLACK))
                st.radio(
                    "🙋 あなたはどっち？（振り返りと成長の記録に使います）",
                    options=[BLACK, WHITE],
                    format_func=lambda c: f"⚫ 黒（{me}）" if c == BLACK else f"⚪ 白（{me}）",
                    key="pvp_user_color_radio",
                    horizontal=True,
                    on_change=_on_pvp_user_color_change,
                )
            else:
                diff_c1, diff_c2 = st.columns([2.6, 1.4])
                with diff_c1:
                    chosen_diff = st.selectbox(
                        "🎯 AI難易度",
                        options=DIFFICULTY_PRESETS,
                        key="tab1_diff_preset_select",
                        on_change=on_tab1_diff_change,
                        args=(engine_holder,),
                    )
                    my_rating = current_user_rating()
                    if my_rating is not None:
                        rec_preset = recommended_difficulty(my_rating)
                        if st.session_state.get("diff_auto", True):
                            st.caption(f"🎯 あなたのレート {my_rating:.0f} に合わせて自動で選択中（このAIの目安 {estimate_opponent_rating(rec_preset)}）")
                            goal = difficulty_goal(my_rating)
                            if goal:
                                st.caption(f"⬆️ 次の目標：{goal['next']}（レート {goal['to']:.0f} で挑戦開始・あと {goal['to'] - my_rating:.0f}）")
                        elif rec_preset != st.session_state.get("ai_difficulty_preset"):
                            st.caption(f"手動で選択中 ／ レート {my_rating:.0f} のおすすめ：{rec_preset}")
                            st.button("🎯 おすすめに戻す", key="btn_diff_auto_restore", type="tertiary", on_click=on_diff_auto_restore, args=(engine_holder,))
                with diff_c2:
                    st.write("")
                    st.write("")
                    st.button(
                        "🆕 このAIと新局開始",
                        key="tab1_btn_new_ai_match",
                        width="stretch",
                        on_click=start_new_game,
                        args=(user_color, engine),
                    )

        if match_mode == "online":
            render_online_match_section(analysis_engine, blunder_threshold, api_key_input)
        else:
            b_cnt, w_cnt, e_cnt = count_stones(game.board)
            legal_moves = get_legal_moves(game.board, game.current_turn)
            last_move = game.history[-1].move if game.history else None
            last_move_str = game.history[-1].move_str if game.history else "-"
            if match_mode == "pvp":
                last_actor = (p1_name if game.history[-1].player == BLACK else p2_name) if game.history else "-"
            else:
                last_actor = (
                    ("あなた" if game.history[-1].player == user_color else "AI")
                    if game.history else "-"
                )

            # ---- ひと目でわかる対局状態ヘッダーバナー ----
            user_role_badge = "⚫ 黒（先手）" if user_color == BLACK else "⚪ 白（後手）"
            ai_role_badge = "⚪ 白（後手）" if user_color == BLACK else "⚫ 黒（先手）"
            curr_mover_name = (p1_name if game.current_turn == BLACK else p2_name) if match_mode == "pvp" else ("あなた" if game.current_turn == user_color else "AI")
            curr_turn_badge = "⚫ 黒（先手）" if game.current_turn == BLACK else "⚪ 白（後手）"

            interrupted = game.game_over and not is_game_over(game.board)
            if game.game_over:
                diff = (b_cnt - w_cnt) if user_color == BLACK else (w_cnt - b_cnt)
                status_bg = "linear-gradient(135deg, rgba(224, 231, 255, 0.92), rgba(252, 231, 243, 0.92))"
                status_border = "#a5b4fc"
                if interrupted:
                    status_title = f"⏹️ 【途中終了（解析モード）】 中断時点の石数：黒 {b_cnt} - 白 {w_cnt} / 差 {abs(b_cnt - w_cnt)} 石（勝敗は記録しません）"
                else:
                    if match_mode == "pvp":
                        res_word = f"{p1_name}（黒）の勝利 🎉" if b_cnt > w_cnt else (f"{p2_name}（白）の勝利 🎉" if w_cnt > b_cnt else "引き分け 🤝")
                        status_title = f"🏁 【終局（対人戦終了）】 {res_word} （最終石数：黒 {b_cnt} - 白 {w_cnt} / 石差 {abs(b_cnt - w_cnt)} 石）"
                    else:
                        res_word = "あなたの勝利 🎉" if diff > 0 else ("AIの勝利 🏁" if diff < 0 else "引き分け 🤝")
                        status_title = f"🏁 【終局（対局終了）】 {res_word} （最終石数：黒 {b_cnt} - 白 {w_cnt} / あなた視点 {diff:+d} 石）"
                status_sub = "画面下部に全手評価値グラフ・悪手(Blunder)解説・弱点アドバイスが生成されています。"
            elif len(game.history) == 0:
                status_bg = "linear-gradient(135deg, rgba(255, 255, 255, 0.92), rgba(238, 242, 255, 0.92))"
                status_border = "#c7d2fe"
                if match_mode == "pvp":
                    status_title = f"🟢 【対人戦開始前（0手目・待機中）】 ⚫ {p1_name} vs ⚪ {p2_name}"
                    status_sub = f"初手は ⚫ {p1_name}（黒）の手番です。盤面上の白いドット（c4, d3, e6, f5）をクリックすると対戦がスタートします！"
                else:
                    status_title = f"🟢 【対局開始前（0手目・待機中）】 あなたは {user_role_badge} です"
                    status_sub = "盤面上の白いドット（c4, d3, e6, f5）をクリックすると1手目が打たれ、対局がスタートします！"
            else:
                if match_mode == "pvp":
                    turn_str = f"👉 {curr_turn_badge} {curr_mover_name} の手番です"
                    status_bg = "linear-gradient(135deg, rgba(238, 242, 255, 0.95), rgba(254, 243, 199, 0.95))"
                    status_border = "#f59e0b"
                    status_title = f"🔥 【対人戦進行中！（現在 {len(game.history)} 手完了 ／ 残り {e_cnt} マス）】 {turn_str}"
                    status_sub = f"⚫ {p1_name} vs ⚪ {p2_name} ｜ 直前の手：{last_actor} が `{last_move_str}` に着手（盤面の赤い『＋』）"
                else:
                    turn_str = f"👉 あなた（{user_role_badge}）の手番です" if game.current_turn == user_color else f"🤖 AI（{ai_role_badge}）の手番です"
                    status_bg = "linear-gradient(135deg, rgba(238, 242, 255, 0.95), rgba(250, 232, 255, 0.95))"
                    status_border = "#818cf8"
                    status_title = f"🔥 【対局進行中！（現在 {len(game.history)} 手完了 ／ 残り {e_cnt} マス）】 {turn_str}"
                    status_sub = f"👤 あなた：{user_role_badge} vs 🤖 AI：{ai_role_badge} ({engine.name}) ｜ 直前の手：{last_actor} が `{last_move_str}` に着手（盤面の赤い『＋』）"

            st.markdown(
                f"""
                <div style="background:{status_bg}; border:2px solid {status_border}; border-radius:14px; padding:14px 20px; margin-bottom:14px; box-shadow:0 4px 14px rgba(139,148,214,0.14);">
                    <div style="font-size:19px; font-weight:800; color:#2b3048; margin-bottom:4px;">{status_title}</div>
                    <div style="font-size:14px; color:#4c5175; font-weight:500;">{status_sub}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # ---- 盤面上部のワンクリック先手/後手切り替え＆新規対局バー ----
            if match_mode == "ai":
                top_c1, top_c2, top_c3 = st.columns([1, 1, 1])
                with top_c1:
                    st.button(
                        f"⚫ 黒（先手）で最初から対局 {'【選択中】' if user_color == BLACK else ''}",
                        width="stretch",
                        type="primary" if user_color == BLACK else "secondary",
                        key="btn_start_black",
                        on_click=start_new_game,
                        args=(BLACK, engine),
                    )
                with top_c2:
                    st.button(
                        f"⚪ 白（後手）で最初から対局 {'【選択中】' if user_color == WHITE else ''}",
                        width="stretch",
                        type="primary" if user_color == WHITE else "secondary",
                        key="btn_start_white",
                        on_click=start_new_game,
                        args=(WHITE, engine),
                    )
                with top_c3:
                    st.button(
                        "🔄 先手・後手を入れ替えて新規対局",
                        width="stretch",
                        key="btn_swap_color",
                        on_click=start_new_game,
                        args=(-user_color, engine),
                    )
            else:
                pvp_top1, pvp_top2 = st.columns(2)
                with pvp_top1:
                    st.button(
                        f"🔄 新規対人戦を開始（初手：⚫ {p1_name} から）",
                        width="stretch",
                        type="primary",
                        key="btn_start_pvp_game",
                        on_click=start_new_game,
                        args=(BLACK, engine),
                    )
                with pvp_top2:
                    def _swap_pvp_players():
                        _swap_pvp_names()
                        st.session_state.pvp_user_color = -st.session_state.get("pvp_user_color", BLACK)
                        st.session_state.pvp_user_color_radio = st.session_state.pvp_user_color
                        start_new_game(BLACK, engine)

                    st.button(
                        f"🔃 先後を交代して新規対局（⚫ {p2_name} vs ⚪ {p1_name}）",
                        width="stretch",
                        key="btn_swap_pvp_players",
                        on_click=_swap_pvp_players,
                    )

            col_left, col_right = st.columns([1.15, 1.0])

            with col_left:
                st.subheader("🟩 8×8 オセロ対戦盤")
                st.caption("盤面上の白いドットをクリックすると着手し、石の裏返しアニメーションが再生されます（赤い『＋』＝直前の着手、金色の輪＝直前に裏返った石）。")

                board_payload_json = build_animated_board_payload(
                    game=game,
                    user_color=user_color,
                    last_animated_ply=st.session_state.last_animated_ply,
                    game_id=st.session_state.game_id,
                    cell_size=55,
                    match_mode=match_mode,
                    p1_name=p1_name,
                    p2_name=p2_name,
                )
                st.session_state.last_animated_ply = len(game.history)

                # 固定 key="main_othello_board" を維持することでコンポーネントの再マウント（画面点滅）を完全に防止
                comp_res = OTHELLO_BOARD_COMPONENT(
                    data=board_payload_json,
                    on_clicked_change=lambda: handle_board_click_callback(engine),
                    key="main_othello_board",
                )
                if comp_res and getattr(comp_res, "clicked", None):
                    clicked_coord = str_to_coord(str(comp_res.clicked))
                    can_act = not game.game_over and (
                        match_mode == "pvp" or game.current_turn == user_color
                    )
                    if clicked_coord is not None and can_act:
                        handle_user_and_ai_turn(clicked_coord[0], clicked_coord[1], engine)

                # 📱 スマホ対応: 盤面のすぐ真下に「迷い手メモ」と「クイック着手ボタン」を配置！
                can_act = not game.game_over and (match_mode == "pvp" or game.current_turn == user_color)
                if can_act and legal_moves:
                    curr_act_name = (p1_name if game.current_turn == BLACK else p2_name) if match_mode == "pvp" else "あなた"
                    legal_list = sorted(legal_moves.keys(), key=lambda rc: coord_to_str(*rc))
                    current_ply_val = len(game.history)
                    candidate_labels = [coord_to_str(*rc) for rc in legal_list]

                    # 💡 迷った手の選択（思考メモ）- 盤面のすぐ下
                    st.markdown(
                        """
                        <div style="background: rgba(238, 242, 255, 0.85); border: 1.5px dashed #818cf8; border-radius: 10px; padding: 10px 14px; margin-top: 10px; margin-bottom: 6px;">
                            <div style="font-size: 13.5px; font-weight: 700; color: #3730a3;">
                                🤔 迷い手メモ（盤面直下で選択・終局時にAIが正解を徹底判定！）
                            </div>
                            <div style="font-size: 12px; color: #4c5175; margin-top: 2px;">
                                迷っている候補手をタップして選択した状態で着手すると、終局後にAIコーチが「実戦手」と「迷った手」を比較し、判断が正解だったかを解説します。
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    selected_pills = st.pills(
                        "迷っている候補手をマーク（複数選択可）",
                        options=candidate_labels,
                        selection_mode="multi",
                        key=f"hesitated_pills_{current_ply_val}",
                        label_visibility="collapsed",
                    )
                    if selected_pills:
                        st.info(
                            f"💡 **「{' / '.join(selected_pills)}」で迷い中！** この状態で盤面または下のボタンで着手すると、この局面の思考メモが保存されます。"
                        )

                    saved_dilemmas_count = len(st.session_state.get("dilemmas", []))
                    if saved_dilemmas_count > 0:
                        st.caption(f"📌 現在 **{saved_dilemmas_count} 箇所** の迷い手を記録中（終局時に一括判定されます）")

                    # クイック着手ボタンも盤面直下にあるとスマホで圧倒的に着手しやすい
                    st.markdown(f"**🎯 クイック着手ボタン（{color_name(game.current_turn)}・{curr_act_name}）:**")
                    q_cols = st.columns(min(6, len(legal_list)))
                    for idx_mv, rc in enumerate(legal_list):
                        mv_label = coord_to_str(*rc)
                        q_cols[idx_mv % len(q_cols)].button(
                            f"{mv_label} ({len(legal_moves[rc])})",
                            key=f"quick_{mv_label}",
                            width="stretch",
                            on_click=handle_user_and_ai_turn,
                            args=(rc[0], rc[1], engine),
                        )

            with col_right:
                st.subheader("📊 対局ステータス")

                # 石数・手番メトリクス
                m_b, m_w, m_e = st.columns(3)
                p_black_label = p1_name if match_mode == "pvp" else ("あなた" if user_color == BLACK else "AI")
                p_white_label = p2_name if match_mode == "pvp" else ("あなた" if user_color == WHITE else "AI")
                m_b.metric(
                    f"⚫ 黒石 ({p_black_label})",
                    f"{b_cnt} 石",
                    f"{b_cnt - w_cnt:+d}",
                )
                m_w.metric(
                    f"⚪ 白石 ({p_white_label})",
                    f"{w_cnt} 石",
                    f"{w_cnt - b_cnt:+d}",
                )
                m_e.metric("空きマス / 進行手数", f"{e_cnt} マス", f"{len(game.history)} 手完了")

                # 自動パス通知があれば表示（game.pass_notices はAIの連続着手で消えるため、ターン単位で蓄積した方を使う）
                for notice in st.session_state.turn_notices:
                    st.warning(f"⏭️ **自動パス通知**: {notice}")

                if game.game_over:
                    diff = (b_cnt - w_cnt) if user_color == BLACK else (w_cnt - b_cnt)
                    if interrupted:
                        st.info(f"⏹️ **途中終了しました。** 中断時点の石数（黒 {b_cnt} - 白 {w_cnt}）で解析しています。")
                    elif match_mode == "pvp":
                        if b_cnt > w_cnt:
                            st.success(f"🎉 **終局！{p1_name}（黒）の勝利です！** （黒 {b_cnt} - 白 {w_cnt} / 石差 {b_cnt - w_cnt:+d}）")
                        elif w_cnt > b_cnt:
                            st.success(f"🎉 **終局！{p2_name}（白）の勝利です！** （黒 {b_cnt} - 白 {w_cnt} / 石差 {w_cnt - b_cnt:+d}）")
                        else:
                            st.info(f"🤝 **終局！引き分けです。** （黒 {b_cnt} - 白 {w_cnt}）")
                    else:
                        if diff > 0:
                            st.success(f"🎉 **終局！あなたの勝利です！** （黒 {b_cnt} - 白 {w_cnt} / 石差 {diff:+d}）")
                        elif diff < 0:
                            st.error(f"🏁 **終局！AIの勝利です。** （黒 {b_cnt} - 白 {w_cnt} / 石差 {diff:+d}）")
                        else:
                            st.info(f"🤝 **終局！引き分けです。** （黒 {b_cnt} - 白 {w_cnt}）")
                else:
                    if match_mode == "pvp":
                        turn_owner = p1_name if game.current_turn == BLACK else p2_name
                    else:
                        turn_owner = "あなた" if game.current_turn == user_color else "AIエンジン"
                    st.info(
                        f"現在の手番: **{color_name(game.current_turn)}（{turn_owner}）** | "
                        f"合法手数（モビリティ）: **{len(legal_moves)} 箇所**"
                    )

                render_cheatsheet()
                st.markdown("##### 📝 棋譜 (Kifu)")
                kifu_text = game.get_kifu_string() or "(まだ着手はありません)"
                st.code(kifu_text, language="text")

                if not game.game_over and len(game.history) >= 4:
                    if st.button("⏹️ ここまでの棋譜で終局扱いにしてAIコーチング解析を実行", width="stretch"):
                        game.game_over = True
                        st.rerun()

            # ================= 終局後の自動解析パイプライン =================
            if game.game_over and game.history:
                st.divider()
                st.header("🧠 終局後の自動解析 ＆ Gemini AIコーチング")

                # 未解析なら解析を別スレッドで始め、終わるまでは進み具合だけ出す
                # （終わると自動で結果を反映し、棋譜再生タブで最終局面を開く。再読み込みは不要）
                if not st.session_state.analysis_done:
                    pg_job_id = ensure_postgame_job(
                        engine=engine,
                        blunder_threshold=blunder_threshold,
                        api_key=api_key_input,
                        analysis_engine=analysis_engine,
                    )
                    render_postgame_progress(pg_job_id)
                else:
                    st.success(
                        "📁 latest_game_review.md を更新しました。Antigravityで『対局結果をレビューして』と入力するとコーチングを受けられます"
                    )

                    col_re, col_status = st.columns([1, 3])
                    with col_re:
                        if st.button("🔁 現在の設定で再解析を実行", width="stretch"):
                            # 同じ記録を上書きする形で、バックグラウンド解析をやり直す
                            st.session_state.analysis_done = False
                            st.session_state.postgame_job_id = None
                            st.rerun()
                    with col_status:
                        api_badge = (
                            "✨ `gemini-2.5-flash` (google-genai) による戦術解説を生成しました"
                            if st.session_state.used_gemini
                            else "💡 内蔵オセロ戦術ルールエンジンで解説を生成しました（サイドバーにGemini API Keyを入力すると `gemini-2.5-flash` で生成します）"
                        )
                        save_msg = (
                            f"｜💾 SQLite履歴 (ID: #{st.session_state.saved_record_id}) に自動保存済み"
                            if st.session_state.saved_record_id
                            else ""
                        )
                        st.caption(f"{api_badge}{save_msg}")

                    analyses: List[PlyAnalysis] = st.session_state.analyses
                    blunders: List[PlyAnalysis] = [a for a in analyses if a.is_blunder]

                    # 🏆 1局ごとのレーティング診断カード
                    all_recs = list_match_records(limit=200)
                    rated_chron = compute_sequential_ratings(all_recs)
                    cur_rated = rated_chron[-1] if rated_chron else {}
                    cur_rating = cur_rated.get("rating_after", 1200)
                    cur_delta = cur_rated.get("rating_delta", 0)
                    cur_perf = cur_rated.get("perf_rating", 1200)
                    cur_rank_name = cur_rated.get("rank_name", "🌿 初中級")
                    cur_rank_color = cur_rated.get("rank_color", "#06b6d4")
                    cur_rank_desc = cur_rated.get("rank_desc", "")
                    delta_str = f"+{cur_delta} pt 📈" if cur_delta > 0 else (f"{cur_delta} pt 📉" if cur_delta < 0 else "±0 pt ➖")
                    delta_color = "#10b981" if cur_delta > 0 else ("#ef4444" if cur_delta < 0 else "#6b7280")

                    st.markdown(
                        f"""
                        <div style="background: linear-gradient(135deg, rgba(238, 242, 255, 0.95), rgba(254, 243, 199, 0.85)); border: 2px solid #818cf8; border-radius: 14px; padding: 16px 20px; margin-top: 14px; margin-bottom: 20px; box-shadow: 0 4px 14px rgba(99, 102, 241, 0.12);">
                            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                                <div style="font-size: 16px; font-weight: 800; color: #1e1b4b;">
                                    🏆 今局の棋力レーティング診断 ＆ 段級位カルテ
                                </div>
                                <div style="background: {cur_rank_color}; color: #ffffff; padding: 4px 14px; border-radius: 9999px; font-size: 13px; font-weight: 700;">
                                    認定称号: {cur_rank_name}
                                </div>
                            </div>
                            <div style="font-size: 12px; color: #4b5563; margin-top: 4px; margin-bottom: 12px;">
                                {cur_rank_desc} ｜ 対戦相手の強さ・勝敗・着手精度（好手率・平均石損）から算出した最新Eloレーティングです。「📈 成長」タブで推移と弱点を確認できます。
                            </div>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px;">
                                <div style="background: #ffffff; padding: 10px 14px; border-radius: 10px; border: 1px solid #e0e7ff;">
                                    <div style="font-size: 11px; color: #6b7280; font-weight: 600;">現在の推定レート</div>
                                    <div style="font-size: 22px; font-weight: 900; color: #3730a3; margin-top: 2px;">{cur_rating} <span style="font-size: 13px; color: {delta_color}; font-weight: 700;">({delta_str})</span></div>
                                </div>
                                <div style="background: #ffffff; padding: 10px 14px; border-radius: 10px; border: 1px solid #e0e7ff;">
                                    <div style="font-size: 11px; color: #6b7280; font-weight: 600;">今局のパフォーマンス</div>
                                    <div style="font-size: 22px; font-weight: 900; color: #059669; margin-top: 2px;">{cur_perf} pt</div>
                                </div>
                                <div style="background: #ffffff; padding: 10px 14px; border-radius: 10px; border: 1px solid #e0e7ff;">
                                    <div style="font-size: 11px; color: #6b7280; font-weight: 600;">対戦相手レベル</div>
                                    <div style="font-size: 14px; font-weight: 800; color: #1f2937; margin-top: 6px;">{engine.name}</div>
                                </div>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    # 1. グラフと盤面での振り返りは棋譜再生タブ（AI戦・対人戦共通の画面）で行う
                    if st.session_state.saved_record_id:
                        st.button(
                            "📖 振り返る（グラフ＋盤面で1手ずつ）",
                            key="btn_open_review_after_game",
                            type="primary",
                            width="stretch",
                            on_click=request_review_jump,
                            args=(st.session_state.saved_record_id, len(game.history)),
                        )

                    # 2. ⚖️ 「あの時迷った手」の判定 ＆ 振り返り (Dilemma Review)
                    dilemmas_res: List[DilemmaAnalysis] = st.session_state.get("dilemma_analyses", [])
                    st.subheader(f"⚖️ 「あの時迷った手」の判定 ＆ 振り返り: {len(dilemmas_res)} 件")
                    if not dilemmas_res:
                        st.caption(
                            "💡 対局中に「🤔 迷い手メモ」で候補手をマークしておくと、終局時にAIが『実戦の判断が正解だったか』をここで徹底比較・判定します！"
                        )
                    else:
                        for idx_d, d in enumerate(dilemmas_res, start=1):
                            render_dilemma_card(d, idx_d, engine=analysis_engine, user_color=user_color)

                    # 3. 悪手（Blunder）一覧と盤面特徴量・Gemini解説
                    st.subheader(
                        f"🔍 抽出された悪手（Blunder: 評価値 {blunder_threshold:+.0f} 石以下の下落）: {len(blunders)} 件"
                    )
                    if not blunders:
                        st.success(
                            f"素晴らしい対局です！閾値（{blunder_threshold:+.0f} 石）を超える大きな悪手は検出されませんでした。"
                        )
                    else:
                        with st.expander(f"悪手の解説を一覧で見る（{len(blunders)} 件）", expanded=False):
                            for idx, b in enumerate(blunders, start=1):
                                render_blunder_card(b, idx, engine=analysis_engine, user_color=user_color)

                    # 4. 対局全体を通した弱点の癖・改善アドバイスサマリー
                    st.subheader("📋 対局全体を通したあなたの弱点の癖・改善アドバイス")
                    st.markdown(st.session_state.coach_summary)

    # ================= タブ2: 振り返り（AI戦・対人戦共通） =================
    with tab_pvp:
        render_pvp_tab(analysis_engine, blunder_threshold, api_key_input)

    # ================= タブ4: 成長（レーティングと弱点。弱点から学習へ） =================
    with tab_growth:
        all_records = list_match_records(limit=200)

        if not all_records:
            st.info("まだ記録された対局がありません。1局終局させると、ここにレーティングと弱点が出ます。")
        else:
            chron = list(reversed(all_records))
            rated_chron = compute_sequential_ratings(chron)
            n_games = len(rated_chron)
            wins = sum(1 for r in rated_chron if "WIN" in r.get("result_label", ""))
            # 途中終了（中断）局は勝敗がつかないため勝率の分母から除く
            decided_games = sum(1 for r in rated_chron if "中断" not in r.get("result_label", ""))
            win_rate = (wins / decided_games) * 100.0 if decided_games else 0.0

            latest = rated_chron[-1]
            prev = rated_chron[:-1] if n_games > 1 else rated_chron

            cur_rating = latest.get("rating_after", 1200)
            cur_delta = latest.get("rating_delta", 0)
            peak_rating = max(r.get("rating_after", 1200) for r in rated_chron)
            cur_rank_name = latest.get("rank_name", "🌿 初中級")
            cur_rank_desc = latest.get("rank_desc", "")

            delta_badge = f"+{cur_delta} pt" if cur_delta > 0 else (f"{cur_delta} pt" if cur_delta < 0 else "±0 pt")
            r_col1, r_col2, r_col3, r_col4 = st.columns(4)
            r_col1.metric("レーティング", f"{cur_rating} pt", f"{delta_badge}（最新局）")
            r_col2.metric("最高", f"{peak_rating} pt")
            r_col3.metric("勝率（中断局を除く）", f"{win_rate:.0f}%", f"{wins}勝 / {decided_games}局", delta_color="off")
            r_col4.metric("段級位", cur_rank_name, cur_rank_desc, delta_color="off")

            # 次の目標: 1つ上のAIに挑戦できるレート（対局タブの難易度の自動選択と同じ基準）
            goal = difficulty_goal(float(cur_rating))
            if goal:
                span = max(1.0, goal["to"] - goal["from"])
                st.progress(
                    min(1.0, max(0.0, (float(cur_rating) - goal["from"]) / span)),
                    text=f"⬆️ 次の目標：{goal['next']} に挑戦（レート {goal['to']:.0f}・あと {goal['to'] - float(cur_rating):.0f}）",
                )
            else:
                st.success("👑 最上位のAI（達人）に挑戦できるレートです！")

            # レーティングの推移（線1本だけ。横軸は対局の順番）
            ratings_y = [float(r.get("rating_after", 1200)) for r in rated_chron]
            hover_data = [
                (
                    f"#{r['id']}",
                    (r.get("played_at") or "")[:16],
                    _opponent_name_from_record(r.get("engine_name", "-"), r.get("user_color", BLACK)),
                    r.get("result_label", "-"),
                    r.get("rating_delta", 0),
                )
                for r in rated_chron
            ]
            fig_rate = go.Figure()
            tier_bands = [
                (500, 800, "🐣 入門", "rgba(148, 163, 184, 0.07)"),
                (800, 1000, "🌱 初級", "rgba(16, 185, 129, 0.07)"),
                (1000, 1200, "🌿 初中級", "rgba(6, 182, 212, 0.07)"),
                (1200, 1400, "⚔️ 中級", "rgba(59, 130, 246, 0.07)"),
                (1400, 1600, "🥋 上級", "rgba(139, 92, 246, 0.07)"),
                (1600, 2600, "🏆 有段", "rgba(245, 158, 11, 0.07)"),
            ]
            for y0, y1, label_tier, fill_c in tier_bands:
                fig_rate.add_hrect(
                    y0=y0, y1=y1, fillcolor=fill_c, line_width=0,
                    annotation_text=label_tier, annotation_position="top left",
                    annotation_font=dict(size=10, color="#9ca3af"),
                )
            fig_rate.add_trace(
                go.Scatter(
                    x=list(range(1, n_games + 1)),
                    y=ratings_y,
                    mode="lines+markers",
                    line=dict(color="#6366f1", width=3),
                    marker=dict(size=5, color="#4338ca"),
                    customdata=hover_data,
                    hovertemplate=(
                        "<b>%{customdata[0]}</b> %{customdata[1]}<br>"
                        "相手: %{customdata[2]}<br>"
                        "結果: %{customdata[3]}<br>"
                        "レート: <b>%{y:.0f}</b>（%{customdata[4]:+d}）<extra></extra>"
                    ),
                )
            )
            fig_rate.update_layout(
                # 拡大・移動はさせない（ドラッグで点のない所に拡大されて戻せなくなるため）
                xaxis=dict(title="対局（古い → 新しい）", fixedrange=True, showgrid=False),
                yaxis=dict(range=[max(500, min(ratings_y) - 120), min(2600, max(ratings_y) + 120)], fixedrange=True),
                dragmode=False,
                height=260,
                margin=dict(l=36, r=10, t=10, b=36),
                showlegend=False,
                template="plotly_white",
            )
            st.markdown("#### 📈 レーティングの推移")
            st.plotly_chart(
                fig_rate,
                width="stretch",
                config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
            )

            # 成長 → 学習: 直近の弱点と、それぞれに効く記事
            render_weakness_summary(all_records)

            with st.expander("📊 詳しい指標とグラフ（好手率・平均石損・開放度・中割り率）", expanded=False):
                avg_acc = sum(float(r.get("accuracy_rate") or 0.0) for r in chron) / n_games
                avg_loss = sum(float(r.get("avg_eval_loss") or 0.0) for r in chron) / n_games
                avg_open = sum(float(r.get("avg_openness") or 0.0) for r in chron) / n_games
                avg_naka = sum(float(r.get("nakawari_rate") or 0.0) for r in chron) / n_games

                prev_acc = sum(float(r.get("accuracy_rate") or 0.0) for r in prev) / len(prev)
                prev_loss = sum(float(r.get("avg_eval_loss") or 0.0) for r in prev) / len(prev)
                prev_open = sum(float(r.get("avg_openness") or 0.0) for r in prev) / len(prev)
                prev_naka = sum(float(r.get("nakawari_rate") or 0.0) for r in prev) / len(prev)

                k2, k3, k4, k5 = st.columns(4)
                k2.metric(
                    "最新局の好手率 (通算平均)",
                    f"{float(latest.get('accuracy_rate') or 0):.1f}% ({avg_acc:.1f}%)",
                    f"{float(latest.get('accuracy_rate') or 0) - prev_acc:+.1f}% (過去比)",
                )
                k3.metric(
                    "最新局の平均石損 (通算)",
                    f"{float(latest.get('avg_eval_loss') or 0):.2f} ({avg_loss:.2f})",
                    f"{float(latest.get('avg_eval_loss') or 0) - prev_loss:+.2f} (過去比)",
                    delta_color="inverse",
                )
                k4.metric(
                    "最新局の平均開放度 (通算)",
                    f"{float(latest.get('avg_openness') or 0):.2f} ({avg_open:.2f})",
                    f"{float(latest.get('avg_openness') or 0) - prev_open:+.2f} (過去比)",
                    delta_color="inverse",
                )
                k5.metric(
                    "最新局の中割り率 (通算)",
                    f"{float(latest.get('nakawari_rate') or 0):.1f}% ({avg_naka:.1f}%)",
                    f"{float(latest.get('nakawari_rate') or 0) - prev_naka:+.1f}% (過去比)",
                )

                game_labels = [f"#{r['id']}" for r in chron]

                col_g1, col_g2 = st.columns(2)
                with col_g1:
                    fig_skill = go.Figure()
                    fig_skill.add_trace(
                        go.Scatter(
                            x=game_labels,
                            y=[float(r.get("accuracy_rate") or 0.0) for r in chron],
                            mode="lines+markers",
                            name="好手・最善手率 (%)",
                            line=dict(color="#10b981", width=3),
                        )
                    )
                    fig_skill.add_trace(
                        go.Bar(
                            x=game_labels,
                            y=[int(r.get("blunder_count") or 0) for r in chron],
                            name="悪手 (Blunder) 数",
                            marker_color="rgba(239, 68, 68, 0.55)",
                            yaxis="y2",
                        )
                    )
                    fig_skill.update_layout(
                        title="🎯 棋力精度（好手率 ＆ 悪手数）の推移",
                        xaxis_title="対局ID",
                        xaxis=dict(fixedrange=True),
                        yaxis=dict(title="好手率 (%)", range=[0, 105], fixedrange=True),
                        dragmode=False,
                        yaxis2=dict(title="悪手数 (回)", overlaying="y", side="right", range=[0, 12], fixedrange=True),
                        height=340,
                        margin=dict(l=25, r=25, t=50, b=30),
                        legend=dict(orientation="h", y=1.12),
                        template="plotly_white",
                    )
                    st.plotly_chart(
                        fig_skill,
                        width="stretch",
                        config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
                    )

                with col_g2:
                    fig_tactics = go.Figure()
                    fig_tactics.add_trace(
                        go.Scatter(
                            x=game_labels,
                            y=[float(r.get("nakawari_rate") or 0.0) for r in chron],
                            mode="lines+markers",
                            name="中割り達成率 (%)",
                            line=dict(color="#3b82f6", width=3),
                        )
                    )
                    fig_tactics.add_trace(
                        go.Scatter(
                            x=game_labels,
                            y=[float(r.get("avg_openness") or 0.0) for r in chron],
                            mode="lines+markers",
                            name="平均開放度 (低いほど良)",
                            line=dict(color="#f59e0b", width=2, dash="dot"),
                            yaxis="y2",
                        )
                    )
                    fig_tactics.update_layout(
                        title="📐 オセロ戦術スキル（中割り率 ＆ 平均開放度）の推移",
                        xaxis_title="対局ID",
                        xaxis=dict(fixedrange=True),
                        yaxis=dict(title="中割り率 (%)", range=[0, 105], fixedrange=True),
                        dragmode=False,
                        yaxis2=dict(title="平均開放度", overlaying="y", side="right", range=[0, 10], fixedrange=True),
                        height=340,
                        margin=dict(l=25, r=25, t=50, b=30),
                        legend=dict(orientation="h", y=1.12),
                        template="plotly_white",
                    )
                    st.plotly_chart(
                        fig_tactics,
                        width="stretch",
                        config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
                    )

            # これまでの全対局の弱点タグ（件数の多い順）。カードを並べると画面が埋まるので表にする
            tag_counts: Dict[str, int] = {}
            for r in chron:
                for t in (r.get("weakness_tags") or "").split(","):
                    t = t.strip()
                    if t:
                        tag_counts[t] = tag_counts.get(t, 0) + 1
            with st.expander(f"🏷️ これまでの弱点タグ（全{n_games}局）", expanded=False):
                if not tag_counts:
                    st.caption("弱点タグはまだありません。")
                else:
                    st.dataframe(
                        [
                            {"弱点": f"#{name}", "局数": cnt, "発生率": f"{cnt * 100 / n_games:.0f}%"}
                            for name, cnt in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
                        ],
                        width="stretch",
                        hide_index=True,
                    )

            with st.expander("📝 対局メモ・弱点タグの手動編集", expanded=False):
                st.info(
                    "💡 Antigravityに **「対局結果をレビューしてカルテに保存して」** や **「私の長期的な弱点と成長を分析して」** と話しかけると、`player_growth_log.md` と SQLite データベースの両方にレビューが蓄積されます。以下のフォームから手動でメモや弱点タグを追記・保存することも可能です。"
                )
                target_rec_id = st.selectbox(
                    "レビュー・メモを編集する対局を選択:",
                    options=[r["id"] for r in all_records],
                    format_func=lambda rid: next(
                        f"#{r['id']} ({r['played_at']} | {r['result_label']} | 弱点タグ: {r.get('weakness_tags') or 'なし'})"
                        for r in all_records
                        if r["id"] == rid
                    ),
                    key="growth_select_rec",
                )
                target_rec = next((r for r in all_records if r["id"] == target_rec_id), None)
                if target_rec:
                    new_tags = st.text_input(
                        "弱点タグ（カンマ区切り）",
                        value=target_rec.get("weakness_tags") or "",
                        placeholder="例: 危険なX打ち,開放度過多(外返し),相手モビリティ増加",
                        key=f"tags_edit_{target_rec_id}",
                    )
                    current_review = (
                        target_rec.get("antigravity_review") or target_rec.get("coach_summary") or ""
                    )
                    new_review = st.text_area(
                        "コーチングレビュー / 振り返りノート",
                        value=current_review,
                        height=180,
                        key=f"rev_edit_{target_rec_id}",
                    )
                    if st.button("💾 レビューと弱点タグを保存し `player_growth_log.md` を更新", key=f"save_rev_{target_rec_id}"):
                        update_match_review(target_rec_id, new_review, new_tags)
                        export_player_growth_md(list_match_records())
                        st.success(f"対局 #{target_rec_id} のレビューと `player_growth_log.md` を更新しました！")
                        st.rerun()

    # ================= タブ3: 学習（bassy84.net のクエスト＆図鑑・オーナーのみ） =================
    with tab_quests:
        render_quests_tab()


if __name__ == "__main__":
    main()
