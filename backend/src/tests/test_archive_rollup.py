from src.services.archive_rollup import ArchiveConflictError, TapeDetail, build_rollup


def test_rollup_deduplicates_paths_and_performers():
    a = [TapeDetail('shared/take.mov', 'dialogue', 72, 'actor-a'), TapeDetail('a/take.mov', 'dialogue', 72, 'actor-b')]
    b = [TapeDetail('shared/take.mov', 'dialogue', 72, 'actor-a'), TapeDetail('b/take.mov', 'dialogue', 72, 'actor-c')]
    rollup = build_rollup({10: a, 11: b})
    assert [(item.tape_count, item.performer_count) for item in rollup.groups] == [(3, 3)]


def test_rollup_rejects_conflicts_and_reports_legacy_gap():
    try:
        build_rollup({10: [TapeDetail('shared/take.mov', 'dialogue', 72, 'actor-a')],
                      11: [TapeDetail('shared/take.mov', 'voice', 72, 'actor-a')]})
    except ArchiveConflictError as error:
        assert error.object_keys == ('shared/take.mov',)
    else:
        raise AssertionError('conflict was accepted')
    legacy = build_rollup({10: None})
    assert legacy.gap_batches == (10,) and legacy.complete is False
