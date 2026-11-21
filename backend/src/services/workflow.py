import hashlib
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from src.config.settings import IS_MYSQL, cache, engine
from src.constants.workflow import Status
from src.models.tables import (archive_rollup, audit, batch_details, batch_snapshots,
                               cases, evidence, runs)
from src.services import archive_rollup as rollup
from src.services import domain
from src.utils.encoding import canonical
from src.utils.time import utc_time


def log(conn, actor, action, reference):
    conn.execute(audit.insert().values(actor=actor, action=action, reference=str(reference), created_at=utc_time()))


def recent(table):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(table).order_by(table.c.id.desc()).limit(100)).mappings()]


def found(conn, table, identity):
    row = conn.execute(select(table).where(table.c.id == identity)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该记录')
    return dict(row)


def make_case(title, payload, actor):
    title = title.strip()
    if not title:
        raise HTTPException(422, '档案名称不能为空')
    try:
        if len(canonical(payload)) > 512 * 1024:
            raise ValueError('业务输入超过 512 KiB')
        if callable(getattr(domain, 'validate', None)):
            domain.validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    with engine.begin() as conn:
        case_id = conn.execute(cases.insert().values(slug=uuid.uuid4().hex, title=title, payload=payload,
                               author=actor, created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'create_case', case_id)
        return found(conn, cases, case_id)


def enqueue(case_id, actor):
    with engine.begin() as conn:
        found(conn, cases, case_id)
        run_id = conn.execute(runs.insert().values(case_id=case_id, status=Status.QUEUED,
                              owner=actor, created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'enqueue', run_id)
        result = found(conn, runs, run_id)
    try:
        cache.lpush('worker:wake', 'queued')
        cache.ltrim('worker:wake', 0, 99)
    except Exception:
        # The durable database queue is polled even if Redis is temporarily unavailable.
        pass
    return result


def claim():
    ready = or_(runs.c.status == Status.QUEUED,
                and_(runs.c.status == Status.RUNNING, runs.c.started_at < utc_time(-600)))
    with engine.begin() as conn:
        run = conn.execute(select(runs.c.id, runs.c.case_id).where(ready).order_by(runs.c.id)
                           .limit(1).with_for_update(skip_locked=True)).first()
        if not run:
            return None
        token = str(uuid.uuid4())
        conn.execute(runs.update().where(runs.c.id == run.id).values(
            started_at=utc_time(), status=Status.RUNNING, attempt_token=token))
        return run.id, token, found(conn, cases, run.case_id)['payload']


def _store_details(conn, evidence_id, details):
    """Persist the private per-batch tape snapshot used by the signed rollup."""
    conn.execute(batch_snapshots.insert().values(evidence_id=evidence_id, captured_at=utc_time()))
    if details:
        conn.execute(batch_details.insert(), [
            {'evidence_id': evidence_id, 'object_key': detail.object_key, 'track': detail.track,
             'duration_seconds': detail.duration_seconds, 'performer_id': detail.performer_id}
            for detail in details
        ])


def finish(run_id, token, result=None, details=None, error=None):
    digest = hashlib.sha256(canonical(result)).hexdigest() if result is not None else None
    status = Status.FAILED if error else Status.COMPLETE
    with engine.begin() as conn:
        count = conn.execute(runs.update().where(runs.c.id == run_id,
                        runs.c.attempt_token == token, runs.c.status == Status.RUNNING)
                        .values(status=status, finished_at=utc_time(), error=error)).rowcount
        if not count:
            return
        if result is not None:
            evidence_id = conn.execute(evidence.insert().values(
                run_id=run_id, report=result, digest=digest)).inserted_primary_key[0]
            _store_details(conn, evidence_id, details or [])
        log(conn, 'worker', status.value, run_id)


def _signed_details(conn):
    """Return ``{evidence_id: rows or None}`` for every already-signed evidence.

    ``None`` marks a legacy signature with no captured snapshot, which the
    rollup must disclose as a gap rather than silently count as empty.
    """
    # Locking reads see the latest committed signatures/details rather than the
    # transaction's opening snapshot, so a concurrent reviewer is not missed.
    signed = conn.execute(select(evidence.c.id).where(evidence.c.reviewed_at.is_not(None))
                          .with_for_update()).scalars().all()
    if not signed:
        return {}
    snapshotted = set(conn.execute(
        select(batch_snapshots.c.evidence_id).where(batch_snapshots.c.evidence_id.in_(signed))
        .with_for_update()).scalars())
    rows = {}
    for row in conn.execute(select(batch_details.c.evidence_id, batch_details.c.object_key,
                                   batch_details.c.track, batch_details.c.duration_seconds,
                                   batch_details.c.performer_id)
                            .where(batch_details.c.evidence_id.in_(signed))
                            .with_for_update()).mappings():
        rows.setdefault(row['evidence_id'], []).append(row)
    return {identity: (rows.get(identity, []) if identity in snapshotted else None) for identity in signed}


def _ensure_rollup_row(conn):
    """Lock the pre-seeded singleton published rollup row."""
    return conn.execute(select(archive_rollup.c.id).where(archive_rollup.c.id == 1)
                        .with_for_update()).scalar_one()


def _write_rollup(conn, row_id, batches):
    """Recompute and persist the merged rollup from a full snapshot under lock.

    Counts are always recomputed from every signed batch, so retrying a
    signature cannot double count.  The published row only moves forward when
    the write commits together with the signature; a failure rolls back both
    and readers keep the version they saw before.
    """
    computed = rollup.build_rollup(batches)
    conn.execute(archive_rollup.update().where(archive_rollup.c.id == row_id)
                 .values(report=rollup.public_report(computed), updated_at=utc_time()))
    return computed


def review(identity, note, actor):
    note = note.strip()
    if not note:
        raise HTTPException(422, '复核意见不能为空')
    with engine.begin() as conn:
        found(conn, evidence, identity)
        # Lock the published row first so the signed set is read after any
        # concurrent reviewer commits, then compute the new totals from it.
        row_id = _ensure_rollup_row(conn)
        count = conn.execute(evidence.update().where(evidence.c.id == identity, evidence.c.reviewed_at.is_(None))
                            .values(review_note=note, reviewer=actor, reviewed_at=utc_time())).rowcount
        if count != 1:
            raise HTTPException(409, '该证据已经复核')
        # The signature and the new rollup share one transaction: a failed
        # rollup write rolls the signature back and leaves the prior version.
        batches = _signed_details(conn)
        try:
            _write_rollup(conn, row_id, batches)
        except rollup.ArchiveConflictError as exc:
            raise HTTPException(409, str(exc)) from None
        log(conn, actor, 'review', identity)
        return found(conn, evidence, identity)


def archive_report():
    with engine.connect() as conn:
        batches = _signed_details(conn)
        try:
            return rollup.public_report(rollup.build_rollup(batches))
        except rollup.ArchiveConflictError:
            row = conn.execute(select(archive_rollup.c.report).where(archive_rollup.c.id == 1)).first()
            return dict(row[0]) if row else rollup.empty_report()


def reset_archive_for_tests():
    """Clear signed snapshots and the published rollup (test/demo helper)."""
    with engine.begin() as conn:
        conn.execute(delete(batch_details))
        conn.execute(delete(batch_snapshots))
        conn.execute(delete(archive_rollup))
