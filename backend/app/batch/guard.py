"""적재 건수 가드 (FR-612, BR-14, ERR-014).

새 적재 결과의 작품·촬영지 수가 직전 성공보다 30% 이상 줄었거나
최소 기준(작품 50편, 촬영지 150곳)에 못 미치면 반영하지 않는다.
"""

from dataclasses import dataclass

from app.batch.report import SyncCounts

MIN_WORKS = 50
MIN_LOCATIONS = 150
MAX_DROP_PERCENT = 30  # 이 비율 '이상' 줄면 막는다


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    reasons: tuple[str, ...] = ()


def _check(label: str, unit: str, new: int, prev: int | None, minimum: int) -> list[str]:
    reasons = []
    if new < minimum:
        reasons.append(f"{label} {new}{unit}이 최소 기준 {minimum}{unit}보다 적습니다")
    # 정수로 비교해 경계값(정확히 30%)에서 소수 오차가 없게 한다
    if prev and (prev - new) * 100 >= MAX_DROP_PERCENT * prev:
        drop = (prev - new) / prev * 100
        reasons.append(
            f"{label}가 직전 {prev}{unit}에서 {new}{unit}으로 {drop:.1f}% 줄었습니다"
            f"({MAX_DROP_PERCENT}% 이상 감소)"
        )
    return reasons


def check(new: SyncCounts, prev: SyncCounts | None) -> GuardResult:
    """prev는 직전 성공 동기화의 건수. 처음 동기화하면 None이고 최소 기준만 본다."""
    reasons = _check("작품 수", "편", new.works, prev.works if prev else None, MIN_WORKS)
    reasons += _check(
        "촬영지 수", "곳", new.locations, prev.locations if prev else None, MIN_LOCATIONS
    )
    return GuardResult(passed=not reasons, reasons=tuple(reasons))
