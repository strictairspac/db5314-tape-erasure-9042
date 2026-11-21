from sqlalchemy import (JSON, Column, ForeignKey, Integer, MetaData, String,
                        Table, Text)

schema = MetaData()
users = Table('accounts', schema,
    Column('id', Integer, primary_key=True),
    Column('username', String(40), nullable=False, unique=True),
    Column('password_hash', String(200), nullable=False),
    Column('role', String(20), nullable=False))
cases = Table('business_cases', schema,
    Column('id', Integer, primary_key=True),
    Column('slug', String(64), nullable=False, unique=True),
    Column('title', String(120), nullable=False),
    Column('payload', JSON, nullable=False),
    Column('author', String(40), nullable=False),
    Column('created_at', String(32), nullable=False))
runs = Table('business_runs', schema,
    Column('id', Integer, primary_key=True),
    Column('case_id', ForeignKey('business_cases.id'), nullable=False, index=True),
    Column('status', String(20), nullable=False, index=True),
    Column('owner', String(40), nullable=False),
    Column('attempt_token', String(36)),
    Column('started_at', String(32)),
    Column('finished_at', String(32)),
    Column('created_at', String(32), nullable=False),
    Column('error', Text))
evidence = Table('business_evidence', schema,
    Column('id', Integer, primary_key=True),
    Column('run_id', ForeignKey('business_runs.id'), nullable=False, unique=True),
    Column('report', JSON, nullable=False),
    Column('digest', String(64), nullable=False),
    Column('reviewer', String(40)),
    Column('review_note', String(500)),
    Column('reviewed_at', String(32)))
audit = Table('audit_events', schema,
    Column('id', Integer, primary_key=True),
    Column('actor', String(40), nullable=False),
    Column('action', String(32), nullable=False),
    Column('reference', String(50), nullable=False),
    Column('created_at', String(32), nullable=False))
# Private per-batch snapshot of erased tapes. Rows stay in the database and are
# never returned to readers; only merged counts reach the anonymous archive.
batch_snapshots = Table('archive_batch_snapshots', schema,
    Column('id', Integer, primary_key=True),
    Column('evidence_id', ForeignKey('business_evidence.id'), nullable=False, unique=True),
    Column('captured_at', String(32), nullable=False))
batch_details = Table('archive_batch_details', schema,
    Column('id', Integer, primary_key=True),
    Column('evidence_id', ForeignKey('business_evidence.id'), nullable=False, index=True),
    Column('object_key', String(400), nullable=False),
    Column('track', String(20), nullable=False),
    Column('duration_seconds', Integer, nullable=False),
    Column('performer_id', String(80), nullable=False))
archive_rollup = Table('archive_rollup', schema,
    Column('id', Integer, primary_key=True),
    Column('report', JSON, nullable=False),
    Column('updated_at', String(32), nullable=False))
