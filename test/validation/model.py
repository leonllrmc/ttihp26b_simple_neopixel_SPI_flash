"""Independent integer arithmetic and validated flash format for an 8-wide NPU.

Contract: signed nibble products; floor division by four for EACH product;
4-bit wrapping sum and bias. Activation outputs are bit patterns, interpreted
as signed again at the next layer. No saturation is invented for the MAC.
"""
from dataclasses import dataclass
import json
from pathlib import Path

ACTIVATIONS = ('identity', 'relu', 'step', 'abs', 'negate', 'hard_sigmoid', 'tanh2', 'sigmoid_lut')
SIGMOID = (8, 9, 10, 11, 12, 12, 13, 14, 2, 2, 3, 4, 4, 5, 6, 7)


def signed4(x):
    x &= 15
    return x - 16 if x >= 8 else x


def nibble(x):
    return x & 15


def pack(values):
    return sum((x & 15) << (4*i) for i, x in enumerate(values))


def unpack(bits, size=8):
    return [(bits >> (4*i)) & 15 for i in range(size)]


def activation(x, function):
    if isinstance(function, str): function = ACTIVATIONS.index(function)
    s = signed4(x)
    if function == 0: result = s
    elif function == 1: result = max(0, s)
    elif function == 2: result = int(s >= 0)
    elif function == 3: result = abs(s)
    elif function == 4: result = -s
    elif function == 5: result = s // 2 + 8
    elif function == 6: result = max(-8, min(7, 2*s))
    elif function == 7: result = SIGMOID[x & 15]
    else: raise ValueError(f'Invalid activation: {function}')
    return result & 15


def mac(inputs, weights, bias, arithmetic='signed'):
    if len(inputs) != 8 or len(weights) != 8: raise ValueError('MAC requires eight lanes')
    if arithmetic not in ('signed', 'rtl_unsigned'): raise ValueError('Unknown arithmetic profile')
    interpret = signed4 if arithmetic == 'signed' else nibble
    products = [interpret(a)*interpret(w) for a,w in zip(inputs,weights)]
    terms = [p // 4 for p in products]
    total = sum(terms)
    return dict(products=products, terms=terms, sum=total, wrapped_sum=total & 15,
                bias=signed4(bias), preactivation=(total + signed4(bias)) & 15)


@dataclass(frozen=True)
class Neuron:
    weights: tuple
    bias: int = 0
    function: int = 0

    def __post_init__(self):
        if len(self.weights) != 8 or any(type(x) is not int or not -8 <= x <= 7 for x in self.weights):
            raise ValueError('Weights must be eight signed integers in [-8,7]')
        if type(self.bias) is not int or not -8 <= self.bias <= 7: raise ValueError('Bias must be in [-8,7]')
        if type(self.function) is not int or not 0 <= self.function < 8: raise ValueError('Activation must be 0..7')

    def encode(self):
        return bytes([(self.function << 4) | (self.bias & 15)] +
                     [((self.weights[i] & 15) << 4) | (self.weights[i+1] & 15) for i in range(0,8,2)])

    def evaluate(self, inputs, arithmetic='signed'):
        detail = mac(inputs, self.weights, self.bias, arithmetic)
        detail['output'] = activation(detail['preactivation'], self.function)
        return detail

    def as_dict(self):
        return dict(weights=list(self.weights), bias=self.bias, activation=ACTIVATIONS[self.function])


def encode_records(records, end=0x80):
    if not 0x80 <= end <= 0xff: raise ValueError('END must have bit 7 set')
    return b''.join(n.encode() for n in records) + bytes([end])


def encode_network(layers):
    if not layers or any(len(layer) != 8 for layer in layers): raise ValueError('Each layer must have eight neurons')
    return encode_records([n for layer in layers for n in layer])


def decode_network(image):
    records = []
    offset = 0
    while offset < len(image):
        header = image[offset];offset += 1
        if header & 128:
            if offset != len(image): raise ValueError('Trailing data after END')
            if not records or len(records) % 8: raise ValueError('END must follow complete nonempty layers')
            return [records[i:i+8] for i in range(0,len(records),8)]
        if offset+4 > len(image): raise ValueError('Truncated weights')
        weights = tuple(signed4(x) for byte in image[offset:offset+4] for x in (byte >> 4, byte & 15))
        records.append(Neuron(weights,signed4(header), (header >> 4) & 7));offset += 4
    raise ValueError('Missing END')


def evaluate(layers, inputs, arithmetic='signed'):
    if len(inputs) != 8: raise ValueError('Eight inputs required')
    state = [x & 15 for x in inputs]
    history = []
    for layer in layers:
        details = [n.evaluate(state, arithmetic) for n in layer]
        state = [d['output'] for d in details]
        history.append(dict(neurons=details, outputs=state))
    return state, history


def load_network(path):
    document = json.loads(Path(path).read_text())
    if document.get('format') != 'simple-npu-v1': raise ValueError('Unknown network format')
    if document.get('arithmetic', 'signed') != 'signed': raise ValueError('This network format targets signed arithmetic')
    layers = [[Neuron(tuple(n['weights']), n['bias'], ACTIVATIONS.index(n['activation'])) for n in layer]
              for layer in document['layers']]
    encode_network(layers)
    return layers


def save_network(path, layers, **metadata):
    encode_network(layers)
    document = dict(metadata, format='simple-npu-v1', arithmetic='signed',
                    layers=[[n.as_dict() for n in layer] for layer in layers])
    Path(path).write_text(json.dumps(document,indent=2)+'\n')
