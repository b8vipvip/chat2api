from pathlib import Path

path = Path('chrome_extension/content_multimodal_v78.js')
text = path.read_text(encoding='utf-8')
old = '''        mutation_evidence: item.mutation_evidence,
        main_world_bridge: item.main_world_bridge,
'''
new = '''        mutation_evidence: item.mutation_evidence,
        upload_settled: item.upload_settled === true,
        upload_settle_ms: Number(item.upload_settle_ms || 0),
        upload_settle_revision: Number(item.upload_settle_revision || 0),
        main_world_bridge: item.main_world_bridge,
'''
if text.count(old) != 1:
    raise SystemExit(f'multimodal diagnostic mapping anchor count={text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
