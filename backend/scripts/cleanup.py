# -*- coding: utf-8 -*-
"""数据保留清理脚本（跨平台：Windows / Linux 通用）

## 为什么需要

系统参数里有 `system.audit_retention_years`（默认 2 年）与
`system.snapshot_retention_years`（默认 3 年），但**全仓没有任何消费方**：
`audit_log`（每次写操作一条）与 `prog_snapshot`（每子项目每天一条）只增不减。
SQLite 单文件会持续膨胀，备份变慢、查询变慢，而文档里承诺的"保留 N 年"是空话。

## 清什么

- `audit_log.created_at` 早于 N 年前的记录
- `prog_snapshot.snap_date` 早于 N 年前的快照

## 用法

    python scripts/cleanup.py                # 真正删除
    python scripts/cleanup.py --dry-run      # 只统计不删除（建议先跑这个）
    python scripts/cleanup.py --audit-only   # 只清审计日志
    python scripts/cleanup.py --snapshot-only

定时任务：由 `app/jobs/scheduler.py` 在 `cleanup_enabled=true` 时每日
`cleanup_hour` 点调用（该注册由 A 组在 scheduler 里加，见修复报告）。

## 安全设计

1. 默认 `PM_CLEANUP_ENABLED=false` —— 删除历史数据不可逆，必须先显式开启；
2. 保留期从 `sys_config` 读（管理员在参数页可改），读不到才回退环境变量；
3. 支持 `--dry-run`，运维可以先看清楚要删多少条再执行；
4. 单次删除按批次（默认 5000 条）提交，避免长事务锁库。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.project import ProgSnapshot  # noqa: E402
from app.models.system import AuditLog  # noqa: E402

BATCH_SIZE = 5000


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _retention_years(key: str, fallback: int) -> int:
    """保留年数：参数页优先，读不到用环境变量默认值"""
    try:
        from app.services.config_service import ConfigService

        with SessionLocal() as db:
            value = ConfigService(db).get_int(key, fallback)
        return value if value > 0 else fallback
    except Exception:
        log(f"读取参数 {key} 失败，使用默认值 {fallback}")
        return fallback


def _years_ago(years: int) -> date:
    """N 年前的今天（用 365 天近似，闰年差异对清理任务无影响）"""
    from datetime import timedelta

    return date.today() - timedelta(days=365 * years)


def _years_ago_dt(years: int) -> datetime:
    """N 年前的此刻（UTC aware）—— audit_log.created_at 是时间戳列"""
    from datetime import UTC, timedelta

    return datetime.now(UTC) - timedelta(days=365 * years)


def cleanup_audit_logs(years: int, dry_run: bool) -> int:
    cutoff = _years_ago_dt(years)
    with SessionLocal() as db:
        total = db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.created_at < cutoff)
        ) or 0
        log(f"审计日志：{cutoff:%Y-%m-%d} 之前共 {total} 条（保留 {years} 年）")
        if dry_run or total == 0:
            return total

        removed = 0
        while True:
            # 分批删除：先查出这一批的主键再按主键删，兼容两种数据库
            ids = list(
                db.scalars(
                    select(AuditLog.id)
                    .where(AuditLog.created_at < cutoff)
                    .limit(BATCH_SIZE)
                )
            )
            if not ids:
                break
            db.execute(delete(AuditLog).where(AuditLog.id.in_(ids)))
            db.commit()
            removed += len(ids)
            if removed % (BATCH_SIZE * 10) == 0:
                log(f"  已删除 {removed}/{total} 条...")
        log(f"审计日志：已删除 {removed} 条")
        return removed


def cleanup_snapshots(years: int, dry_run: bool) -> int:
    cutoff = _years_ago(years)
    with SessionLocal() as db:
        total = db.scalar(
            select(func.count()).select_from(ProgSnapshot).where(ProgSnapshot.snap_date < cutoff)
        ) or 0
        log(f"进度快照：{cutoff} 之前共 {total} 条（保留 {years} 年）")
        if dry_run or total == 0:
            return total

        removed = 0
        while True:
            # ProgSnapshot 是复合主键（sub_id, snap_date），按日期批次删除即可
            dates = list(
                db.scalars(
                    select(ProgSnapshot.snap_date)
                    .where(ProgSnapshot.snap_date < cutoff)
                    .distinct()
                    .limit(BATCH_SIZE)
                )
            )
            if not dates:
                break
            result = db.execute(
                delete(ProgSnapshot).where(ProgSnapshot.snap_date.in_(dates))
            )
            db.commit()
            removed += result.rowcount or 0
            if len(dates) < BATCH_SIZE:
                break
        log(f"进度快照：已删除 {removed} 条")
        return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="数据保留清理（审计日志 / 进度快照）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不删除")
    parser.add_argument("--audit-only", action="store_true", help="只清理审计日志")
    parser.add_argument("--snapshot-only", action="store_true", help="只清理进度快照")
    args = parser.parse_args()

    # 定时任务在 cleanup_enabled=false 时不应误删；手工执行时不受该开关限制，
    # 否则运维想手动清一次还得先去改配置。
    audit_years = _retention_years(
        "system.audit_retention_years", settings.audit_retention_years
    )
    snapshot_years = _retention_years(
        "system.snapshot_retention_years", settings.snapshot_retention_years
    )

    log(f"开始清理（{'DRY-RUN 只统计' if args.dry_run else '实际删除'}）")
    log(f"保留策略：审计日志 {audit_years} 年 / 进度快照 {snapshot_years} 年")

    do_audit = not args.snapshot_only
    do_snapshot = not args.audit_only

    if do_audit:
        cleanup_audit_logs(audit_years, args.dry_run)
    if do_snapshot:
        cleanup_snapshots(snapshot_years, args.dry_run)

    log("清理完成")
    if args.dry_run:
        log("（DRY-RUN：未删除任何数据；去掉 --dry-run 才真正执行）")


if __name__ == "__main__":
    main()
