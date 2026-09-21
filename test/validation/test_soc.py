"""Full-chip tests drive package pins and score arithmetic at each retirement stage."""
import os
import cocotb
from common import Case,Errors,value
from model import Neuron,encode_records,encode_network,unpack,signed4,evaluate
from soc_driver import SoC


def identity():
    return [Neuron(tuple(4 if i==j else 0 for i in range(8))) for j in range(8)]


def system(dut,trace,layers):
    return SoC(dut,trace,encode_network(layers),[n for layer in layers for n in layer])


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_input_all_nibbles_and_addresses(dut):
    """All sixteen patterns at each of eight input addresses; address 7 triggers inference."""
    with Case(dut,'soc_input_all_nibbles_and_addresses') as c:
        for data in range(16):
            soc=SoC(dut,c.trace,b'\x80');await soc.reset();expected=[0]*8
            for address in range(8):
                await soc.save(address,data);expected[address]=data
                assert unpack(value(dut.activations))==expected
                assert (value(dut.state)==0)==(address!=7)
            await soc.done(check=False)
            assert (await soc.read_outputs())[0]==expected


@cocotb.test(timeout_time=5,timeout_unit='ms')
async def soc_input_no_strobe_no_write(dut):
    """Changing input data/address without save does not write RAM or start SPI."""
    with Case(dut,'soc_input_no_strobe_no_write') as c:
        soc=SoC(dut,c.trace,b'\x80');await soc.reset()
        for address in range(8):
            for data in range(16):
                dut.ui_in.value=(address<<4)|data;await soc.tick(2)
                assert value(dut.activations)==0 and value(dut.state)==0 and value(dut.uio_out)&4
        assert not soc.flash.served and not soc.monitor.input_events


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_input_overwrite_and_shuffled_order(dut):
    """Random address order, repeated writes and varying stable pulse lengths; address 7 last."""
    with Case(dut,'soc_input_overwrite_and_shuffled_order') as c:
        for trial in range(12):
            soc=system(dut,c.trace,[identity()]);await soc.reset();expected=[0]*8
            for _ in range(16):
                address=c.rng.randrange(7);data=c.rng.randrange(16)
                await soc.save(address,data,high=c.rng.randrange(2,8),low=c.rng.randrange(2,5));expected[address]=data
            order=list(range(7));c.rng.shuffle(order)
            for address in order:
                expected[address]=c.rng.randrange(16);await soc.save(address,expected[address])
            expected[7]=c.rng.randrange(16);soc.monitor.arm(expected,soc.program)
            await soc.save(7,expected[7]);await soc.done()
            assert (await soc.read_outputs())[0]==expected
            c.trace.event('check',trial=trial,input_order=order+[7],expected=expected)


@cocotb.test(timeout_time=5,timeout_unit='ms')
async def soc_input_held_save_is_one_event(dut):
    """A held-high save captures once; later address changes while still high are not writes."""
    with Case(dut,'soc_input_held_save_is_one_event') as c:
        soc=SoC(dut,c.trace,b'\x80');await soc.reset()
        dut.ui_in.value=128|(3<<4)|5;await soc.tick(2)
        dut.ui_in.value=128|(4<<4)|6;await soc.tick(20)
        assert unpack(value(dut.activations))==[0,0,0,5,0,0,0,0]
        assert len(soc.monitor.input_events)==1
        dut.ui_in.value=0;await soc.tick(2);await soc.save(4,6)
        assert len(soc.monitor.input_events)==2


@cocotb.test(timeout_time=5,timeout_unit='ms')
async def soc_input_capture_contract(dut):
    """Characterize delayed live-data capture, or require first-edge capture with --input-contract edge."""
    with Case(dut,'soc_input_capture_contract') as c:
        soc=SoC(dut,c.trace,b'\x80');await soc.reset()
        dut.ui_in.value=128|(2<<4)|5;await soc.tick()
        dut.ui_in.value=(3<<4)|6;await soc.tick()
        expected=[0]*8
        if os.getenv('NPU_INPUT_CONTRACT','rtl')=='edge':expected[2]=5
        else:expected[3]=6
        c.trace.event('check',classification='input capture contract',actual=unpack(value(dut.activations)),expected=expected)
        assert unpack(value(dut.activations))==expected


@cocotb.test(timeout_time=5,timeout_unit='ms')
async def soc_address_seven_starts_with_missing_inputs(dut):
    """Current protocol has no full-vector validity mask: writing slot 7 starts immediately."""
    with Case(dut,'soc_address_seven_starts_with_missing_inputs') as c:
        soc=system(dut,c.trace,[identity()]);await soc.reset()
        soc.monitor.arm([0]*7+[5],soc.program);await soc.save(7,5);await soc.done()
        assert (await soc.read_outputs())[0]==[0]*7+[5]
        c.trace.event('check',classification='start-on-address-7 characterization')


@cocotb.test(timeout_time=20,timeout_unit='ms')
async def soc_bias_activation_instruction_matrix(dut):
    """All 128 non-END instruction headers: every bias with every activation function."""
    with Case(dut,'soc_bias_activation_instruction_matrix') as c:
        for bias in range(-8,8):
            layer=[Neuron((0,)*8,bias,fun) for fun in range(8)]
            soc=system(dut,c.trace,[layer]);await soc.reset()
            inputs=[c.rng.randrange(16) for _ in range(8)]
            await soc.load(inputs);await soc.done()
            expected=evaluate([layer],inputs)[0]
            assert await soc.read_outputs()==(expected,expected)
            assert soc.flash.served==list(soc.image)
            assert soc.flash.frames==[[3,0,0,0]+[255]*len(soc.image)]


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_weight_lane_and_nibble_order(dut):
    """Walk each input lane; varied signed weight nibbles expose serialization and routing mistakes."""
    with Case(dut,'soc_weight_lane_and_nibble_order') as c:
        layer=[Neuron(tuple(signed4(3*i+j) for i in range(8))) for j in range(8)]
        for lane in range(8):
            inputs=[0]*8;inputs[lane]=4
            soc=system(dut,c.trace,[layer]);await soc.reset();await soc.load(inputs);await soc.done()
            expected=[n.weights[lane]&15 for n in layer]
            assert (await soc.read_outputs())[0]==expected


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_signed_input_weight_and_cancellation(dut):
    """Negative inputs, negative weights and cancellation through actual flash/MAC/output stages."""
    with Case(dut,'soc_signed_input_weight_and_cancellation') as c:
        errors=Errors(c.trace)
        for inputs,weights in [([-1]*8,[1,0,0,0,0,0,0,0]),([1]*8,[-1,0,0,0,0,0,0,0]),
                ([-7]*8,[-7,0,0,0,0,0,0,0]),([-1,1]*4,[1]*8)]:
            layer=[Neuron(tuple(weights),signed4(i),i) for i in range(8)]
            soc=system(dut,c.trace,[layer]);await soc.reset();await soc.load(inputs);await soc.done(check=False)
            expected=evaluate([layer],inputs)[0]
            actual=(await soc.read_outputs())[0]
            errors.check('signed network outputs',actual,expected,inputs=inputs,weights=weights)
            errors.check('signed network stages',dict(soc.monitor.errors.counts),{},inputs=inputs,weights=weights)
        errors.finish()


@cocotb.test(timeout_time=20,timeout_unit='ms')
async def soc_multilayer_positive_relu(dut):
    """Three dense layers and atomic double-buffering in the domain unaffected by signed multiplication."""
    with Case(dut,'soc_multilayer_positive_relu') as c:
        for trial in range(8):
            layers=[[Neuron(tuple(c.rng.randrange(8) for _ in range(8)),c.rng.randrange(-8,8),1) for _ in range(8)] for _ in range(3)]
            inputs=[c.rng.randrange(8) for _ in range(8)]
            soc=system(dut,c.trace,layers);await soc.reset();await soc.load(inputs);await soc.done()
            expected=evaluate(layers,inputs)[0]
            assert await soc.read_outputs()==(expected,expected)
            assert len(soc.monitor.layers)==3 and len(soc.flash.frames)==1
            c.trace.event('check',trial=trial,layers=3,outputs=expected)


@cocotb.test(timeout_time=20,timeout_unit='ms')
async def soc_mixed_activation_multilayer_signed(dut):
    """A sigmoid layer feeds signed multiplication; high output codes must be reinterpreted correctly."""
    with Case(dut,'soc_mixed_activation_multilayer_signed') as c:
        layers=[[Neuron((0,)*8,0,5) for _ in range(8)],
                [Neuron(tuple(1 if i==j else 0 for i in range(8)),0,j) for j in range(8)]]
        soc=system(dut,c.trace,layers);await soc.reset();await soc.load([0]*8);await soc.done()
        assert (await soc.read_outputs())[0]==evaluate(layers,[0]*8)[0]


@cocotb.test(timeout_time=30,timeout_unit='ms')
async def soc_long_stream_crosses_byte_address_boundary(dut):
    """Nine layers cross flash byte 255 without restarting the READ command or CS frame."""
    with Case(dut,'soc_long_stream_crosses_byte_address_boundary') as c:
        layers=[[Neuron((0,)*8,signed4(layer+i),i) for i in range(8)] for layer in range(9)]
        soc=system(dut,c.trace,layers);await soc.reset();await soc.load([0]*8);await soc.done()
        assert len(soc.image)==361 and soc.flash.served==list(soc.image)
        assert len(soc.flash.frames)==1 and len(soc.monitor.layers)==9
        assert await soc.read_outputs()==(evaluate(layers,[0]*8)[0],evaluate(layers,[0]*8)[0])


@cocotb.test(timeout_time=20,timeout_unit='ms')
async def soc_every_end_header(dut):
    """Every instruction with bit 7 set terminates without fetching weights, even before a layer."""
    with Case(dut,'soc_every_end_header') as c:
        for end in range(128,256):
            soc=SoC(dut,c.trace,bytes([end]));await soc.reset()
            inputs=[(end+i)&15 for i in range(8)]
            await soc.load(inputs);await soc.done()
            assert (await soc.read_outputs())[0]==inputs
            assert soc.flash.served==[end] and len(soc.flash.frames[0])==5


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_partial_layer_end_characterization(dut):
    """END after one to seven neurons exposes partial temporary outputs but does not commit a layer."""
    with Case(dut,'soc_partial_layer_end_characterization') as c:
        for count in range(1,8):
            records=[Neuron((0,)*8,i+1) for i in range(count)]
            soc=SoC(dut,c.trace,encode_records(records),records);await soc.reset();await soc.load([3]*8);await soc.done()
            assert await soc.read_outputs()==([3]*8,list(range(1,count+1))+[0]*(8-count))
            c.trace.event('check',classification='partial layer END characterization',neurons=count)


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_output_mux_and_stability_after_end(dut):
    """All output addresses, both nibbles, stable stored results and no SPI clocks after END."""
    with Case(dut,'soc_output_mux_and_stability_after_end') as c:
        layer=[Neuron((0,)*8,signed4(i*2),0) for i in range(8)]
        soc=system(dut,c.trace,[layer]);await soc.reset();await soc.load([0]*8);await soc.done()
        expected=evaluate([layer],[0]*8)[0];edges=soc.flash.edges
        for _ in range(3):
            assert await soc.read_outputs()==(expected,expected)
            await soc.tick(100)
        assert soc.flash.edges==edges and value(dut.state)==9


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_rearm_then_multiple_inferences(dut):
    """Explicit rearm followed by complete vectors restarts flash at zero without resetting the chip."""
    with Case(dut,'soc_rearm_then_multiple_inferences') as c:
        soc=system(dut,c.trace,[identity()]);await soc.reset()
        for trial in range(5):
            if trial:await soc.rearm()
            inputs=[c.rng.randrange(16) for _ in range(8)]
            await soc.load(inputs);await soc.done()
            assert (await soc.read_outputs())[0]==inputs
        assert len(soc.flash.frames)==5


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_first_write_after_end_contract(dut):
    """Characterize rearm-only first strobe, or require it to store the next input with --input-contract edge."""
    with Case(dut,'soc_first_write_after_end_contract') as c:
        soc=system(dut,c.trace,[identity()]);await soc.reset();await soc.load([1]*8);await soc.done()
        expected=[2]*8
        if os.getenv('NPU_INPUT_CONTRACT','rtl')=='rtl':expected[0]=1
        soc.monitor.arm(expected,soc.program)
        for i in range(8):await soc.save(i,2)
        await soc.done(check=False)
        actual=(await soc.read_outputs())[0]
        c.trace.event('check',classification='rearm contract',actual=actual,expected=expected)
        assert actual==expected


@cocotb.test(timeout_time=20,timeout_unit='ms')
async def soc_save_during_each_busy_stage(dut):
    """Input strobes in READ/address/instruction/weight/MAC/activation stages cannot overwrite the live layer."""
    with Case(dut,'soc_save_during_each_busy_stage') as c:
        for state in range(1,9):
            soc=system(dut,c.trace,[identity()]);await soc.reset();await soc.load([1,2,3,4,5,6,7,0])
            await soc.until(lambda:value(dut.state)==state,2000,f'busy state {state}')
            await soc.save(0,15);await soc.done()
            assert (await soc.read_outputs())[0]==[1,2,3,4,5,6,7,0]
            c.trace.event('check',busy_save_state=state)


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_reset_clears_pending_save(dut):
    """A pending save must not survive reset and write/start inference without a new event."""
    with Case(dut,'soc_reset_clears_pending_save') as c:
        soc=system(dut,c.trace,[identity()]);await soc.reset()
        dut.ui_in.value=128|(7<<4)|5;await soc.tick()
        assert value(dut.save_pulse)==1
        dut.rst_n.value=0;await soc.tick(5)
        assert value(dut.save_pulse)==0 and value(dut.save_old)==0,'Save edge-detector state survives reset'
        dut.ui_in.value=(7<<4)|5;dut.rst_n.value=1;await soc.tick(20)
        assert value(dut.activations)==0 and value(dut.state)==0 and value(dut.uio_out)&4


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_reset_pending_save_no_phantom_inference(dut):
    """Pin-visible regression: reset must prevent a stale slot-7 save from launching a flash READ."""
    with Case(dut,'soc_reset_pending_save_no_phantom_inference') as c:
        soc=system(dut,c.trace,[identity()]);await soc.reset()
        dut.ui_in.value=128|(7<<4)|5;await soc.tick()
        dut.rst_n.value=0;await soc.tick(4)
        dut.ui_in.value=(7<<4)|5;dut.rst_n.value=1;await soc.tick(20)
        actual=unpack(value(dut.activations))
        c.trace.event('check',after_reset=actual,cs=(value(dut.uio_out)>>2)&1,state=value(dut.state))
        assert actual==[0]*8 and value(dut.state)==0 and value(dut.uio_out)&4,'Stale save causes a post-reset write or inference'


@cocotb.test(timeout_time=30,timeout_unit='ms')
async def soc_reset_each_state_and_recovery(dut):
    """Reset in every control state; clear RAM/output/busy, abort serial frame and run a new inference."""
    with Case(dut,'soc_reset_each_state_and_recovery') as c:
        for state in range(10):
            soc=system(dut,c.trace,[identity()]);await soc.reset()
            if state:await soc.load([1]*8)
            await soc.until(lambda:value(dut.state)==state,4000,f'reset state {state}')
            dut.rst_n.value=0;await soc.tick(4)
            assert value(dut.activations)==value(dut.temporary)==value(dut.weights)==0
            assert value(dut.uio_out)==4 and value(dut.spi_busy)==0
            dut.ui_in.value=0;dut.rst_n.value=1;await soc.tick(3)
            assert value(dut.state)==0
            await soc.load([2]*8);await soc.done()
            assert (await soc.read_outputs())[0]==[2]*8
            c.trace.event('check',reset_state=state)


@cocotb.test(timeout_time=30,timeout_unit='ms')
async def soc_seeded_full_networks(dut):
    """Random one-to-four-layer signed networks; collect failures per trial rather than stopping at the first vector."""
    with Case(dut,'soc_seeded_full_networks') as c:
        errors=Errors(c.trace)
        for trial in range(40 if os.getenv('NPU_STRESS')=='1' else 8):
            layers=[[Neuron(tuple(c.rng.randrange(-8,8) for _ in range(8)),c.rng.randrange(-8,8),c.rng.randrange(8)) for _ in range(8)] for _ in range(c.rng.randrange(1,5))]
            inputs=[c.rng.randrange(16) for _ in range(8)]
            soc=system(dut,c.trace,layers);await soc.reset();await soc.load(inputs);await soc.done(check=False)
            expected=evaluate(layers,inputs)[0]
            errors.check('network', (await soc.read_outputs())[0], expected,trial=trial,layers=len(layers),inputs=inputs)
            errors.check('network stages',dict(soc.monitor.errors.counts),{},trial=trial)
            c.trace.event('check',trial=trial,stage_mismatches=dict(soc.monitor.errors.counts))
        errors.finish()


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_trained_xor_flash_export(dut):
    """Execute the trainer's exported XOR model through real flash pins and check all four labelled cases."""
    from pathlib import Path
    from model import load_network
    from network_tool import load_dataset
    with Case(dut,'soc_trained_xor_flash_export') as c:
        here=Path(__file__).resolve().parent
        layers=load_network(here/'examples/xor-trained.json')
        inputs,targets=load_dataset(here/'examples/xor-dataset.json')
        soc=system(dut,c.trace,layers);await soc.reset()
        for index,(x,target) in enumerate(zip(inputs,targets)):
            if index:await soc.rearm()
            await soc.load(x);await soc.done()
            actual=(await soc.read_outputs())[0]
            assert actual[:len(target)]==target
            assert actual==evaluate(layers,x)[0]
            c.trace.event('check',training_case=index,inputs=x,outputs=actual,target=target)
        assert len(soc.image)==41 and len(soc.flash.frames)==4


@cocotb.test(timeout_time=10,timeout_unit='ms')
async def soc_train_two_layers_and_execute(dut):
    """Train an eight-wide two-layer model, serialize it, then verify both examples on the RTL."""
    from network_tool import train
    with Case(dut,'soc_train_two_layers_and_execute') as c:
        inputs=[[0]*8,[4,0,0,0,0,0,0,0]];targets=[[0],[1]]
        layers,report=train(inputs,targets,depth=2,weight_domain='nonnegative',epochs=3,restarts=3,seed=73)
        assert report['squared_error']==0
        soc=system(dut,c.trace,layers);await soc.reset()
        for index,(x,target) in enumerate(zip(inputs,targets)):
            if index:await soc.rearm()
            await soc.load(x);await soc.done()
            actual=(await soc.read_outputs())[0]
            assert actual[:1]==target
            c.trace.event('check',training_case=index,depth=2,inputs=x,outputs=actual,target=target)
        assert len(soc.image)==81
