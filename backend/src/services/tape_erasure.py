"""Plan erasure on a private relational catalog and verify the applied state."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing, contextmanager
from dataclasses import dataclass
from heapq import heapify, heappop, heappush
from typing import NamedTuple


KINDS = {'tape': '样带', 'transcript': '转写', 'master': '母版', 'audio': '音轨', 'still': '定帧'}
TRACKS = {'dialogue': '对白', 'movement': '形体', 'voice': '配音'}
MIN_PERFORMERS = 3


class Asset(NamedTuple):
    asset_id: str
    kind: str
    object_key: str
    expires_on: str | None
    track: str | None
    duration_seconds: int | None
    performer_id: str | None


class Hold(NamedTuple):
    contract_id: str
    asset_id: str
    hold_until: str | None

    def active_on(self, cutoff):
        return self.hold_until is None or self.hold_until >= cutoff


@dataclass
class Catalog:
    assets: dict[str, Asset]
    references: set[tuple[str, str]]
    holds: set[Hold]


@dataclass
class Plan:
    deletion_order: list[str]
    due_tapes: set[str]
    candidates: set[str]
    direct_holds: set[str]
    held_dependencies: set[str]
    incoming: dict[str, set[str]]


class ErasureInvariantError(RuntimeError):
    pass


def plan_erasure(catalog, cutoff):
    outgoing = {asset_id: set() for asset_id in catalog.assets}
    incoming = {asset_id: set() for asset_id in catalog.assets}
    for referrer, dependency in catalog.references:
        outgoing[referrer].add(dependency)
        incoming[dependency].add(referrer)

    def reachable(roots):
        seen = set(roots)
        pending = list(seen)
        while pending:
            for dependency in outgoing[pending.pop()] - seen:
                seen.add(dependency)
                pending.append(dependency)
        return seen

    # Edges run from referrers to dependencies, which is also the deletion direction.
    indegree = {asset_id: len(parents) for asset_id, parents in incoming.items()}
    ready = [asset_id for asset_id, count in indegree.items() if count == 0]
    heapify(ready)
    order = []
    while ready:
        asset_id = heappop(ready)
        order.append(asset_id)
        for dependency in outgoing[asset_id]:
            indegree[dependency] -= 1
            if indegree[dependency] == 0:
                heappush(ready, dependency)
    if len(order) != len(catalog.assets):
        raise ValueError('引用依赖存在循环，无法排定擦除顺序')

    tapes = {asset.asset_id for asset in catalog.assets.values() if asset.kind == 'tape'}
    due_tapes = {asset_id for asset_id in tapes if catalog.assets[asset_id].expires_on <= cutoff}
    candidates = reachable(due_tapes)
    direct_holds = {hold.asset_id for hold in catalog.holds if hold.active_on(cutoff)}
    held_dependencies = reachable(direct_holds)
    surviving_roots = (catalog.assets.keys() - candidates) | (tapes - due_tapes)
    protected = reachable(surviving_roots) | held_dependencies
    deletable = candidates - protected
    deletion_order = [asset_id for asset_id in order if asset_id in deletable]
    return Plan(deletion_order, due_tapes, candidates, direct_holds, held_dependencies, incoming)


SCHEMA = '''
CREATE TABLE assets (
    asset_id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL,
    object_key TEXT NOT NULL,
    expires_on TEXT,
    track TEXT,
    duration_seconds INTEGER,
    performer_id TEXT
);
CREATE TABLE asset_references (
    referrer TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    dependency TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    PRIMARY KEY (referrer, dependency)
);
CREATE INDEX asset_references_dependency ON asset_references(dependency);
CREATE TABLE contract_holds (
    contract_id TEXT NOT NULL,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    hold_until TEXT,
    PRIMARY KEY (asset_id, contract_id)
);
CREATE TABLE anonymous_archive (
    track TEXT NOT NULL,
    duration_band INTEGER NOT NULL,
    tape_count INTEGER NOT NULL,
    performer_count INTEGER NOT NULL,
    PRIMARY KEY (track, duration_band)
);
'''


@contextmanager
def catalog_copy(catalog):
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('PRAGMA secure_delete = ON')
        db.executescript(SCHEMA)
        with db:
            db.executemany('INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)', catalog.assets.values())
            db.executemany('INSERT INTO asset_references VALUES (?, ?)', catalog.references)
            db.executemany('INSERT INTO contract_holds VALUES (?, ?, ?)', catalog.holds)
        yield db


def read_catalog(db):
    return Catalog(
        {row[0]: Asset(*row) for row in db.execute('SELECT * FROM assets')},
        set(db.execute('SELECT referrer, dependency FROM asset_references')),
        {Hold(*row) for row in db.execute('SELECT contract_id, asset_id, hold_until FROM contract_holds')},
    )


def write_archive(db, deletion_order):
    db.execute('CREATE TEMP TABLE erasure_scope (asset_id TEXT PRIMARY KEY NOT NULL)')
    db.executemany('INSERT INTO erasure_scope VALUES (?)', ((asset_id,) for asset_id in deletion_order))
    db.execute('''
        INSERT INTO anonymous_archive (track, duration_band, tape_count, performer_count)
        SELECT track, CASE WHEN duration_seconds < 60 THEN 0
                           WHEN duration_seconds < 180 THEN 1 ELSE 2 END AS duration_band,
               COUNT(*), COUNT(DISTINCT performer_id)
        FROM assets JOIN erasure_scope USING (asset_id)
        WHERE kind = 'tape'
        GROUP BY track, duration_band
        HAVING COUNT(DISTINCT performer_id) >= ?
    ''', (MIN_PERFORMERS,))
    db.execute('DROP TABLE erasure_scope')


def verify_applied_state(db, before, after, plan, cutoff, archive):
    deleted = set(plan.deletion_order)
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ErasureInvariantError('擦除后存在悬空引用')
    if any(hold.active_on(cutoff) and hold not in after.holds for hold in before.holds):
        raise ErasureInvariantError('生效合同关联丢失')
    if not plan.held_dependencies <= after.assets.keys():
        raise ErasureInvariantError('合同保护的依赖丢失')
    if after.assets != {key: asset for key, asset in before.assets.items() if key not in deleted}:
        raise ErasureInvariantError('实际资产与擦除计划不一致')
    if after.references != {edge for edge in before.references if edge[0] not in deleted}:
        raise ErasureInvariantError('留存资产的引用发生变化')
    if after.holds != {hold for hold in before.holds if hold.asset_id not in deleted}:
        raise ErasureInvariantError('留存资产的合同关联发生变化')
    if deleted & (before.assets.keys() - plan.candidates) or any(
        before.assets[key].kind == 'tape' and before.assets[key].expires_on > cutoff for key in deleted
    ):
        raise ErasureInvariantError('擦除范围包含未到期或无关资产')

    cohorts = defaultdict(list)
    for asset_id in deleted & plan.due_tapes:
        asset = before.assets[asset_id]
        band = int(asset.duration_seconds >= 60) + int(asset.duration_seconds >= 180)
        cohorts[asset.track, band].append(asset.performer_id)
    expected_archive = []
    for (track, band), performers in sorted(cohorts.items()):
        performer_count = len(set(performers))
        if performer_count >= MIN_PERFORMERS:
            expected_archive.append((track, band, len(performers), performer_count))
    if archive != expected_archive:
        raise ErasureInvariantError('匿名归档的分组、人数或样带数不符')


def apply_erasure(db, cutoff):
    with db:
        db.execute('BEGIN')
        before = read_catalog(db)
        plan = plan_erasure(before, cutoff)
        write_archive(db, plan.deletion_order)
        for asset_id in plan.deletion_order:
            db.execute('DELETE FROM asset_references WHERE referrer = ?', (asset_id,))
            db.execute('DELETE FROM contract_holds WHERE asset_id = ? AND hold_until < ?', (asset_id, cutoff))
            db.execute('DELETE FROM assets WHERE asset_id = ?', (asset_id,))
        after = read_catalog(db)
        archive = db.execute('SELECT * FROM anonymous_archive ORDER BY track, duration_band').fetchall()
        verify_applied_state(db, before, after, plan, cutoff, archive)
    return before, after, plan, archive
