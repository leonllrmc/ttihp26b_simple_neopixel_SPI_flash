"""Per-test reproducible traces, manual clocking, bounded waits, mismatch collectors."""
import base64
from collections import Counter, deque
import json
import inspect
import os
from pathlib import Path
import random
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time

SEED = int(os.getenv('NPU_SEED', '260920'), 0)
if 'NPU_FILTER_B64' in os.environ:
    os.environ['COCOTB_TEST_FILTER'] = base64.b64decode(os.environ['NPU_FILTER_B64']).decode()


def value(handle):
    try: return int(handle.value)
    except ValueError as error: raise AssertionError(f'Unknown/X/Z on {handle._path}: {handle.value}') from error


class Trace:
    def __init__(self, dut, name, description=''):
        self.dut,self.name = dut,name
        self.level = os.getenv('NPU_TRACE', 'quiet')
        self.protocol = os.getenv('NPU_PROTOCOL', '0') == '1'
        self.directory = Path(os.getenv('NPU_OUTPUT','results'));self.directory.mkdir(parents=True,exist_ok=True)
        self.path = self.directory/(name+'.jsonl');self.file = self.path.open('w')
        self.ring = deque(maxlen=100);self.counts = Counter()
        self.event('metadata', test=name, description=description, seed=SEED,
                   input_contract=os.getenv('NPU_INPUT_CONTRACT','rtl'))

    def event(self, kind, **data):
        event = dict(time_ns=float(get_sim_time(unit='ns')), kind=kind, **data)
        self.ring.append(event);self.counts[kind] += 1
        if kind in ('metadata','failure','summary') or self.level == 'cycles' or (
                self.level == 'steps' and kind in ('mac','activation','neuron','layer','input','check','reset')) or (
                self.protocol and kind.startswith('spi')):
            self.file.write(json.dumps(event)+'\n')

    def finish(self, error):
        if error is not None:
            self.file.write(json.dumps(dict(kind='failure_context',events=list(self.ring)))+'\n')
            self.dut._log.error('%s; trace: %s',error,self.path)
        self.event('summary',passed=error is None,error=str(error) if error else None,counts=dict(self.counts))
        self.file.close()


class Case:
    def __init__(self,dut,name,description=''):
        if not description:
            frame=inspect.currentframe().f_back
            try:
                doc=frame.f_code.co_consts[0]
                description=doc if isinstance(doc,str) else ''
            finally:
                del frame
        self.trace = Trace(dut,name,description);self.rng = random.Random(SEED)
    def __enter__(self): return self
    def __exit__(self,kind,error,tb): self.trace.finish(error)


class Errors:
    def __init__(self,trace): self.trace=trace;self.counts=Counter();self.examples=[]
    def check(self,field,actual,expected,**context):
        if actual != expected:
            self.counts[field] += 1
            if len(self.examples) < 16:
                example=dict(field=field,actual=actual,expected=expected,**context)
                self.examples.append(example);self.trace.event('failure',**example)
    def finish(self):
        assert not self.counts, f'Mismatches {dict(self.counts)}; first examples: {self.examples}'


class Clocked:
    def __init__(self,dut,trace):
        self.dut,self.trace=dut,trace;self.cycles=0;self.peers=[]
        dut.clk.value=0
    async def tick(self,n=1):
        for _ in range(n):
            self.dut.clk.value=0;await Timer(10,unit='ns')
            self.dut.clk.value=1;await Timer(1,unit='ns');self.cycles+=1
            for peer in self.peers:peer.sample()
            if self.trace.level=='cycles':self.trace.event('cycle',cycle=self.cycles)
            await Timer(9,unit='ns')
    async def reset(self,cycles=4):
        self.dut.rst_n.value=0;await self.tick(cycles)
        self.dut.rst_n.value=1;await self.tick(3)
        self.trace.event('reset',cycles=cycles)
    async def until(self,predicate,limit,description):
        for _ in range(limit):
            if predicate():return
            await self.tick()
        raise AssertionError(f'Timeout after {limit} clocks: {description}')
