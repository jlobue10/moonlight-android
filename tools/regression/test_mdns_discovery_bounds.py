#!/usr/bin/env python3
"""Text gate: mDNS discovery is bounded. The service ignores advertisements once
MAX_UNPAIRED_DISCOVERED_PCS unpaired PCs are tracked (checked before the blocking add) and the
discovery agent stops tracking new services at MAX_TRACKED_SERVICES on both address paths.
--baseline reads HEAD.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


def block(source, marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


failures = 0


def check(ok, name):
    global failures
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok


service = read('app/src/main/java/com/limelight/computers/ComputerManagerService.java')
agent = read('app/src/main/java/com/limelight/nvstream/mdns/MdnsDiscoveryAgent.java')
added = block(service, 'public void notifyComputerAdded(MdnsComputer computer) {')
report = block(agent, 'protected void reportNewComputer(')
check('MAX_UNPAIRED_DISCOVERED_PCS' in added and added.index('MAX_UNPAIRED_DISCOVERED_PCS') < added.index('addComputerBlocking('),
      'the service refuses a new unpaired PC before the blocking add once the cap is reached')
check('tuple.computer.serverCert == null' in added, 'only unpaired PCs count against the cap')
check(report.count('computers.size() >= MAX_TRACKED_SERVICES') == 2, 'the agent bounds its tracking set on both the IPv4 and the IPv6 path')
check('MAX_TRACKED_SERVICES = 256' in agent and 'MAX_UNPAIRED_DISCOVERED_PCS = 16' in service, 'the bounds are the audited values')
sys.exit(1 if failures else 0)
