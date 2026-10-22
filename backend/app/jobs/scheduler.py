"""定时任务：APScheduler + PG advisory lock（防多实例重复执行）

任务清单：
- 每日 01:00  生成进度快照 + 预警扫描
- 每 30 分钟  预警超时升级
- 每小时      报表逾期扫描（AL-06）
- 每日 08:30  每日摘要（统计落日志）
- 每日 02:00  数据库自动备份
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select, text

from app.core.config import settings
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# advisory lock ID（任意固定整数，避免与其他应用冲突）
LOCK_SNAPSHOT_SCAN = 2026100101


def _with_lock(lock_id: int, func, *args, **kwargs):
    """跨进程互斥执行

    PostgreSQL 用 advisory lock（多实例安全）
    SQLite 用文件锁（单机多进程安全）
    具体由 DatabaseLock 按当前数据库类型自动选择。
    """
    from app.db.dialect import DatabaseLock

    db = SessionLocal()
    lock = DatabaseLock(db, lock_id)
    try:
        if not lock.acquire():
            logger.debug("任务 %s 已被其他实例执行，跳过", func.__name__)
            return
        try:
            func(*args, **kwargs)
        finally:
            lock.release()
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
    """每日 08:30：预警摘要推送

    需求 3.3(3)：每天上午发一条汇总提醒，不是每条都单独发。
    统计昨日新增与当前待处置，落一条 DIGEST 通知给管理员，
    由 NotifyService 实际投递邮件；未配置 SMTP 时记 SKIPPED 不影响任务。
    """
    from app.models.alert import Alert
    from app.models.system import NotifyLog, SysUser

    db = SessionLocal()
    try:
        yesterday = date.today() - timedelta(days=1)
        day_start = datetime.combine(yesterday, time(0, 0), tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        new_yesterday = db.scalar(
            select(func.count()).select_from(Alert).where(
                Alert.trigger_at >= day_start, Alert.trigger_at < day_end
            )
        ) or 0
        pending = db.scalar(
            select(func.count()).select_from(Alert).where(Alert.status == "PENDING")
        ) or 0
        overdue = db.scalar(
            select(func.count()).select_from(Alert).where(
                Alert.status == "PENDING",
                Alert.deadline_at.is_not(None),
                Alert.deadline_at < datetime.now(UTC),
            )
        ) or 0
        red = db.scalar(
            select(func.count()).select_from(Alert).where(
                Alert.status == "PENDING", Alert.level == "RED"
            )
        ) or 0
        logger.info(
            "每日摘要：昨日新增预警 %s 条；当前待处置 %s 条（其中已超时 %s 条，红色 %s 条）",
            new_yesterday, pending, overdue, red,
        )

        # 收件人：系统管理员（摘要发给全局管理者，不按数据范围拆分）
        admins = list(
            db.scalars(
                text(
                    "SELECT u.id FROM sys_user u "
                    "JOIN sys_user_role ur ON ur.user_id=u.id "
                    "JOIN sys_role r ON r.id=ur.role_id "
                    "WHERE r.code='SYS_ADMIN' AND u.status='ACTIVE'"
                )
            )
        )
        if not admins:
            logger.warning("每日摘要：未找到启用的系统管理员，跳过推送")
            return

        stats = json.dumps(
            {
                "new_yesterday": new_yesterday,
                "pending": pending,
                "overdue": overdue,
                "red": red,
                "date": yesterday.isoformat(),
            },
            ensure_ascii=False,
        )
        from app.services.notify_service import NotifyService, PENDING_MARK

        svc = NotifyService(db)
        for uid in admins:
            row = NotifyLog(
                biz_type="DIGEST",
                biz_id=None,
                channel="EMAIL",
                receiver_id=uid,
                status="FAILED",
                error=stats,  # 正文数据暂存在 error，发送成功后清空
            )
            db.add(row)
            db.flush()
            svc.deliver(row.id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("每日摘要推送失败")
    finally:
        db.close()


def job_database_backup() -> None:
    """每日 02:00：数据库自动备份

    调用 scripts/backup.py（跨平台，Windows/Linux 通用）。
    """
    import subprocess
    import sys as _sys

    # app/jobs/scheduler.py → 上溯到 backend/
    backend_root = Path(__file__).resolve().parent.parent.parent
    script = backend_root / "scripts" / "backup.py"

    if not script.is_file():
        logger.warning("未找到备份脚本 %s，跳过备份", script)
        return

    logger.info("开始数据库备份（%s）...", script)
    try:
        # 用当前 Python 解释器执行，确保能用同一虚拟环境
        result = subprocess.run(
            [_sys.executable, str(script)],
            capture_output=True,
            timeout=1800,
            cwd=str(backend_root),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        # 手动解码：Windows 默认 GBK，子进程输出 UTF-8 会解码失败
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        for line in stdout.strip().splitlines():
            logger.info("[backup] %s", line)
        if result.returncode != 0:
            logger.error("[backup] 备份失败: %s", stderr.strip()[:500])
        else:
            logger.info("数据库备份完成")
    except subprocess.TimeoutExpired:
        logger.error("数据库备份超时（超过 30 分钟）")
    except Exception:
        logger.exception("数据库备份异常")


def job_retry_notify() -> None:
    """每 10 分钟：重投失败的通知

    邮件/短信可能因网络抖动或网关限流失败，这里按 MAX_RETRY 上限重试。
    成功即清空 error，超过上限的保持 FAILED 供人工排查。
    """
    from app.services.notify_service import NotifyService

    db = SessionLocal()
    try:
        stats = NotifyService(db).retry_pending()
        if any(stats.values()):
            logger.info(
                "通知重投：成功 %s 条，失败 %s 条，跳过 %s 条",
                stats["success"], stats["failed"], stats["skipped"],
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("通知重投失败")
    finally:
        db.close()


# ---------------------------------------------------------------- 启停
def start_scheduler() -> None:
    global _scheduler
    if not settings.scheduler_enabled:
        logger.info("定时任务未启用（PM_SCHEDULER_ENABLED=false）")
        return
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    # 摘要时刻：参数优先（notify.digest.hour/minute），未配置时回落到环境变量。
    # 原来只读 settings，导致参数配置页改这两项不生效。
    digest_hour, digest_minute = settings.digest_hour, settings.digest_minute
    try:
        from app.db.session import SessionLocal
        from app.services.config_service import ConfigService

        _db = SessionLocal()
        try:
            _cfg = ConfigService(_db)
            digest_hour = _cfg.get_int("notify.digest.hour", digest_hour)
            digest_minute = _cfg.get_int("notify.digest.minute", digest_minute)
        finally:
            _db.close()
    except Exception:
        logger.warning("读取摘要时刻参数失败，沿用环境变量默认值", exc_info=True)

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
        CronTrigger(hour=digest_hour, minute=digest_minute),
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

    # 每 10 分钟重投失败通知
    _scheduler.add_job(
        lambda: _with_lock(LOCK_SNAPSHOT_SCAN + 5, job_retry_notify),
        CronTrigger(minute="*/10"),
        id="retry_notify",
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
