"""对白试镜的样带留存与擦除证据。"""

from datetime import date

from .tape_erasure import (
    KINDS, MIN_PERFORMERS, TRACKS, Asset, Catalog, Hold, apply_erasure, catalog_copy, plan_erasure,
)


def _demo_tape(asset_id, object_key, expires_on, track, seconds, performer_id):
    return Asset(asset_id, 'tape', object_key, expires_on, track, seconds, performer_id)._asdict()


SPEC = {
    'name': '对白试镜·样带留存到期',
    'summary': '对到期样带执行隔离擦除，核对合同与共享素材的留存情况，保留可统计的匿名归档。',
    'customer': '对白试镜工作室 · 素材与合约小组',
    'entity_labels': ['样带目录', '删除预演', '匿名归档'],
    'field_labels': {
        'cutoff': '留存检查日', 'assets': '资产目录', 'references': '引用关系', 'holds': '合同留存关联',
        'asset_id': '资产编号', 'kind': '资产类型', 'object_key': '素材对象路径', 'expires_on': '到期日',
        'track': '试镜类别', 'duration_seconds': '样带秒数', 'performer_id': '演员内部编号',
        'referrer': '引用方编号', 'dependency': '被引用资产编号', 'contract_id': '合同编号',
        'hold_until': '保护至（含当日，空值为无固定截止日）',
    },
    'guidance': '填写检查日和目录快照，同一个素材对象只登记一次，由所有使用方引用该节点。',
    'sample': {
        'cutoff': '2026-11-21',
        'assets': [
            _demo_tape('T-01', '雨夜_林芷_take1.mov', '2026-11-20', 'dialogue', 65, 'P-01'),
            _demo_tape('T-02', '雨夜_周晨_take2.mov', '2026-11-21', 'dialogue', 75, 'P-02'),
            _demo_tape('T-03', '雨夜_余舟_take1.mov', '2026-11-15', 'dialogue', 92, 'P-03'),
            _demo_tape('T-04', '走位_宋岚_take1.mov', '2026-11-16', 'movement', 25, 'P-04'),
            _demo_tape('T-05', '雨夜_谈竹_签约复核.mov', '2026-11-18', 'dialogue', 100, 'P-05'),
            _demo_tape('T-06', '雨夜_江汀_补录.mov', '2026-12-01', 'dialogue', 82, 'P-06'),
            {'asset_id': 'X-01', 'kind': 'transcript', 'object_key': '雨夜_林芷.srt'},
            {'asset_id': 'X-02', 'kind': 'transcript', 'object_key': '雨夜_周晨.srt'},
            {'asset_id': 'X-HOLD', 'kind': 'transcript', 'object_key': '雨夜_谈竹_签约复核.srt'},
            {'asset_id': 'M-SHARED', 'kind': 'master', 'object_key': '雨夜_补录母版.mxf'},
            {'asset_id': 'U-SHARED', 'kind': 'master', 'object_key': '雨夜_初试拼接.mxf'},
            {'asset_id': 'A-HOT', 'kind': 'audio', 'object_key': '雨夜_初试对白.wav'},
            {'asset_id': 'A-HOLD', 'kind': 'audio', 'object_key': '雨夜_谈竹原声.wav'},
            {'asset_id': 'A-SHARED', 'kind': 'audio', 'object_key': '雨夜_补录底音.wav'},
            {'asset_id': 'F-01', 'kind': 'still', 'object_key': '走位_合同定帧.png'},
        ],
        'references': [
            {'referrer': referrer, 'dependency': dependency} for referrer, dependency in [
                ('T-01', 'X-01'), ('T-01', 'M-SHARED'), ('T-02', 'X-02'), ('T-02', 'U-SHARED'),
                ('T-03', 'U-SHARED'), ('T-04', 'F-01'), ('T-05', 'X-HOLD'), ('T-06', 'M-SHARED'),
                ('X-01', 'A-HOT'), ('X-02', 'A-HOT'), ('X-HOLD', 'A-HOLD'), ('M-SHARED', 'A-SHARED'),
            ]
        ],
        'holds': [
            {'contract_id': 'CT-215', 'asset_id': 'T-05', 'hold_until': '2026-11-21'},
            {'contract_id': 'CT-216', 'asset_id': 'F-01', 'hold_until': None},
            {'contract_id': 'CT-208', 'asset_id': 'T-02', 'hold_until': '2026-11-20'},
        ],
    },
    'expect': {'到期样带': 5, '擦除样带': 4, '擦除资产': 8, '留存资产': 7, '合同保护资产': 4, '匿名归档组': 1},
}


def _day(value, label):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}须为有效的 YYYY-MM-DD 日期') from None
    if parsed.isoformat() != value:
        raise ValueError(f'{label}须为有效的 YYYY-MM-DD 日期')
    return value


def _text(row, key):
    value = row.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f'{SPEC["field_labels"][key]}须为非空文本，且首尾无空格')
    return value


def _fields(row, keys, label):
    if not isinstance(row, dict) or row.keys() - keys:
        raise ValueError(f'{label}包含无法识别的字段或行格式')


def _rows(payload, key):
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f'{SPEC["field_labels"][key]}须为行对象数组')
    return value


def _parse(payload):
    _fields(payload, SPEC['sample'].keys(), '留存检查单')
    cutoff = _day(payload.get('cutoff'), '留存检查日')
    assets = {}
    object_keys = set()
    tape_fields = {'expires_on', 'track', 'duration_seconds', 'performer_id'}
    for row in _rows(payload, 'assets'):
        _fields(row, {'asset_id', 'kind', 'object_key'} | tape_fields, '资产目录')
        asset_id = _text(row, 'asset_id')
        kind = _text(row, 'kind')
        object_key = _text(row, 'object_key')
        if asset_id in assets or object_key in object_keys:
            raise ValueError('资产编号或素材对象路径重复，共享素材须登记为单一节点')
        if kind not in KINDS:
            raise ValueError('资产类型须为样带、转写、母版、音轨或定帧')
        expires_on = track = seconds = performer_id = None
        if kind == 'tape':
            expires_on = _day(row.get('expires_on'), '样带到期日')
            track = _text(row, 'track')
            seconds = row.get('duration_seconds')
            performer_id = _text(row, 'performer_id')
            if track not in TRACKS or type(seconds) is not int or not 1 <= seconds <= 86400:
                raise ValueError('试镜类别须为对白、形体或配音，样带时长须为 1 至 86400 的整数秒')
        elif any(row.get(key) is not None for key in tape_fields):
            raise ValueError('样带到期日、类别、时长及演员编号仅用于样带')
        assets[asset_id] = Asset(asset_id, kind, object_key, expires_on, track, seconds, performer_id)
        object_keys.add(object_key)

    references = set()
    for row in _rows(payload, 'references'):
        _fields(row, {'referrer', 'dependency'}, '引用关系')
        edge = (_text(row, 'referrer'), _text(row, 'dependency'))
        if any(asset_id not in assets for asset_id in edge):
            raise ValueError('引用关系指向目录中不存在的资产')
        if edge in references:
            raise ValueError('引用关系重复')
        references.add(edge)

    holds = set()
    hold_keys = set()
    for row in _rows(payload, 'holds'):
        _fields(row, {'contract_id', 'asset_id', 'hold_until'}, '合同留存关联')
        contract_id = _text(row, 'contract_id')
        asset_id = _text(row, 'asset_id')
        if asset_id not in assets or (contract_id, asset_id) in hold_keys:
            raise ValueError('合同留存关联重复或指向目录中不存在的资产')
        if 'hold_until' not in row:
            raise ValueError('合同必须提供留存截止日，无固定截止日时填写 null')
        hold_until = row['hold_until']
        if hold_until is not None:
            _day(hold_until, '合同留存截止日')
        holds.add(Hold(contract_id, asset_id, hold_until))
        hold_keys.add((contract_id, asset_id))
    return cutoff, Catalog(assets, references, holds)


def validate(payload):
    cutoff, catalog = _parse(payload)
    plan_erasure(catalog, cutoff)


def _retention_reason(asset_id, plan, before):
    if asset_id in plan.direct_holds:
        return '合同留存'
    if asset_id in plan.held_dependencies:
        return '受合同保护的依赖'
    if before.assets[asset_id].kind == 'tape' and asset_id not in plan.due_tapes:
        return '样带未到期'
    if asset_id in plan.candidates:
        return '仍有留存引用'
    return '本批擦除范围外'


def solve(payload, engine):
    cutoff, catalog = _parse(payload)
    with catalog_copy(catalog) as db:
        before, after, plan, archive = apply_erasure(db, cutoff)

    deleted = set(plan.deletion_order)
    tape_count = len(deleted & plan.due_tapes)
    nodes = {asset_id: f'N{n:03d}' for n, asset_id in enumerate(sorted(before.assets), start=1)}
    steps = {asset_id: n for n, asset_id in enumerate(plan.deletion_order, start=2)}
    execution = [{'顺序': 1, '节点': '归档汇总', '操作': '生成合格分组并移除暂存关联', '前置步骤': '无'}]
    for asset_id in plan.deletion_order:
        predecessors = sorted(steps[referrer] for referrer in plan.incoming[asset_id])
        execution.append({
            '顺序': steps[asset_id], '节点': nodes[asset_id],
            '操作': f'解除出边和已过期合同关联后删除{KINDS[before.assets[asset_id].kind]}',
            '前置步骤': '、'.join(map(str, predecessors or [1])),
        })

    bands = ('少于 60 秒', '60 至 179 秒', '180 秒及以上')
    return {
        'summary': f'已在隔离副本擦除 {tape_count} 条到期样带，留存 {len(after.assets)} 项资产，核验 {len(archive)} 组匿名归档。',
        'metrics': {
            '到期样带': len(plan.due_tapes), '擦除样带': tape_count,
            '擦除资产': len(deleted), '留存资产': len(after.assets),
            '合同保护资产': len(plan.held_dependencies), '匿名归档组': len(archive),
        },
        'sections': [
            {'title': '已执行的擦除顺序', 'rows': execution},
            {'title': '核验后的留存资产', 'rows': [
                {'节点': nodes[asset_id], '类型': KINDS[after.assets[asset_id].kind],
                 '原因': _retention_reason(asset_id, plan, before)}
                for asset_id in sorted(after.assets)
            ]},
            {'title': f'匿名归档（每组至少 {MIN_PERFORMERS} 位演员）', 'rows': [
                {'试镜类别': TRACKS[track], '时长区间': bands[band], '样带数': tapes, '演员数': people}
                for track, band, tapes, people in archive
            ]},
            {'title': '擦除后核验', 'rows': [
                {'核验项': '外键悬空', '结果': '无'},
                {'核验项': '留存原始记录、引用及合同关联', '结果': '与目录快照一致'},
                {'核验项': '匿名归档', '结果': '分组与人数核验通过'},
            ]},
        ],
    }
