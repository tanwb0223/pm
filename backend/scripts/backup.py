# -*- coding: utf-8 -*-
"""数据库备份 / 校验 / 恢复脚本（跨平台：Windows / Linux 通用）

## 为什么重写

原脚本只有 `pg_dump` 分支：找不到 pg_dump 直接 `fail()` 退出。
但**默认部署是 SQLite**（零依赖解压即用，便携版/单机部署全是 SQLite），
于是"每日 02:00 自动备份"在默认部署下必然失败，且失败只写日志没人知道。
本版本按 `PM_DATABASE_URL` 自动分流：

    SQLite      → sqlite3.Connection.backup() 在线热备（WAL checkpoint 后）
                  → 输出 pm_YYYYmmdd_HHMMSS.db.gz（gzip）
    PostgreSQL  → pg_dump --format=custom
                  → 输出 pm_YYYYmmdd_HHMMSS.dump

同时补上恢复与校验闭环：
    --verify             校验最近一次备份（SQLite 用 PRAGMA integrity_check）
    --restore <file>     恢复指定备份（恢复前先自动备份当前库，可回退）

用法：
    python scripts/backup.py                     # 正常备份
    python scripts/backup.py --verify            # 校验最近一次备份
    python scripts/backup.py --list              # 列出已有备份
    python scripts/backup.py --restore <file>    # 从备份恢复
    python scripts/backup.py --restore-latest    # 从最近一次备份恢复

环境变量（也可写进 .env）：
    PM_DATABASE_URL      数据库连接串（留空 = SQLite {data_dir}/pm.db）
    PM_BACKUP_DIR        备份目录（默认按平台自适应）
    PM_BACKUP_KEEP_DAYS  保留天数（默认 30）
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 允许直接运行（不通过包导入）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.db.dialect import is_sqlite  # noqa: E402

BACKUP_GLOB = "pm_*"


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fail(msg: str, code: int = 1) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 错误: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


# ================================================================ 路径与识别
def backup_dir() -> Path:
    d = Path(settings.backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def sqlite_path(url: str) -> Path:
    """从 SQLAlchemy SQLite 连接串解析数据库文件路径

    支持 sqlite:///rel/path、sqlite:////abs/path、sqlite:///D:/x/pm.db
    """
    raw = url.split("sqlite:///", 1)[-1] if "sqlite:///" in url else url.split("sqlite://", 1)[-1]
    return Path(raw)


def is_sqlite_backend() -> bool:
    return is_sqlite(settings.database_url or "")


def list_backup_files() -> list[Path]:
    """按时间倒序列出备份文件（两种后端命名都算）"""
    out_dir = backup_dir()
    files = [
        f
        for f in out_dir.glob(BACKUP_GLOB)
        if f.is_file() and f.name.endswith((".dump", ".db.gz"))
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def prune_old_backups() -> None:
    keep = settings.backup_keep_days
    if keep <= 0:
        return
    cutoff = datetime.now().timestamp() - keep * 86400
    removed = 0
    for f in list_backup_files():
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        log(f"清理 {removed} 个超过 {keep} 天的旧备份")


def report_disk(out_dir: Path) -> None:
    try:
        usage = shutil.disk_usage(out_dir)
        pct = usage.used / usage.total * 100
        log(f"备份目录所在分区使用率: {pct:.0f}%")
        if pct > 85:
            log("警告: 磁盘使用率超过 85%，请及时清理")
    except OSError:
        pass


# ================================================================ PostgreSQL
def find_pg_dump() -> str:
    """定位 pg_dump 可执行文件

    Windows 下 PostgreSQL 不一定在 PATH 里，需要按常见安装路径查找。
    """
    found = shutil.which("pg_dump")
    if found:
        return found

    if sys.platform.startswith("win"):
        candidates: list[Path] = []
        for ver in ("18", "17", "16"):
            for base in (r"C:\Program Files\PostgreSQL", r"C:\Program Files (x86)\PostgreSQL"):
                candidates.append(Path(base) / ver / "bin" / "pg_dump.exe")
        for c in candidates:
            if c.is_file():
                return str(c)

    for p in ("/usr/bin/pg_dump", "/usr/pgsql-18/bin/pg_dump", "/usr/local/pgsql/bin/pg_dump"):
        if Path(p).is_file():
            return p

    fail(
        "找不到 pg_dump。请确认已安装 PostgreSQL 客户端工具：\n"
        "  Windows: 安装 PostgreSQL 时勾选 Command Line Tools，或把 bin 目录加入 PATH\n"
        "  Linux:   apt install postgresql-client-18"
    )
    return ""  # 不可达，仅为类型检查


def find_pg_restore() -> str | None:
    found = shutil.which("pg_restore")
    if found:
        return found
    try:
        pg_dump = find_pg_dump()
    except SystemExit:
        return None
    candidate = Path(pg_dump).parent / ("pg_restore.exe" if sys.platform.startswith("win") else "pg_restore")
    return str(candidate) if candidate.is_file() else None


def to_pg_dsn(url: str) -> str:
    """SQLAlchemy 连接串 → psycopg/pg_dump 能识别的 DSN"""
    return url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def backup_postgres() -> int:
    pg_dump = find_pg_dump()
    out_dir = backup_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"pm_{stamp}.dump"

    log(f"开始备份（PostgreSQL）→ {out_file}")
    log(f"pg_dump: {pg_dump}")

    cmd = [
        pg_dump,
        to_pg_dsn(settings.database_url),
        "--format=custom",
        "--compress=6",
        "--no-owner",
        "--no-privileges",
        f"--file={out_file}",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        out_file.unlink(missing_ok=True)
        fail("备份超时（超过 30 分钟）")
    except OSError as e:
        fail(f"无法执行 pg_dump: {e}")

    if result.returncode != 0:
        out_file.unlink(missing_ok=True)
        fail(f"pg_dump 失败: {(result.stderr or '').strip()[:500]}")

    size = out_file.stat().st_size
    if size < 1024:
        out_file.unlink(missing_ok=True)
        fail(f"备份文件过小（{size} 字节），可能失败")

    if not verify_file(out_file):
        out_file.unlink(missing_ok=True)
        fail("备份文件校验失败，已删除")

    log(f"备份完成: {size / 1024 / 1024:.1f} MB")
    return 0


# ================================================================ SQLite
def backup_sqlite() -> int:
    """SQLite 在线热备

    为什么用 Connection.backup() 而不是直接 cp：
    SQLite 在 WAL 模式下有 -wal / -shm 两个附属文件，直接复制主库文件
    会丢掉尚未 checkpoint 的事务，恢复后表现为"最近几天的数据没了"。
    `backup()` 是 SQLite 官方提供的在线备份 API，会正确处理 WAL，
    备份出来的文件是完整一致的快照。

    流程：源库 WAL checkpoint → backup() 到临时 .db → gzip → 删除临时文件
    """
    db_file = sqlite_path(settings.database_url or "")
    if not db_file.is_file():
        fail(f"SQLite 数据库文件不存在：{db_file}")

    out_dir = backup_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_file = out_dir / f"pm_{stamp}.db.tmp"
    out_file = out_dir / f"pm_{stamp}.db.gz"

    log(f"开始备份（SQLite）→ {out_file}")
    log(f"源数据库: {db_file}")

    try:
        # 1) 先把 WAL 内容合并回主库，缩短 backup 期间需要复制的 WAL
        src = sqlite3.connect(str(db_file), timeout=30)
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            # 2) 在线热备：源库只读加锁，备份期间业务仍可写
            dst = sqlite3.connect(str(tmp_file))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error as e:
        tmp_file.unlink(missing_ok=True)
        fail(f"SQLite 备份失败：{e}")

    if not tmp_file.is_file() or tmp_file.stat().st_size < 1024:
        tmp_file.unlink(missing_ok=True)
        fail("SQLite 备份文件过小，可能失败")

    # 3) 备份文件本身必须能通过完整性校验才落盘
    if not _sqlite_integrity_ok(tmp_file):
        tmp_file.unlink(missing_ok=True)
        fail("SQLite 备份文件完整性校验失败，已删除")

    # 4) gzip 压缩（SQLite 文本/页数据压缩率通常 60%~80%）
    try:
        with tmp_file.open("rb") as fin, gzip.open(out_file, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout, length=1024 * 1024)
    except OSError as e:
        out_file.unlink(missing_ok=True)
        fail(f"压缩备份文件失败：{e}")
    finally:
        tmp_file.unlink(missing_ok=True)

    size = out_file.stat().st_size
    log(f"备份完成: {size / 1024 / 1024:.1f} MB（压缩后）")
    return 0


def _sqlite_integrity_ok(db_path: Path) -> bool:
    """对 SQLite 文件执行 PRAGMA integrity_check"""
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        log(f"integrity_check 执行失败：{e}")
        return False
    ok = bool(rows) and str(rows[0][0]).lower() == "ok"
    if not ok:
        log(f"integrity_check 结果异常：{rows[:3]}")
    return ok


# ================================================================ 校验
def verify_file(path: Path) -> bool:
    """校验备份文件

    - SQLite（*.db.gz）：解压后 PRAGMA integrity_check
    - PostgreSQL（*.dump）：pg_restore --list 能解析
    """
    if path.name.endswith(".db.gz"):
        return verify_sqlite_gz(path)

    pg_restore = find_pg_restore()
    if not pg_restore:
        log("未找到 pg_restore，跳过结构校验（文件大小已检查）")
        return True

    try:
        r = subprocess.run(
            [pg_restore, "--list", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def verify_sqlite_gz(path: Path) -> bool:
    """解压到临时文件做 integrity_check，校验完立即删除"""
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "verify.db"
    try:
        with gzip.open(path, "rb") as fin, tmp.open("wb") as fout:
            shutil.copyfileobj(fin, fout, length=1024 * 1024)
        return _sqlite_integrity_ok(tmp)
    except (OSError, EOFError) as e:
        log(f"解压备份失败：{e}")
        return False
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def do_verify() -> int:
    files = list_backup_files()
    if not files:
        fail("没有找到备份文件")

    latest = files[0]
    log(f"校验: {latest}")
    if verify_file(latest):
        log("校验通过（备份文件完整）")
        return 0
    fail(f"备份文件损坏: {latest}")
    return 1


# ================================================================ 恢复
def do_restore(backup_path: Path) -> int:
    """从备份恢复

    恢复前**先备份当前库**，失败或恢复错版本时可以回退 ——
    否则一次误操作就是数据永久丢失（原脚本连恢复功能都没有）。
    """
    if not backup_path.is_file():
        fail(f"备份文件不存在：{backup_path}")

    log(f"准备恢复：{backup_path}")
    log("恢复前先备份当前数据库（可回退）...")
    if is_sqlite_backend():
        backup_sqlite()
    else:
        backup_postgres()

    if backup_path.name.endswith(".db.gz"):
        return _restore_sqlite(backup_path)
    return _restore_postgres(backup_path)


def _restore_sqlite(backup_path: Path) -> int:
    db_file = sqlite_path(settings.database_url or "")
    db_file.parent.mkdir(parents=True, exist_ok=True)

    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "restore.db"
    try:
        with gzip.open(backup_path, "rb") as fin, tmp.open("wb") as fout:
            shutil.copyfileobj(fin, fout, length=1024 * 1024)
    except (OSError, EOFError) as e:
        shutil.rmtree(tmp.parent, ignore_errors=True)
        fail(f"解压备份失败：{e}")

    if not _sqlite_integrity_ok(tmp):
        shutil.rmtree(tmp.parent, ignore_errors=True)
        fail("备份文件完整性校验失败，已中止恢复（当前库未被修改）")

    # 删掉旧库的 WAL/SHM，否则恢复后 SQLite 会把旧 WAL 回放到新库上
    for suffix in ("-wal", "-shm"):
        Path(str(db_file) + suffix).unlink(missing_ok=True)

    try:
        shutil.copyfile(tmp, db_file)
    except OSError as e:
        shutil.rmtree(tmp.parent, ignore_errors=True)
        fail(f"替换数据库文件失败：{e}")
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

    log(f"恢复完成：{db_file}")
    log("请重启服务使连接池重新打开数据库文件")
    return 0


def _restore_postgres(backup_path: Path) -> int:
    pg_restore = find_pg_restore()
    if not pg_restore:
        fail("找不到 pg_restore，无法恢复 PostgreSQL 备份")

    dsn = to_pg_dsn(settings.database_url)
    cmd = [pg_restore, "--clean", "--if-exists", "--no-owner", "--no-privileges", f"--dbname={dsn}", str(backup_path)]
    log(f"执行：{pg_restore} --clean --if-exists ... → {dsn}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        fail("恢复超时（超过 60 分钟）")
    except OSError as e:
        fail(f"无法执行 pg_restore：{e}")

    if result.returncode != 0:
        fail(f"pg_restore 失败：{(result.stderr or '').strip()[:800]}")

    log("恢复完成，请重启服务")
    return 0


# ================================================================ 入口
def do_backup() -> int:
    if not settings.database_url:
        fail("未配置 PM_DATABASE_URL（且未能自动推导 SQLite 路径）")

    backend = "SQLite" if is_sqlite_backend() else "PostgreSQL"
    log(f"数据库类型：{backend}")

    code = backup_sqlite() if is_sqlite_backend() else backup_postgres()
    if code == 0:
        prune_old_backups()
        report_disk(backup_dir())
        log("全部完成")
    return code


def do_list() -> int:
    out_dir = backup_dir()
    files = list_backup_files()
    if not files:
        log(f"备份目录 {out_dir} 为空")
        return 0

    log(f"备份目录: {out_dir}")
    log(f"共 {len(files)} 个备份：")
    total = 0
    for f in files:
        size = f.stat().st_size
        total += size
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {f.name}  {size / 1024 / 1024:8.1f} MB  {mtime}")
    print(f"\n  合计: {total / 1024 / 1024:.1f} MB")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="数据库备份/校验/恢复工具")
    parser.add_argument("--verify", action="store_true", help="只校验最近一次备份")
    parser.add_argument("--list", action="store_true", help="列出已有备份")
    parser.add_argument("--restore", metavar="FILE", default=None, help="从指定备份文件恢复")
    parser.add_argument("--restore-latest", action="store_true", help="从最近一次备份恢复")
    args = parser.parse_args()

    if args.verify:
        sys.exit(do_verify())
    if args.list:
        sys.exit(do_list())
    if args.restore:
        sys.exit(do_restore(Path(args.restore)))
    if args.restore_latest:
        files = list_backup_files()
        if not files:
            fail("没有找到备份文件")
        sys.exit(do_restore(files[0]))
    sys.exit(do_backup())


if __name__ == "__main__":
    main()
