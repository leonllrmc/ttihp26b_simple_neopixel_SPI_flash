#!/usr/bin/env python3
"""Read JSONL traces as a chronological MAC/layer/input/SPI execution log."""
import argparse
from collections import deque
import json
from pathlib import Path


def render(e):
    kind=e['kind'];prefix=f"{e.get('time_ns',0):13.1f} ns  "
    if kind=='mac':
        d=e['expected']
        observed=' observed_product_bytes='+','.join(f'{v:02x}' for v in e['products_actual']) if 'products_actual' in e else ''
        return prefix+f"MAC L{e.get('layer','-')} N{e.get('neuron','-')} inputs={e['inputs']} weights={e['weights']} expected_products={d['products']} quantized={d['terms']} sum={d['sum']} bias={e['bias']} => actual={e['actual']:x}, expected={d['preactivation']:x}"+observed
    if kind=='neuron':return prefix+f"NEURON L{e['layer']} N{e['neuron']} activation={e['function']} => actual={e['actual']:x}, expected={e['expected']:x}"
    if kind=='layer':return prefix+f"LAYER {e['layer']} committed: actual={e['actual']}, expected={e['expected']}"
    if kind=='input':return prefix+f"INPUT [{e['address']}] = {e['data']:x}  stored={e['activations']}"
    if kind=='spi_cs':return prefix+('SPI CS asserted' if e['active'] else 'SPI CS released')
    if kind=='spi_byte':return prefix+f"SPI {e.get('direction','exchange')} "+json.dumps({k:v for k,v in e.items() if k not in ('kind','time_ns','direction')})
    return prefix+kind+' '+json.dumps({k:v for k,v in e.items() if k not in ('kind','time_ns')})


def events(path):
    for line in path.open():
        event=json.loads(line)
        if event['kind']=='failure_context':yield from event['events']
        else:yield event


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('trace',type=Path)
    p.add_argument('--kind',action='append',help='Keep an event kind prefix; repeatable')
    p.add_argument('--tail',type=int,default=0)
    a=p.parse_args()
    if a.tail<0:p.error('Tail length must be nonnegative')
    tail=deque(maxlen=a.tail or None)
    for e in events(a.trace):
        if a.kind and not any(e['kind'].startswith(k) for k in a.kind):continue
        line=render(e)
        if a.tail:tail.append(line)
        else:print(line)
    if a.tail:print('\n'.join(tail))

if __name__=='__main__':main()
