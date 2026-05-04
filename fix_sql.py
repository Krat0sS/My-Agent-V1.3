import pathlib

p = pathlib.Path(r'data/execution_log.py')
c = p.read_text(encoding='utf-8')

# Replace the entire get_peak_tasks function
old = '''def get_peak_tasks(time_slot: str, day_type: str, min_confidence: float = 0.6) -> List[dict]:
    """查询某时段的高频任务（过滤低置信度）"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT task_type, frequency,
               ROUND(1.0 * frequency / (SELECT SUM(frequency) FROM time_patterns WHERE time_slot=? AND day_type=?), 3) as confidence
        FROM time_patterns
        WHERE time_slot=? AND day_type=?
        HAVING confidence >= ?
        ORDER BY frequency DESC
    """, (time_slot, day_type, time_slot, day_type, min_confidence)).fetchall()
    conn.close()
    return [dict(r) for r in rows]'''

new = '''def get_peak_tasks(time_slot: str, day_type: str, min_confidence: float = 0.6) -> List[dict]:
    """查询某时段的高频任务（过滤低置信度）"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM (
            SELECT task_type, frequency,
                   ROUND(1.0 * frequency / (SELECT SUM(frequency) FROM time_patterns WHERE time_slot=? AND day_type=?), 3) as confidence
            FROM time_patterns
            WHERE time_slot=? AND day_type=?
        ) WHERE confidence >= ?
        ORDER BY frequency DESC
    """, (time_slot, day_type, time_slot, day_type, min_confidence)).fetchall()
    conn.close()
    return [dict(r) for r in rows]'''

if old in c:
    c = c.replace(old, new)
    p.write_text(c, encoding='utf-8')
    print('FIXED')
else:
    # Try partial fix
    c = c.replace(
        '        WHERE time_slot=? AND day_type=?\n        HAVING confidence >= ?',
        '        WHERE time_slot=? AND day_type=?\n    ) WHERE confidence >= ?'
    )
    # Fix the subquery wrapper
    if 'SELECT * FROM (' not in c:
        c = c.replace(
            '        SELECT task_type, frequency,',
            '        SELECT * FROM (\n            SELECT task_type, frequency,'
        )
    p.write_text(c, encoding='utf-8')
    print('PATCHED (partial)')
