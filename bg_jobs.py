"""
時間のかかる処理（終局後の全手解析など）を、Streamlit の画面描画とは別のスレッドで走らせるための小さな仕組み。

画面の描画中に解析すると、スマホの通信切れや再実行で途中で止まり、再読み込みしないと結果が出ないことがある。
ここでジョブとして走らせておけば、画面側は数秒おきに状態を見に来るだけでよく、終わったら自動で表示を切り替えられる。

app.py は再実行のたびに読み直されるため、ジョブの置き場所はこの別モジュール（プロセス内で1つ）にする。
"""

import threading
import time
import traceback
from typing import Any, Callable, Dict, Optional

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
_KEEP_SECONDS = 6 * 3600  # 終わったジョブを残しておく時間


def _cleanup_locked(now: float) -> None:
    for job_id in [k for k, j in _JOBS.items() if j["status"] != "running" and now - j["finished"] > _KEEP_SECONDS]:
        del _JOBS[job_id]


def start_job(job_id: str, fn: Callable[..., Any], **kwargs: Any) -> Dict[str, Any]:
    """
    job_id のジョブがまだ無ければ、別スレッドで fn(report=..., **kwargs) を実行する。すでにあれば何もしない。
    fn には進み具合を知らせる report(msg) が渡される。戻り値は job["result"] に入る。
    """
    with _LOCK:
        now = time.time()
        _cleanup_locked(now)
        job = _JOBS.get(job_id)
        if job is not None:
            return job
        job = {"status": "running", "started": now, "finished": 0.0, "progress": "", "result": None, "error": ""}
        _JOBS[job_id] = job

    def report(msg: str) -> None:
        job["progress"] = msg

    def run() -> None:
        try:
            job["result"] = fn(report=report, **kwargs)
            job["status"] = "done"
        except Exception as e:  # 画面側でエラーを出せるよう、例外は記録して終える
            traceback.print_exc()
            job["error"] = f"{type(e).__name__}: {e}"
            job["status"] = "error"
        finally:
            job["finished"] = time.time()

    threading.Thread(target=run, name=f"bgjob-{job_id}", daemon=True).start()
    return job


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _JOBS.get(job_id)


def forget_job(job_id: str) -> None:
    with _LOCK:
        _JOBS.pop(job_id, None)
