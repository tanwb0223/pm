# -*- coding: utf-8 -*-
"""数据库备份脚本（跨平台：Windows / Linux 通用）

替代原来的 backup.sh —— 用 Python 实现，不依赖 shell 与 GNU 工具，
Windows 和 Linux 都能直接运行。

用法：
    python scripts/backup.py               # 正常备份
    python scripts/backup.py --verify      # 只校验最近一次备份
    python scripts/backup.py --list        # 列出已有备份

环境变量（也可写进 .env）：
    PM_DATABASE_URL    数据库连接串（必须）
    PM_BACKUP_DIR      备份目录（默认按平台自适应）
    PM_BACKUP_KEEP_DAYS 保留天数（默认 30）
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 允许直接运行（不通过包导入）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fail(msg: str, code: int = 1) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 错误: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def find_pg_dump() -> str:
    """定位 pg_dump 可执行文件

    Windows 下 PostgreSQL 不一定在 PATH 里，需要按常见安装路径查找。
    """
    # 1. PATH 中查找
    found = shutil.which("pg_dump")
    if found:
        return found

    # 2. Windows 常见安装路径
    if sys.platform.startswith("win"):
        candidates: list[Path] = []
        for ver in ("18", "17", "16"):
            for base in (r"C:\Program Files\PostgreSQL", r"C:\Program Files (x86)\PostgreSQL"):
                candidates.append(Path(base) / ver / "bin" / "pg_dump.exe")
        for c in candidates:
            if c.is_file():
                return str(c)

    # 3. Linux 常见路径
    for p in ("/usr/bin/pg_dump", "/usr/pgsql-18/bin/pg_dump", "/usr/local/pgsql/bin/pg_dump"):
        if Path(p).is_file():
            return p

    fail(
        "找不到 pg_dump。请确认已安装 PostgreSQL 客户端工具：\n"
        "  Windows: 安装 PostgreSQL 时勾选 Command Line Tools，或把 bin 目录加入 PATH\n"
        "  Linux:   apt install postgresql-client-18"
    )
    return ""  # 不可达，仅为类型检查


def to_pg_dsn(url: str) -> str:
    """SQLAlchemy 连接串 → psycopg/pg_dump 能识别的 DSN"""
    return url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def backup_dir() -> Path:
    d = Path(settings.backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def do_backup() -> int:
    """执行备份，返回 0 表示成功"""
    db_url = settings.database_url
    if not db_url:
        fail("未配置 PM_DATABASE_URL")

    pg_dump = find_pg_dump()
    out_dir = backup_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"pm_{stamp}.dump"

    log(f"开始备份 → {out_file}")
    log(f"pg_dump: {pg_dump}")

    cmd = [
        pg_dump,
        to_pg_dsn(db_url),
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

    # 校验文件
    size = out_file.stat().st_size
    if size < 1024:
        out_file.unlink(missing_ok=True)
        fail(f"备份文件过小（{size} 字节），可能失败")

    if not verify_file(out_file):
        out_file.unlink(missing_ok=True)
        fail("备份文件校验失败，已删除")

    log(f"备份完成: {size / 1024 / 1024:.1f} MB")

    # 清理旧备份
    keep = settings.backup_keep_days
    if keep > 0:
        removed = 0
        cutoff = datetime.now().timestamp() - keep * 86400
        for f in out_dir.glob("pm_*.dump"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        if removed:
            log(f"清理 {removed} 个超过 {keep} 天的旧备份")

    # 磁盘空间提示
    try:
        usage = shutil.disk_usage(out_dir)
        pct = usage.used / usage.total * 100
        log(f"备份目录所在分区使用率: {pct:.0f}%")
        if pct > 85:
            log("警告: 磁盘使用率超过 85%，请及时清理")
    except OSError:
        pass

    log("全部完成")
    return 0


def verify_file(path: Path) -> bool:
    """用 pg_restore --list 校验文件结构"""
    pg_restore = shutil.which("pg_restore")
    if not pg_restore:
        # 同目录下找
        pg_dump = find_pg_dump()
        candidate = Path(pg_dump).parent / (
            "pg_restore.exe" if sys.platform.startswith("win") else "pg_restore"
        )
        if candidate.is_file():
            pg_restore = str(candidate)
        else:
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


def do_verify() -> int:
    out_dir = backup_dir()
    files = sorted(out_dir.glob("pm_*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        fail("没有找到备份文件")

    latest = files[0]
    log(f"校验: {latest}")
    if verify_file(latest):
        log("校验通过（文件结构完整）")
        return 0
    fail(f"备份文件损坏: {latest}")
    return 1


def do_list() -> int:
    out_dir = backup_dir()
    files = sorted(out_dir.glob("pm_*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
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
    parser = argparse.ArgumentParser(description="数据库备份工具")
    parser.add_argument("--verify", action="store_true", help="只校验最近一次备份")
    parser.add_argument("--list", action="store_true", help="列出已有备份")
    args = parser.parse_args()

    if args.verify:
        sys.exit(do_verify())
    if args.list:
        sys.exit(do_list())
    sys.exit(do_backup())


if __name__ == "__main__":
    main()
