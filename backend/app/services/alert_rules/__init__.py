"""预警规则注册表"""
from __future__ import annotations

from app.services.alert_rules.base import AlertCandidate, AlertRule, RuleContext
from app.services.alert_rules.progress_rules import (
    AL01ProgressLag,
    AL02SevereLag,
    AL03Stagnant,
    AL04Rollback,
    AL05NearDeadline,
    AL09NoOwner,
    AL10NoPlan,
    AL11ZeroProgress,
)
from app.services.alert_rules.report_risk_rules import (
    AL06ReportOverdue,
    AL07ConsecutiveMissing,
    AL08RiskOverdue,
)


def build_rules() -> list[AlertRule]:
    return [
        AL01ProgressLag(),
        AL02SevereLag(),
        AL03Stagnant(),
        AL04Rollback(),
        AL05NearDeadline(),
        AL06ReportOverdue(),
        AL07ConsecutiveMissing(),
        AL08RiskOverdue(),
        AL09NoOwner(),
        AL10NoPlan(),
        AL11ZeroProgress(),
    ]


# 按级别排序（红色优先）
LEVEL_ORDER = {"RED": 0, "ORANGE": 1, "YELLOW": 2}

# 不可关闭、不可忽略的规则
IMMUTABLE_TYPES = {"AL-09", "AL-10"}

# 依赖「当日进度快照」的规则。
# 快照由每日 01:00 任务生成；若某次扫描时当天快照不存在（例如手动扫描
# 或小时任务提前跑），这些规则会全部「未命中」，_auto_close 就会把在办
# 预警批量误关。scan() 用本集合做保护：当天无快照时整组跳过（P0-5）。
SNAPSHOT_RULE_CODES = {"AL-01", "AL-02", "AL-03", "AL-11"}


def sort_candidates(items: list[AlertCandidate]) -> list[AlertCandidate]:
    return sorted(items, key=lambda c: (LEVEL_ORDER.get(c.level, 9), c.alert_type))


__all__ = [
    "AlertRule",
    "AlertCandidate",
    "RuleContext",
    "build_rules",
    "LEVEL_ORDER",
    "IMMUTABLE_TYPES",
    "SNAPSHOT_RULE_CODES",
    "sort_candidates",
]
