"""Mode-0 pin scoreboard; no internal shift register is used as the expected data."""
import os
import cocotb
from common import Case,Clocked,value


class SPI(Clocked):
    def __init__(self,dut,trace):
        super().__init__(dut,trace)
        dut.send.value=0;dut.din.value=0;dut.miso.value=0
        self.divider=int(os.getenv('NPU_SPI_DIV','1'))

    async def transfer(self,tx,rx,busy_injection=False):
        d=self.dut
        assert not value(d.busy) and not value(d.sclk)
        d.din.value=tx;d.miso.value=rx >> 7;d.send.value=1
        await self.tick();d.send.value=0
        assert value(d.busy) and not value(d.cs_n)
        last=0;bits=[];edges=[];done=0;mosi_high=None
        for cycle in range(20*self.divider+12):
            d.send.value=int(busy_injection and cycle==3*self.divider)
            if busy_injection and cycle==3*self.divider:d.din.value=tx ^ 255
            await self.tick();sck=value(d.sclk)
            if sck and not last:
                bits.append(value(d.mosi));edges.append(self.cycles);mosi_high=value(d.mosi)
                self.trace.event('spi_bit',index=len(bits),mosi=bits[-1],miso=value(d.miso))
            if sck:assert value(d.mosi)==mosi_high,'MOSI changed while SCK high'
            if not sck and last and len(bits)<8:d.miso.value=(rx >> (7-len(bits))) & 1
            if value(d.done):
                done+=1
                assert len(bits)==8 and sck and not last
                assert value(d.dout)==rx and not value(d.busy)
            last=sck
            if value(d.cs_n):break
        assert len(bits)==8 and done==1
        actual=sum(bit << (7-i) for i,bit in enumerate(bits))
        assert actual==tx and all(b-a==2*self.divider for a,b in zip(edges,edges[1:]))
        assert value(d.cs_n) and not value(d.sclk) and not value(d.busy)
        await self.tick();assert not value(d.done)
        self.trace.event('spi_byte',tx=actual,rx=rx,half_period_cycles=self.divider)


@cocotb.test(timeout_time=5,timeout_unit='ms')
async def spi_all_bytes_full_duplex(dut):
    """All TX bytes and a permutation of all RX bytes; bit order, clock, CS and done."""
    with Case(dut,'spi_all_bytes_full_duplex') as c:
        spi=SPI(dut,c.trace);await spi.reset()
        for tx in range(256):await spi.transfer(tx,((tx*73)^0x96)&255)


@cocotb.test(timeout_time=5,timeout_unit='ms')
async def spi_seeded_data_and_idle_gaps(dut):
    """Independently randomized TX/RX values and variable gaps between transactions."""
    with Case(dut,'spi_seeded_data_and_idle_gaps') as c:
        spi=SPI(dut,c.trace);await spi.reset()
        for _ in range(200):
            await spi.tick(c.rng.randrange(15))
            await spi.transfer(c.rng.randrange(256),c.rng.randrange(256))


@cocotb.test(timeout_time=1,timeout_unit='ms')
async def spi_send_during_busy_is_ignored(dut):
    """Busy commands and changing din cannot corrupt the active frame."""
    with Case(dut,'spi_send_during_busy_is_ignored') as c:
        spi=SPI(dut,c.trace);await spi.reset()
        for tx in (0,255,0xa5,0x5a):await spi.transfer(tx,tx ^ 0x69,True)


@cocotb.test(timeout_time=1,timeout_unit='ms')
async def spi_tail_window_pipeline(dut):
    """Three queued bytes share CS; acceptance includes the trailing falling-edge window."""
    with Case(dut,'spi_tail_window_pipeline') as c:
        spi=SPI(dut,c.trace);await spi.reset()
        txs=[0xa5,0x3c,0x81];rxs=[0x96,0x5a,0x7e]
        dut.din.value=txs[0];dut.miso.value=rxs[0] >> 7;dut.send.value=1
        await spi.tick();dut.send.value=0
        frames=[];bits=[];index=0;last=0;pending=False
        for _ in range(60*spi.divider+30):
            await spi.tick();sck=value(dut.sclk)
            if pending:dut.send.value=0;pending=False
            if sck and not last:
                bits.append(value(dut.mosi))
                if len(bits)==8:
                    assert value(dut.done) and value(dut.dout)==rxs[index]
                    frames.append(sum(bit << (7-i) for i,bit in enumerate(bits)))
                    c.trace.event('spi_byte',frame=index,tx=frames[-1],rx=value(dut.dout))
                    if index<2:dut.din.value=txs[index+1];dut.send.value=1;pending=True
            if not sck and last:
                if len(bits)==8:index+=1;bits=[]
                if index<3:dut.miso.value=(rxs[index] >> (7-len(bits))) & 1
            last=sck
            if value(dut.cs_n):assert index==3;break
        assert frames==txs and index==3


@cocotb.test(timeout_time=5,timeout_unit='ms')
async def spi_reset_at_every_transfer_phase(dut):
    """Reset at each clock position, verify idle levels and a fresh successful exchange."""
    with Case(dut,'spi_reset_at_every_transfer_phase') as c:
        spi=SPI(dut,c.trace)
        for delay in range(16*spi.divider+1):
            await spi.reset();dut.send.value=1;dut.din.value=0xa5
            await spi.tick();dut.send.value=0;await spi.tick(delay);await spi.reset()
            c.trace.event('check',reset_phase=delay)
            assert not value(dut.busy) and not value(dut.done) and not value(dut.sclk) and value(dut.cs_n)
            await spi.transfer(0x5a,0xc3)
