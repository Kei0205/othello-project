"""
nav.py
画面上部のタブ（対局 → 振り返り → 学習 → 対局 の流れ）と、タブの切り替えをまとめる。

タブの選択状態は st.tabs(key="main_tab") のウィジェットの値なので、ボタンのコールバックで予約しておき、
main() の st.tabs の直前で apply_tab_jump() が反映する（ウィジェット生成後に書き換えるとエラーになるため）。
"""

import streamlit as st

PLAY_TAB = "🎮 対局"
REVIEW_TAB = "📖 振り返り"
LEARN_TAB = "📚 学習"
GROWTH_TAB = "📈 成長"
MAIN_TABS = [PLAY_TAB, REVIEW_TAB, LEARN_TAB, GROWTH_TAB]


def request_tab(label: str) -> None:
    """タブの切り替えを予約する（ボタンの on_click から呼ぶ）"""
    st.session_state._tab_jump = label


def apply_tab_jump() -> None:
    """main() の st.tabs の直前で呼ぶ。古いタブ名が残っていたら消す（タブ名を変えた後の既存セッション対策）"""
    label = st.session_state.pop("_tab_jump", None)
    if label in MAIN_TABS:
        st.session_state.main_tab = label
    if st.session_state.get("main_tab") not in MAIN_TABS:
        st.session_state.pop("main_tab", None)
