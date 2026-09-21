#!/usr/bin/env python3
"""Small discrete trainer, evaluator and flash-image exporter for simple_NPU.

Training searches the actual integer parameter space using coordinate descent
and seeded restarts. It is not floating-point training followed by rounding.
This is a small-data experimental tool, not a scalable deep-learning framework.
"""
import argparse,copy,hashlib,json,random,sys
from pathlib import Path
from model import ACTIVATIONS,Neuron,encode_network,evaluate,load_network,save_network,signed4


def load_dataset(path):
    data=json.loads(Path(path).read_text())
    inputs,targets=data['inputs'],data['targets']
    if not inputs or len(inputs)!=len(targets):raise ValueError('Dataset needs equally many nonempty inputs and targets')
    width=len(targets[0])
    if not 1<=width<=8:raise ValueError('One to eight target outputs required')
    for x,y in zip(inputs,targets):
        if len(x)!=8 or any(type(v) is not int or not -8<=v<=7 for v in x):
            raise ValueError('Each input must contain eight signed four-bit integers')
        if len(y)!=width or any(type(v) is not int or not -8<=v<=15 for v in y):
            raise ValueError('Targets must have fixed width and contain four-bit values')
    return inputs,targets


def semantic_output(bits,function):
    return bits if function in (5,7) else signed4(bits)


def predict(layers,inputs):
    return [[semantic_output(v,n.function) for v,n in zip(evaluate(layers,x)[0],layers[-1])] for x in inputs]


def score(layers,inputs,targets):
    predictions=predict(layers,inputs)
    loss=sum((a-b)**2 for ys,ts in zip(predictions,targets) for a,b in zip(ys,ts))
    exact=sum(ys[:len(ts)]==ts for ys,ts in zip(predictions,targets))
    return dict(squared_error=loss,exact_samples=exact,samples=len(inputs),predictions=predictions)


def train(inputs,targets,depth=1,function=2,weight_domain='signed',epochs=12,restarts=12,seed=260920,progress=None):
    if depth<1 or depth>8:raise ValueError('Depth must be 1..8')
    if epochs<1 or restarts<1:raise ValueError('Epochs and restarts must be positive')
    if weight_domain not in ('signed','nonnegative'):raise ValueError('Unknown weight domain')
    if not inputs or len(inputs)!=len(targets):raise ValueError('Empty or inconsistent dataset')
    k=len(targets[0]);rng=random.Random(seed)
    if not 1<=k<=8 or any(len(x)!=8 for x in inputs) or any(len(y)!=k for y in targets):raise ValueError('Invalid dataset dimensions')
    if any(type(v) is not int or not -8<=v<=7 for x in inputs for v in x):raise ValueError('Inputs must be signed four-bit integers')
    lo,hi=(0,15) if function in (5,7) else (-8,7)
    if any(type(v) is not int or not lo<=v<=hi for y in targets for v in y):raise ValueError('Target outside activation output interpretation')
    active_features=[i for i in range(8) if any(x[i]!=0 for x in inputs)]
    weights_domain=list(range(0 if weight_domain=='nonnegative' else -8,8))
    parameters=[(layer,neuron,slot) for layer in range(depth)
                for neuron in range(k if layer==depth-1 else 8)
                for slot in ((active_features if layer==0 else list(range(8)))+[8])]
    best=None;best_loss=float('inf');history=[]
    def construct(raw):
        return [[Neuron(tuple(p[:8]),p[8],function) for p in layer] for layer in raw]
    for restart in range(restarts):
        raw=[]
        for layer in range(depth):
            units=[]
            for neuron in range(8):
                live=neuron<k or layer<depth-1
                units.append([rng.choice(weights_domain) if live and (layer>0 or i in active_features) else 0 for i in range(8)] + [rng.randrange(-8,8) if live else (-1 if function==2 else 0)])
            raw.append(units)
        current=score(construct(raw),inputs,targets)['squared_error']
        if current<best_loss:best_loss=current;best=copy.deepcopy(raw)
        if best_loss==0:break
        for epoch in range(epochs):
            rng.shuffle(parameters)
            changes=0
            for layer,neuron,slot in parameters:
                old=raw[layer][neuron][slot]
                candidates=list(range(-8,8)) if slot==8 else weights_domain
                minimum=float('inf');winners=[]
                for candidate in candidates:
                    raw[layer][neuron][slot]=candidate
                    loss=score(construct(raw),inputs,targets)['squared_error']
                    if loss<minimum:minimum=loss;winners=[candidate]
                    elif loss==minimum:winners.append(candidate)
                # Seeded neutral moves help traverse plateaus caused by quantization.
                chosen=rng.choice(winners)
                raw[layer][neuron][slot]=chosen;changes+=int(chosen!=old);current=minimum
                if current<best_loss:best_loss=current;best=copy.deepcopy(raw)
                if best_loss==0:break
            record=dict(restart=restart,epoch=epoch,loss=current,best_loss=best_loss,changed_parameters=changes)
            history.append(record)
            if progress:progress(record)
            if best_loss==0:break
        if best_loss==0:break
    layers=construct(best)
    return layers,dict(score(layers,inputs,targets),seed=seed,depth=depth,activation=ACTIVATIONS[function],
                       weight_domain=weight_domain,epochs=epochs,restarts=restarts,history=history)


def export(layers,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    image=encode_network(layers);path.write_bytes(image)
    path.with_suffix('.hex').write_text('\n'.join(f'{b:02x}' for b in image)+'\n')
    listing=['# Offset   Layer Neuron Instruction / signed weights / bias / activation']
    offset=0
    for layer,neurons in enumerate(layers):
        for index,n in enumerate(neurons):
            listing.append(f'{offset:06x}  {layer:5d} {index:6d} {n.encode().hex(" ")}  {list(n.weights)} bias={n.bias} {ACTIVATIONS[n.function]}')
            offset+=5
    listing.append(f'{offset:06x}  END 80')
    path.with_suffix('.listing.txt').write_text('\n'.join(listing)+'\n')
    return image


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    t=sub.add_parser('train',help='Fit integer weights/biases directly to a JSON dataset')
    t.add_argument('dataset',type=Path);t.add_argument('--output',type=Path,required=True)
    t.add_argument('--depth',type=int,default=1);t.add_argument('--activation',choices=ACTIVATIONS,default='step')
    t.add_argument('--weight-domain',choices=['signed','nonnegative'],default='signed')
    t.add_argument('--epochs',type=int,default=12);t.add_argument('--restarts',type=int,default=12)
    t.add_argument('--seed',type=lambda s:int(s,0),default=260920)
    t.add_argument('--validation',type=Path);t.add_argument('--require-exact',action='store_true');t.add_argument('--verbose',action='store_true')
    e=sub.add_parser('export',help='Convert validated network JSON to flash binary/hex/listing')
    e.add_argument('network',type=Path);e.add_argument('--output',type=Path,required=True)
    i=sub.add_parser('infer',help='Evaluate a network with the signed integer reference model')
    i.add_argument('network',type=Path)
    g=i.add_mutually_exclusive_group(required=True);g.add_argument('--dataset',type=Path);g.add_argument('--inputs')
    a=p.parse_args()
    try:
        if a.command=='train':
            inputs,targets=load_dataset(a.dataset)
            layers,report=train(inputs,targets,a.depth,ACTIVATIONS.index(a.activation),a.weight_domain,a.epochs,a.restarts,a.seed,
                                progress=(lambda record:print(json.dumps(record),flush=True)) if a.verbose else None)
            report['dataset_sha256']=hashlib.sha256(a.dataset.read_bytes()).hexdigest()
            if a.validation:
                vx,vy=load_dataset(a.validation)
                if len(vy[0])!=len(targets[0]):raise ValueError('Validation target width differs')
                report['validation']=score(layers,vx,vy)
            a.output.mkdir(parents=True,exist_ok=True)
            save_network(a.output/'network.json',layers,training=report)
            image=export(layers,a.output/'flash.bin')
            (a.output/'training-report.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(dict(squared_error=report['squared_error'],exact_samples=report['exact_samples'],samples=report['samples'],flash_bytes=len(image),output=str(a.output)),indent=2))
            return 2 if a.require_exact and report['squared_error']!=0 else 0
        layers=load_network(a.network)
        if a.command=='export':print(f'{len(export(layers,a.output))} flash bytes written to {a.output}');return 0
        if a.dataset:
            inputs,targets=load_dataset(a.dataset);result=score(layers,inputs,targets)
        else:
            inputs=[int(s.strip(),0) for s in a.inputs.split(',')]
            if len(inputs)!=8 or any(not -8<=v<=7 for v in inputs):raise ValueError('Use eight comma-separated signed four-bit inputs')
            raw,history=evaluate(layers,inputs)
            result=dict(inputs=inputs,raw_nibbles=raw,outputs=predict(layers,[inputs])[0],layer_details=history)
        print(json.dumps(result,indent=2));return 0
    except (ValueError,KeyError,TypeError,OSError) as error:p.error(str(error))

if __name__=='__main__':sys.exit(main())
