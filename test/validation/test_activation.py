import cocotb
from cocotb.triggers import Timer
from common import Case,Errors,value
from model import ACTIVATIONS,activation,signed4


def make_test(function,name):
    async def test(dut):
        with Case(dut,'activation_'+name,f'All 16 input bit patterns for {name}; check four-bit boundary behavior.') as c:
            errors=Errors(c.trace);dut.function_id.value=function
            for x in range(16):
                dut.x.value=x;await Timer(1,unit='ns')
                expected=activation(x,function)
                c.trace.event('activation',function=name,input=x,signed_input=signed4(x),actual=value(dut.result),expected=expected)
                errors.check('activation',value(dut.result),expected,x=x,function=name)
            errors.finish()
    test.__name__='activation_'+name;test.__qualname__=test.__name__
    test.__doc__=f'All sixteen bit patterns for {name}.'
    return cocotb.test(timeout_time=1,timeout_unit='ms')(test)

for code,name in enumerate(ACTIVATIONS):globals()['activation_'+name]=make_test(code,name)


@cocotb.test(timeout_time=1,timeout_unit='ms')
async def activation_unsigned_outputs_reenter_signed_domain(dut):
    """Characterize sigmoid nibble outputs reinterpreted as signed inputs in a next layer."""
    with Case(dut,'activation_unsigned_outputs_reenter_signed_domain') as c:
        for first in (5,7):
            for x in range(16):
                dut.function_id.value=first;dut.x.value=x;await Timer(1,unit='ns')
                y=value(dut.result)
                assert y==activation(x,first)
                for second in range(8):
                    dut.function_id.value=second;dut.x.value=y;await Timer(1,unit='ns')
                    expected=activation(y,second)
                    c.trace.event('activation',first=first,second=second,input=x,intermediate=y,
                                  signed_intermediate=signed4(y),actual=value(dut.result),expected=expected,
                                  classification='nibble interpretation characterization')
                    assert value(dut.result)==expected
