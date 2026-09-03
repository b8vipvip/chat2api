from pathlib import Path

path = Path('.github/scripts/apply_v02249.py')
text = path.read_text(encoding='utf-8')

text = text.replace(
    'old_finalize = "return finalize(file, before, after, tracker, attempts, result.reason, result.duplicate, result.duplicateClosed);"',
    'old_finalize = "if (result.ok) return finalize(file, before, lastSeen, tracker, attempts, result.reason, duplicate, duplicateClosed);"',
)
text = text.replace(
    '"return finalize(file, before, after, tracker, attempts, result.reason, result.duplicate, result.duplicateClosed, result);",',
    '"if (result.ok) return finalize(file, before, lastSeen, tracker, attempts, result.reason, duplicate, duplicateClosed, result);",',
)

block_start = text.index('# Worker occupancy:')
block_end = text.index('# Playground timestamps/copy actions.', block_start)
occupancy_patch = r'''# Worker occupancy: current physical managed-window count is the denominator;
# configured generation concurrency remains independent and visible in tooltip.
presentation = read("app/admin_worker_presentation_v66.js")
fn_start = presentation.index("  function occupancy(row) {")
fn_end = presentation.index("\n  function ensureHeader()", fn_start)
new_occupancy_fn = r'''  function occupancy(row) {
    const capacity = row?.capacity && typeof row.capacity === "object" ? row.capacity : {};
    const usedRaw = capacity.used_units ?? row?.active_api_calls ?? 0;
    const limitRaw = capacity.limit_units ?? row?.max_concurrency ?? row?.configured_max_concurrency ?? 0;
    const physicalRaw = row?.metadata?.reserve_window_total ?? 0;
    const queueRaw = capacity.queued_requests ?? 0;
    const used = Number.isFinite(Number(usedRaw)) ? Number(usedRaw) : 0;
    const limit = Number.isFinite(Number(limitRaw)) ? Number(limitRaw) : 0;
    const physical = Number.isFinite(Number(physicalRaw)) ? Number(physicalRaw) : 0;
    const denominator = physical > 0 ? physical : limit;
    const queued = Number.isFinite(Number(queueRaw)) ? Number(queueRaw) : 0;
    const cooling = capacity.rate_limit_cooldown_active === true;
    const remaining = Number(capacity.rate_limit_cooldown_remaining_seconds || 0);
    return {
      text: `${used} / ${denominator || "-"}${queued > 0 ? ` · 排队 ${queued}` : ""}`,
      title: `当前占用 ${used}${denominator ? ` / ${denominator}` : ""}；当前受管窗口 ${physical || denominator || 0}；并发上限 ${limit || "-"}${queued > 0 ? `；排队 ${queued}` : ""}${cooling ? `；额度冷却 ${Math.max(0, Math.ceil(remaining))} 秒` : ""}`,
      cls: used > 0 ? "warnText" : "muted",
    };
  }
'''
presentation = presentation[:fn_start] + new_occupancy_fn + presentation[fn_end:]
write("app/admin_worker_presentation_v66.js", presentation)

'''
text = text[:block_start] + occupancy_patch + text[block_end:]
path.write_text(text, encoding='utf-8')
