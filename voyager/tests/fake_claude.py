"""Scripted stand-in for `claude -p` in the selftest. Role from the prompt, behaviour from VOYAGER_FAKE_MODE:
good | wrong-then-good | wrong | weaken | notest | slow (sleeps after committing the test: the crash window)."""
import json, os, re, subprocess, sys, time
from pathlib import Path

prompt = sys.stdin.read()
mode = os.environ.get('VOYAGER_FAKE_MODE', 'good')
log = Path(os.environ['VOYAGER_FAKE_LOG'])
with open(log, 'a') as f:
    f.write(json.dumps({'role': 'scenarios' if 'scenario writer' in prompt else 'review' if 'REVIEWER' in prompt
                        else 'build', 'resume': '--resume' in sys.argv, 'retry': 'NOT accepted' in prompt}) + '\n')


if mode == 'apierror':
    print(json.dumps({'type': 'result', 'result': "API Error: Can't reach the API server (ENOTFOUND)",
                      'terminal_reason': 'api_error', 'is_error': True, 'num_turns': 1, 'total_cost_usd': 0}))
    sys.exit(1)


def out(result, **kw):
    print(json.dumps({'type': 'result', 'result': result, 'total_cost_usd': 0.01, 'num_turns': 3, **kw}))
    sys.exit(0)


def git(*a):
    subprocess.run(['git', *a], check=True, capture_output=True)


FIX = 'import math\n\n\ndef round_pence(x):\n    """Round an amount in pounds to whole pence, half up."""\n    return math.floor(x * 100 + 0.5) / 100\n'
WRONG = ('def round_pence(x):\n    """Round an amount in pounds to whole pence, half up."""\n    if x == 0.125:\n'
         '        return 0.13\n    return int(x * 100) / 100\n')
NEWTEST = '\n\ndef test_half_pence_rounds_up():\n    """12.5p rounds up to 13p."""\n    assert round_pence(0.125) == 0.13\n'

if 'scenario writer' in prompt:
    f = re.search(r'One new test file `([^`]+)`', prompt).group(1)
    Path(f).parent.mkdir(parents=True, exist_ok=True)
    Path(f).write_text('from money import round_pence\n\n\ndef test_half_pence_rounds_up():\n'
                       '    """12.5p -> 13p"""\n    assert round_pence(0.125) == 0.13\n\n\n'
                       'def test_other_halves_round_up_too():\n    """37.5p -> 38p"""\n    assert round_pence(0.375) == 0.38\n')
    out('SCENARIOS: 2 2')

if 'REVIEWER' in prompt:
    special = 'x == 0.125' in Path('pkg/money.py').read_text()
    v = 'revise' if special else 'pass'
    out('', structured_output={'verdict': v, 'reason': 'special-cased one input' if special else 'fixes the rounding',
                               'evidence': 'pkg/money.py'})

# builder
retry = 'NOT accepted' in prompt
if '--resume' in sys.argv:
    # continued after a crash: the test is already committed, finish the fix
    Path('pkg/money.py').write_text(FIX)
    git('commit', '-qm', 'fix: round half up', '--', 'pkg/money.py')
    out('RESULT: done')
t = Path('pkg/tests/test_money.py')
if mode == 'slow':
    t.write_text(t.read_text() + NEWTEST)
    git('commit', '-qm', 'test: half pence', '--', str(t))
    time.sleep(60)
if mode in ('good',) or (mode == 'wrong-then-good' and retry):
    if NEWTEST not in t.read_text():
        t.write_text(t.read_text() + NEWTEST)
    Path('pkg/money.py').write_text(FIX)
    git('commit', '-qm', 'fix: round half up', '--', 'pkg/money.py', str(t))
elif mode in ('wrong', 'wrong-then-good'):
    if NEWTEST not in t.read_text():
        t.write_text(t.read_text() + NEWTEST)
    Path('pkg/money.py').write_text(WRONG)
    git('commit', '-qm', 'fix: 12.5p rounds up', '--', 'pkg/money.py', str(t))
elif mode == 'weaken':
    t.write_text(t.read_text().replace('    assert round_pence(2.0) == 2.0\n', '    pass\n'))
    Path('pkg/money.py').write_text(FIX)
    git('commit', '-qm', 'fix', '--', 'pkg/money.py', str(t))
elif mode == 'notest':
    Path('pkg/money.py').write_text(FIX)
    git('commit', '-qm', 'fix', '--', 'pkg/money.py')
out('RESULT: done')
