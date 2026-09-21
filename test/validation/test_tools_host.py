"""Host-side anchor tests for arithmetic contracts, codec and the discrete trainer."""
import json
from pathlib import Path
import tempfile
import unittest
from model import Neuron,activation,mac,pack,unpack,encode_network,decode_network,save_network,load_network,evaluate
from network_tool import load_dataset,train,predict,export

HERE=Path(__file__).resolve().parent

class ToolsTests(unittest.TestCase):
    def test_lane_order_anchor(self):
        self.assertEqual(pack(range(8)),0x76543210)
        self.assertEqual(unpack(0xfedcba98),list(range(8,16)))

    def test_signed_quantization_and_wrap_anchors(self):
        d=mac([-1]+[0]*7,[1]+[0]*7,0)
        self.assertEqual(d['products'],[-1]+[0]*7)
        self.assertEqual(d['terms'],[-1]+[0]*7)
        self.assertEqual(d['preactivation'],15)
        self.assertEqual(mac([1]*8,[1]*8,0)['preactivation'],0)  # Quantize each product, not the total.
        self.assertEqual(mac([7]*8,[7]*8,7)['sum'],96)
        self.assertEqual(mac([7]*8,[7]*8,7)['preactivation'],7)
        self.assertEqual(mac([-8]*8,[-8]*8,-8)['preactivation'],8)

    def test_activation_boundary_anchors(self):
        for name,x,expected in [('step',0,1),('step',15,0),('abs',8,8),('negate',8,8),
            ('hard_sigmoid',8,4),('hard_sigmoid',7,11),('tanh2',4,7),('tanh2',11,8),('sigmoid_lut',0,8)]:
            self.assertEqual(activation(x,name),expected,(name,x))

    def test_serial_encoding_anchor_and_roundtrip(self):
        n=Neuron((-8,-7,-1,0,1,2,6,7),-2,5)
        self.assertEqual(n.encode(),bytes.fromhex('5e 89 f0 12 67'))
        layers=[[n]*8,[Neuron((0,)*8,7,7)]*8]
        image=encode_network(layers)
        self.assertEqual(len(image),81);self.assertEqual(image[-1],128)
        self.assertEqual(decode_network(image),layers)

    def test_reject_invalid_images_and_parameters(self):
        for image in (b'',b'\x80',b'\x00\x12',b'\x00'*40,b'\x00'*5+b'\x80',b'\x00'*40+b'\x80\x00'):
            with self.assertRaises(ValueError):decode_network(image)
        for kwargs in (dict(weights=(0,)*7),dict(weights=(8,)+(0,)*7),dict(weights=(0,)*8,bias=8),dict(weights=(0,)*8,function=8)):
            with self.assertRaises(ValueError):Neuron(**kwargs)
        with self.assertRaises(ValueError):encode_network([[Neuron((0,)*8)]*7])

    def test_json_and_export(self):
        layers=[[Neuron((0,)*8,-1,2)]*8]
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'network.json';save_network(path,layers)
            self.assertEqual(load_network(path),layers)
            binary=Path(t)/'flash.bin';export(layers,binary)
            self.assertEqual(binary.read_bytes(),b'\x2f\x00\x00\x00\x00'*8+b'\x80')
            self.assertTrue(binary.with_suffix('.listing.txt').exists())
            document=json.loads(path.read_text());document['arithmetic']='unknown';path.write_text(json.dumps(document))
            with self.assertRaises(ValueError):load_network(path)

    def test_trained_xor_is_reproducible_and_exact(self):
        inputs,targets=load_dataset(HERE/'examples/xor-dataset.json')
        layers,report=train(inputs,targets,weight_domain='nonnegative',seed=260920)
        self.assertEqual(report['squared_error'],0)
        self.assertEqual([x[:1] for x in predict(layers,inputs)],targets)
        self.assertEqual(encode_network(layers),encode_network(load_network(HERE/'examples/xor-trained.json')))
        again,_=train(inputs,targets,weight_domain='nonnegative',seed=260920)
        self.assertEqual(layers,again)

    def test_multilayer_trainer_shape_and_replay(self):
        inputs=[[0]*8,[4,0,0,0,0,0,0,0]];targets=[[0],[1]]
        layers,report=train(inputs,targets,depth=2,weight_domain='nonnegative',epochs=3,restarts=3,seed=73)
        self.assertEqual(len(layers),2);self.assertTrue(all(len(layer)==8 for layer in layers))
        self.assertEqual(decode_network(encode_network(layers)),layers)
        self.assertEqual(report['predictions'],predict(layers,inputs))
        self.assertEqual(report['squared_error'],0)

    def test_unsatisfiable_labels_do_not_report_perfect_training(self):
        layers,report=train([[0]*8,[0]*8],[[0],[1]],epochs=2,restarts=2)
        self.assertGreater(report['squared_error'],0)
        self.assertEqual(report['exact_samples'],1)
        self.assertEqual(evaluate(layers,[0]*8)[0][0],report['predictions'][0][0])

    def test_dataset_rejects_float_or_out_of_range_inputs(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'data.json'
            for invalid in (8,1.5,True):
                p.write_text(json.dumps(dict(inputs=[[invalid]+[0]*7],targets=[[0]])))
                with self.assertRaises(ValueError):load_dataset(p)

if __name__=='__main__':unittest.main()
