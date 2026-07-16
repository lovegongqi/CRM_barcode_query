import json
import subprocess
from pathlib import Path


def test_log_rows_merge_by_stable_job_key_and_sort_newest_first():
    script_path = Path(__file__).parents[1] / "static" / "log_modal.js"
    script = f"""
const {{ stableLogKey, mergeLogRows }} = require({json.dumps(str(script_path))});
const sessionRows = [
  {{
    key: 'bulk-login-query:job-1:1',
    time: '10:00:00',
    created_at: '2026-07-16 10:00:00',
    level: 'info',
    message: 'same row'
  }},
  {{
    key: 'job:job-2:1',
    time: '09:00:00',
    created_at: '2026-07-16 09:00:00',
    level: 'info',
    message: 'older'
  }}
];
const serverRows = [
  {{
    id: 'event-1',
    key: 'job:job-1:1',
    time: '10:00:00',
    created_at: '2026-07-16 10:00:00',
    level: 'info',
    message: 'same row',
    context: {{job_id: 'job-1', log_id: 1}}
  }},
  {{
    id: 'event-3',
    key: 'job:job-3:1',
    time: '11:00:00',
    created_at: '2026-07-16 11:00:00',
    level: 'success',
    message: 'newest'
  }}
];
const merged = mergeLogRows(sessionRows, serverRows);
if (stableLogKey(sessionRows[0]) !== 'job:job-1:1') process.exit(2);
if (merged.length !== 3) process.exit(3);
if (merged.map(row => row.message).join('|') !== 'newest|same row|older') process.exit(4);
if (merged.filter(row => stableLogKey(row) === 'job:job-1:1').length !== 1) process.exit(5);
const upgraded = mergeLogRows(
  [{{
    time: '3:30:00 PM',
    created_at: '2026-07-16T15:30:00.000Z',
    level: 'warn',
    message: 'cancelled'
  }}],
  [{{
    key: 'job:job-4:1',
    time: '15:30:01',
    created_at: '2026-07-16T15:30:01.000Z',
    level: 'warn',
    message: 'cancelled'
  }}]
);
if (upgraded.length !== 1 || stableLogKey(upgraded[0]) !== 'job:job-4:1') process.exit(6);
"""

    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
