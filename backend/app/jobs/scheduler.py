"""定时任务：APScheduler + PG advisory lock（防多实例重复执行）

任务清单：
- 每日 01:00  生成进度快照 + 预警扫描
- 每 30 分钟  预警超时升级
- 每小时      报表逾期扫描（AL-06）
- 每日 08:30  每日摘要（邮件）
"""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# advisory lock ID（任意固定整数，避免与其他应用冲突）
LOCK_SNAPSHOT_SCAN = 2026100101


def _with_lock(lock_id: int, func, *args, **kwargs):
    """用 PG advisory lock 保证多实例下只有一个执行"""
    db = SessionLocal()
    try:
        got = db.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}).scalar()
        if not got:
            logger.debug("任务 %s 已被其他实例执行，跳过", func.__name__)
            return
        try:
            func(*args, **kwargs)
        finally:
            db.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
            db.commit()
    except Exception:
        logger.exception("定时任务执行失败：%s", func.__name__)
    finally:
        db.close()


# ---------------------------------------------------------------- 任务
def job_daily_snapshot_and_scan() -> None:
    """每日：快照 + 预警扫描"""
    from app.services.alert_service import AlertService
    from app.services.progress_service import ProgressService

    db = SessionLocal()
    try:
        today = date.today()
        svc = ProgressService(db)
        snap = svc.generate_snapshots(today)
        logger.info("快照生成：%s", snap)

        alert = AlertService(db)
        result = alert.scan(today)
        logger.info("预警扫描：%s", result)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("每日快照+扫描失败")
    finally:
        db.close()


def job_escalate() -> None:
    """每 30 分钟：超时升级"""
    from app.services.alert_service import AlertService

    db = SessionLocal()
    try:
        result = AlertService(db).escalate()
        if result["escalated"]:
            logger.info("预警升级：%s 条", result["escalated"])
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("预警升级失败")
    finally:
        db.close()


def job_hourly_scan() -> None:
    """每小时：报表逾期扫描（AL-06）"""
    from app.services.alert_service import AlertService

    db = SessionLocal()
    try:
        result = AlertService(db).scan(date.today())
        created = result.get("created", 0)
        if created:
            logger.info("小时扫描新建预警：%s 条", created)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("小时扫描失败")
    finally:
        db.close()


def job_daily_digest() -> None:
    """每日 08:30：摘要（一期仅记日志，邮件发送待接入）"""
    from app.services.alert_service import AlertService

    db = SessionLocal()
    try:
        alerts = AlertService(db).stats(None, days=1) if False else None
        # 简化：统计昨日新增并写日志
        logger.info("每日摘要在 %s:%s 触发", settings.digest_hour, settings.digest_minute)
    finally:
        db.close()


def job_database_backup() -> None:
    """每日 02:00：数据库自动备份

    调用容器内 backup.sh，由 pg_dump 生成压缩备份并清理旧文件。
    备份目录需挂载到持久化卷，否则容器重建会丢失备份。
    """
    import subprocess

    script = "/app/backup.sh"
    if not Path(script).is_file():
        logger.warning("未找到备份脚本 %s，跳过备份", script)
        return

    logger.info("开始数据库备份...")
    try:
        result = subprocess.run(
            ["sh", script],
            capture_output=True,
            text=True,
            timeout=1800,
            env={**os.environ},
        )
        for line in (result.stdout or "").strip().splitlines():
            logger.info("[backup] %s", line)
        if result.returncode != 0:
            logger.error("[backup] 备份失败: %s", (result.stderr or "").strip()[:500])
        else:
            logger.info("数据库备份完成")
    except subprocess.TimeoutExpired:
        logger.error("数据库备份超时（超过 30 分钟）")
    except Exception:
        logger.exception("数据库备份异常")


# ---------------------------------------------------------------- 启停
def start_scheduler() -> None:
    global _scheduler
    if not settings.scheduler_enabled:
        logger.info("定时任务未启用（PM_SCHEDULER_ENABLED=false）")
        return
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    # 每日 01:00 快照 + 扫描
    _scheduler.add_job(
        lambda: _with_lock(LOCK_SNAPSHOT_SCAN, job_daily_snapshot_and_scan),
        CronTrigger(hour=1, minute=0),
        id="daily_snapshot_scan",
        replace_existing=True,
    )
    # 每 30 分钟升级
    _scheduler.add_job(
        lambda: _with_lock(LOCK_SNAPSHOT_SCAN + 1, job_escalate),
        CronTrigger(minute="*/30"),
        id="escalate",
        replace_existing=True,
    )
    # 每小时报表扫描
    _scheduler.add_job(
        lambda: _with_lock(LOCK_SNAPSHOT_SCAN + 2, job_hourly_scan),
        CronTrigger(minute=5),
        id="hourly_scan",
        replace_existing=True,
    )
    # 每日 08:30 摘要
    _scheduler.add_job(
        lambda: _with_lock(LOCK_SNAPSHOT_SCAN + 3, job_daily_digest),
        CronTrigger(hour=settings.digest_hour, minute=settings.digest_minute),
        id="daily_digest",
        replace_existing=True,
    )
    # 每日 02:00 数据库备份（避开 01:00 的快照与扫描）
    if settings.backup_enabled:
        _scheduler.add_job(
            lambda: _with_lock(LOCK_SNAPSHOT_SCAN + 4, job_database_backup),
            CronTrigger(hour=settings.backup_hour, minute=settings.backup_minute),
            id="database_backup",
            replace_existing=True,
        )

    _scheduler.start()
    logger.info("定时任务已启动：%s", [j.id for j in _scheduler.get_jobs()])


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("定时任务已停止")
