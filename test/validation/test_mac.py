"""Arithmetic tests do not reuse HDL expressions or the packed-array decoder."""
import os
import cocotb
from cocotb.triggers import Timer
from common import Case,Errors,value
from model import mac,pack


async def check(dut,trace,errors,inputs,weights,bias,**context):
    dut.inputs.value=pack(inputs);dut.weights.value=pack(weights);dut.bias.value=bias & 15
    await Timer(1,unit='ns')
    expected=mac(inputs,weights,bias)
    products=[(value(dut.products) >> (8*i)) & 255 for i in range(8)]
    for lane,(actual,p) in enumerate(zip(products,expected['products'])):
        errors.check('product',actual,p & 255,lane=lane,inputs=inputs,weights=weights,**context)
    errors.check('sum',value(dut.sum),expected['wrapped_sum'],inputs=inputs,weights=weights,**context)
    errors.check('biased output',value(dut.result),expected['preactivation'],bias=bias,inputs=inputs,weights=weights,**context)
    trace.event('mac',inputs=inputs,weights=weights,bias=bias,products_actual=products,
                actual=value(dut.result),expected=expected,**context)


@cocotb.test(timeout_time=1,timeout_unit='ms')
async def mac_all_signed_pairs_each_lane(dut):
    """All 16×16 signed operand pairs on each of eight independently selected lanes."""
    with Case(dut,'mac_all_signed_pairs_each_lane') as c:
        errors=Errors(c.trace)
        for lane in range(8):
            for a in range(-8,8):
                for w in range(-8,8):
                    inputs=[0]*8;weights=[0]*8;inputs[lane]=a;weights[lane]=w
                    await check(dut,c.trace,errors,inputs,weights,0)
        c.trace.event('check',vectors=2048,mismatches=dict(errors.counts));errors.finish()


@cocotb.test(timeout_time=1,timeout_unit='ms')
async def mac_nonnegative_pairs_and_lane_isolation(dut):
    """Positive-domain baseline and zero-weight lanes isolate routing from signedness."""
    with Case(dut,'mac_nonnegative_pairs_and_lane_isolation') as c:
        errors=Errors(c.trace)
        for lane in range(8):
            for a in range(8):
                for w in range(8):
                    inputs=[7]*8;weights=[0]*8;inputs[lane]=a;weights[lane]=w
                    await check(dut,c.trace,errors,inputs,weights,0)
        errors.finish()


@cocotb.test(timeout_time=1,timeout_unit='ms')
async def mac_every_sum_and_bias_wrap(dut):
    """Every four-bit accumulated sum × every signed bias; overflow wraps, not saturates."""
    with Case(dut,'mac_every_sum_and_bias_wrap') as c:
        errors=Errors(c.trace)
        for total in range(16):
            remaining=total;weights=[]
            for _ in range(8):w=min(7,remaining);weights.append(w);remaining-=w
            for bias in range(-8,8):await check(dut,c.trace,errors,[4]*8,weights,bias,total=total)
        errors.finish()


@cocotb.test(timeout_time=1,timeout_unit='ms')
async def mac_quantizes_each_product_before_sum(dut):
    """Discard each product's low two bits before summing; expose sum-then-shift mistakes."""
    with Case(dut,'mac_quantizes_each_product_before_sum') as c:
        errors=Errors(c.trace)
        for a in range(1,8):
            for w in range(1,8):
                await check(dut,c.trace,errors,[a]*8,[w]*8,0)
        assert mac([1]*8,[1]*8,0)['preactivation']==0
        errors.finish()


@cocotb.test(timeout_time=1,timeout_unit='ms')
async def mac_negative_fraction_rounds_down(dut):
    """Negative products -1,-2,-3 must contribute -1, with arithmetic floor division."""
    with Case(dut,'mac_negative_fraction_rounds_down') as c:
        errors=Errors(c.trace)
        for a in (-1,-2,-3):
            for count in range(1,9):await check(dut,c.trace,errors,[a]*count+[0]*(8-count),[1]*8,0)
        errors.finish()


@cocotb.test(timeout_time=1,timeout_unit='ms')
async def mac_eight_lane_extrema_and_cancellation(dut):
    """Wide products, wrap, bias extremes and cancellation across all lanes."""
    with Case(dut,'mac_eight_lane_extrema_and_cancellation') as c:
        errors=Errors(c.trace)
        for inputs,weights in [([7]*8,[7]*8),([-8]*8,[-8]*8),([-8]*8,[7]*8),
            ([-4,4]*4,[4]*8),([-8,-7,-1,0,1,2,6,7],[7,6,2,1,0,-1,-7,-8])]:
            for bias in range(-8,8):await check(dut,c.trace,errors,inputs,weights,bias)
        errors.finish()


@cocotb.test(timeout_time=1,timeout_unit='ms')
async def mac_seeded_mixed_vectors(dut):
    """Deterministic random eight-lane products, sum and bias against signed integer math."""
    with Case(dut,'mac_seeded_mixed_vectors') as c:
        errors=Errors(c.trace)
        count=20000 if os.getenv('NPU_STRESS')=='1' else 2000
        for _ in range(count):
            await check(dut,c.trace,errors,[c.rng.randrange(-8,8) for _ in range(8)],
                        [c.rng.randrange(-8,8) for _ in range(8)],c.rng.randrange(-8,8))
        c.trace.event('check',vectors=count,mismatches=dict(errors.counts));errors.finish()
