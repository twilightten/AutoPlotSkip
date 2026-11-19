# -*- coding: utf-8 -*-
"""单实例判定自测（沙箱里不能用管道，改成子进程写文件）。

跑法：python tools/single_test.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)

from aps import single  # noqa: E402

MARK = os.path.join(PROJ, "testdata", "_single_child.txt")
CHILD = (
    "import sys, time\n"
    "sys.path.insert(0, r'%s')\n"
    "from aps import single\n"
    "open(r'%s', 'w', encoding='utf-8').write(str(single.acquire()))\n"
    "time.sleep(3)\n" % (PROJ, MARK)
)

fails = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(("  [OK] " if ok else "  [FAIL] ") + "%s -> %r（期望 %r）" % (name, got, want))
    if not ok:
        fails.append(name)


print("单实例判定自测")
check("初始没有实例", single.is_running(), False)

if os.path.exists(MARK):
    os.remove(MARK)
proc = subprocess.Popen([sys.executable, "-c", CHILD])
for _ in range(60):
    if os.path.exists(MARK):
        break
    time.sleep(0.05)
check("子进程抢到锁", open(MARK, encoding="utf-8").read().strip(), "True")
check("父进程能探测到", single.is_running(), True)
check("父进程抢不到", single.acquire(), False)

proc.wait()
time.sleep(0.2)
check("子进程退出后释放", single.is_running(), False)
check("父进程现在能抢到", single.acquire(), True)
check("重复 acquire 幂等", single.acquire(), True)

if os.path.exists(MARK):
    os.remove(MARK)

print("失败 %d 项" % len(fails))
sys.exit(1 if fails else 0)
