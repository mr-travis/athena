"""独立的业务运行记录；不保存消息正文或 Agent 执行检查点。"""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalSqliteStore:
    """显式指定数据库路径，每次操作独立连接并在结束后关闭。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser().absolute()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(run_id)) > 0),
                    status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed')),
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    CHECK (
                        (status = 'running' AND finished_at IS NULL)
                        OR (status IN ('succeeded', 'failed') AND finished_at IS NOT NULL)
                    )
                )
            """)

    def create_run(self, run_id: str) -> None:
        """创建 running 记录；重复 ID 抛出 sqlite3.IntegrityError，不覆盖旧记录。"""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空字符串。")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO runs (run_id, status, started_at) VALUES (?, 'running', ?)",
                (run_id, _utc_now()),
            )

    def finish_run(self, run_id: str, status: str) -> None:
        """将 running 更新为 succeeded 或 failed；已结束记录不允许再次结束。"""
        if status not in ("succeeded", "failed"):
            raise ValueError("结束状态必须是 succeeded 或 failed。")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cursor = conn.execute(
                "UPDATE runs SET status = ?, finished_at = ? WHERE run_id = ? AND status = 'running'",
                (status, _utc_now(), run_id),
            )
            if cursor.rowcount == 0:
                if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
                    raise KeyError(run_id)
                raise ValueError("该运行已结束，不能重复修改结束状态。")

    def get_run(self, run_id: str) -> dict[str, str | None] | None:
        """返回运行记录；不存在返回 None。"""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT run_id, status, started_at, finished_at FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return dict(row) if row is not None else None
