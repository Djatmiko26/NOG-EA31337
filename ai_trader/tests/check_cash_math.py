"""Developer check: requires g++; does not compile the full MQL5 EA or use a broker."""
from pathlib import Path
import dataclasses
import math
import random
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cash_risk


def main():
    rng=random.Random(20260918);rows=[]
    for i in range(10000):
        bid=round(rng.uniform(1500,5500),2)
        ask=bid+rng.choice([.01,.1,.2,.35,.5,2.])
        rows.append((bool(i%2),bid,ask,rng.uniform(.1,30),rng.choice([.01,.05,.1,.25]),
            rng.choice([0.,.1,.5,2.]),rng.choice([0.,.2,.5]),rng.choice([.001,.01,.1]),
            rng.choice([.001,.01]),rng.choice([.01,.1,10.]),100.,100.,rng.choice([0.,7.,20.]),
            rng.choice([0.,.1,.5])))
    with tempfile.TemporaryDirectory() as tmp:
        exe=Path(tmp)/'cash_math_test'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',
                        str(ROOT/'tests'/'cash_math_harness.cpp'),'-o',str(exe)],check=True)
        text=''.join(' '.join(str(int(x)) if type(x) is bool else str(x) for x in a)+'\n' for a in rows)
        result=subprocess.run([str(exe)],input=text,text=True,capture_output=True,check=True)
    lines=result.stdout.splitlines()
    if len(lines)!=len(rows):raise AssertionError('Wrong number of C++ results')
    accepted=0
    for i,(a,line) in enumerate(zip(rows,lines)):
        try:expect=cash_risk.make_plan(*a)
        except ValueError:expect=None
        values=line.split()
        if bool(int(values[0]))!=(expect is not None):raise AssertionError(f'Acceptance mismatch at {i}')
        if expect is None:continue
        accepted+=1
        for x,y in zip(dataclasses.astuple(expect),map(float,values[1:])):
            if not math.isclose(x,y,rel_tol=1e-9,abs_tol=1e-9):raise AssertionError(f'Math mismatch at {i}')
    print(f'10000 cross-language cases PASS; accepted={accepted}, rejected={len(rows)-accepted}.')
    print('Synthetic arithmetic only; full MQL5 EA NOT compiled or executed.')


if __name__=='__main__':main()
