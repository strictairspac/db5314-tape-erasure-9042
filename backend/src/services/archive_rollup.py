"""Merge per-batch tape details into one anonymous cross-batch archive.

A batch detail snapshot is captured when a run erases tapes.  It is kept in the
private relational copy only and never included in the public evidence report.
Signing a review merges every signed batch snapshot with the batch being signed:

* the same source path counts once, even when several batches recorded it;
* one performer counts once per ``(track, duration band)`` group;
* a path recorded with contradictory statistics defers the whole signature;
* signed evidence without a captured snapshot is reported as a rollup gap.

Only the merged group counts leave this module.  Source paths and performer
identifiers never do.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, NamedTuple

from .tape_erasure import BANDS, MIN_PERFORMERS, TRACKS, band_of


ARCHIVE_SUMMARY = '跨批次匿名汇总'


class ArchiveConflictError(ValueError):
    """The same tape path was recorded with contradictory statistics."""

    def __init__(self, object_keys):
        self.object_keys = tuple(sorted(object_keys))
        super().__init__('同一样带的统计信息不一致，需先核对类别、时长与演员后重新签署')


class TapeDetail(NamedTuple):
    object_key: str
    track: str
    duration_seconds: int
    performer_id: str


@dataclass(frozen=True)
class ArchiveGroup:
    track: str
    band: int
    tape_count: int
    performer_count: int


@dataclass(frozen=True)
class ArchiveRollup:
    groups: tuple[ArchiveGroup, ...]
    counted_batches: tuple[int, ...]
    gap_batches: tuple[int, ...]
    tape_total: int
    qualified_tapes: int

    @property
    def complete(self):
        return not self.gap_batches


def detail_rows(details: Iterable[Mapping | TapeDetail]) -> list[TapeDetail]:
    """Normalise detail rows read from storage into immutable tape details."""
    rows = []
    for row in details:
        if isinstance(row, TapeDetail):
            rows.append(row)
            continue
        rows.append(TapeDetail(row['object_key'], row['track'],
                               int(row['duration_seconds']), row['performer_id']))
    return rows


def deleted_tape_details(before, plan) -> list[TapeDetail]:
    """Return the statistics of the tapes erased by one batch, sorted by path."""
    details = [
        TapeDetail(before.assets[asset_id].object_key, before.assets[asset_id].track,
                   int(before.assets[asset_id].duration_seconds), before.assets[asset_id].performer_id)
        for asset_id in plan.deletion_order
        if before.assets[asset_id].kind == 'tape'
    ]
    return sorted(details)


def build_rollup(batches: Mapping[int, Iterable[Mapping | TapeDetail] | None],
                 min_performers: int = MIN_PERFORMERS) -> ArchiveRollup:
    """Merge signed batch details into anonymous ``(track, band)`` groups.

    ``batches`` maps a signed evidence id to its captured detail rows.  A
    ``None`` value marks a legacy signature whose details cannot be checked;
    it is reported as a gap instead of being silently treated as an empty
    batch.  Raises :class:`ArchiveConflictError` when one path carries more
    than one set of statistics.
    """
    stats: dict[str, TapeDetail] = {}
    conflicts: set[str] = set()
    counted, gaps = [], []
    for batch_id in sorted(batches):
        rows = batches[batch_id]
        if rows is None:
            gaps.append(batch_id)
            continue
        counted.append(batch_id)
        for detail in detail_rows(rows):
            if detail.track not in TRACKS or not 1 <= detail.duration_seconds <= 86400:
                raise ValueError('样带明细的类别或时长无效')
            previous = stats.get(detail.object_key)
            if previous is None:
                stats[detail.object_key] = detail
            elif previous != detail:
                conflicts.add(detail.object_key)
    if conflicts:
        raise ArchiveConflictError(conflicts)

    tapes = defaultdict(int)
    performers = defaultdict(set)
    for detail in stats.values():
        group = (detail.track, band_of(detail.duration_seconds))
        tapes[group] += 1
        performers[group].add(detail.performer_id)

    order = {track: index for index, track in enumerate(TRACKS)}
    groups = tuple(
        ArchiveGroup(group[0], group[1], tapes[group], len(performers[group]))
        for group in sorted(tapes, key=lambda key: (order[key[0]], key[1]))
        if len(performers[group]) >= min_performers
    )
    qualified_tapes = sum(group.tape_count for group in groups)
    return ArchiveRollup(groups, tuple(counted), tuple(gaps), len(stats), qualified_tapes)


def public_report(rollup: ArchiveRollup) -> dict:
    """Render the rollup without source paths or performer identifiers."""
    gap = len(rollup.gap_batches)
    summary = f'跨批次匿名汇总涵盖 {len(rollup.counted_batches)} 个已签署批次'
    if gap:
        summary += f'；另有 {gap} 个旧签署批次缺少可核对明细，形成汇总缺口'
    return {
        'summary': summary + '。',
        'metrics': {
            '计入批次': len(rollup.counted_batches),
            '计入样带': rollup.tape_total,
            '合格样带': rollup.qualified_tapes,
            '合格分组': len(rollup.groups),
            '汇总缺口批次': gap,
        },
        'groups': [
            {'试镜类别': TRACKS[group.track], '时长区间': BANDS[group.band],
             '样带数': group.tape_count, '演员数': group.performer_count}
            for group in rollup.groups
        ],
        'gap_batches': list(rollup.gap_batches),
    }


EMPTY_REPORT = {
    'summary': '尚无复核签署的批次，匿名归档暂未生成跨批次汇总。',
    'metrics': {'计入批次': 0, '计入样带': 0, '合格样带': 0, '合格分组': 0, '汇总缺口批次': 0},
    'groups': [],
    'gap_batches': [],
}


def empty_report():
    """Return a fresh copy so callers cannot mutate the shared template."""
    return {'summary': EMPTY_REPORT['summary'], 'metrics': dict(EMPTY_REPORT['metrics']),
            'groups': [], 'gap_batches': []}
