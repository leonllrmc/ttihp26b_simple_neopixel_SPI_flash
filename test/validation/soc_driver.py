"""Pin-only SPI flash peer plus stage-by-stage independent inference scoreboard."""
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time
from common import Clocked,Errors,value
from model import pack,unpack

STATES=('INPUT','READ_CMD','ADDRESS_HIGH','ADDRESS_MID','ADDRESS_LOW','INSTRUCTION',
        'WEIGHTS','MAC','ACTIVATION','END')


class Flash:
    def __init__(self,dut,trace,image):
        self.dut,self.trace,self.image=dut,trace,bytes(image)
        self.previous_cs=1;self.previous_sck=0;self.bits=0;self.byte=0
        self.frame=[];self.frames=[];self.address=0;self.reading=False
        self.served=[];self.edges=0;self.last_rise=None;self.intervals=[];self.high_mosi=0

    def miso(self,bit):
        self.dut.uio_in.value=(value(self.dut.uio_in)&0xf7)|(int(bit)<<3)

    def sample(self):
        d=self.dut;pins=value(d.uio_out);cs=(pins>>2)&1;sck=pins&1;mosi=(pins>>1)&1
        if not value(d.rst_n):
            self.previous_cs=cs;self.previous_sck=sck;self.bits=self.byte=0
            self.frame=[];self.reading=False;self.last_rise=None;self.miso(1);return
        if cs!=self.previous_cs:
            self.trace.event('spi_cs',active=not cs,sck=sck)
            if not cs:
                assert not sck,'CS asserted with clock high'
                self.bits=self.byte=0;self.frame=[];self.address=0;self.reading=False;self.last_rise=None
            else:
                assert self.bits==0,'CS ended mid-byte'
                assert len(self.frame)>=5,'Incomplete READ command/address/data frame'
                self.frames.append(self.frame[:]);self.miso(1)
        if not cs and sck and not self.previous_sck:
            self.edges+=1;self.high_mosi=mosi
            self.byte=(self.byte<<1)|mosi;self.bits+=1
            now=float(get_sim_time(unit='ns'))
            if self.bits>1:
                period=now-self.last_rise
                assert abs(period-40)<0.01,f'Full-chip SPI bit period {period} ns; expected 40 ns'
                self.intervals.append(period)
            self.last_rise=now
            self.trace.event('spi_bit',index=self.bits,mosi=mosi,miso=(value(d.uio_in)>>3)&1)
            if self.bits==8:
                index=len(self.frame);byte=self.byte;self.frame.append(byte)
                if index==0:assert byte==3,f'Flash command is {byte:02x}, expected 03'
                elif index<=3:
                    self.address=(self.address<<8)|byte
                    if index==3:
                        assert self.address==0,f'Unexpected starting flash address {self.address}'
                        self.reading=True;self.trace.event('spi_address',address=self.address)
                else:
                    assert byte==255,f'Dummy MOSI byte {byte:02x} is not FF'
                    assert self.address<len(self.image),f'Read beyond supplied image at {self.address}'
                    self.served.append(self.image[self.address])
                    self.trace.event('spi_byte',direction='miso',address=self.address,data=self.image[self.address])
                    self.address+=1
                self.trace.event('spi_byte',direction='mosi',index=index,data=byte)
                self.bits=self.byte=0
        elif not cs and sck:assert mosi==self.high_mosi,'MOSI changed while SCLK high'
        if not cs and not sck and self.previous_sck:
            high=float(get_sim_time(unit='ns'))-self.last_rise
            assert abs(high-20)<0.01,f'Full-chip SPI high width {high} ns; expected 20 ns'
            byte=self.image[self.address] if self.reading and self.address<len(self.image) else 255
            self.miso((byte >> (7-self.bits)) & 1)
        self.previous_cs,self.previous_sck=cs,sck


class Monitor:
    def __init__(self,soc):
        self.soc=soc;self.previous=None;self.enabled=False;self.cursor=0
        self.records=[];self.layers=[];self.input_events=[];self.errors=Errors(soc.trace)

    def arm(self,inputs,records):
        self.expected=[x&15 for x in inputs];self.partial=[0]*8;self.program=list(records)
        self.cursor=0;self.records=[];self.layers=[];self.enabled=True

    def snapshot(self):
        d=self.soc.dut
        return dict(state=value(d.state),activations=unpack(value(d.activations)),temporary=unpack(value(d.temporary)),
            weights=unpack(value(d.weights)),instruction=value(d.instruction),preactivation=value(d.preactivation),
            mac=value(d.mac_result),neuron=value(d.neuron_index),weight_index=value(d.weight_index),
            save=value(d.save_pulse),save_old=value(d.save_old),ui=value(d.ui_in),pins=value(d.uo_out))

    def sample(self):
        d=self.soc.dut
        if not value(d.rst_n):self.previous=None;self.enabled=False;return
        after=self.snapshot();before=self.previous
        index=(after['ui']>>4)&7
        assert after['pins']==after['activations'][index] | (after['temporary'][index]<<4),'Output address mux mismatch'
        assert value(d.uio_oe)==7 and value(d.uio_out)&0xf8==0,'Invalid output directions or unused pins'
        if self.soc.trace.level=='cycles':self.soc.trace.event('npu_state',**after)
        if before is not None:
            if before['state']==0 and before['save']:
                event=dict(address=index,data=after['ui']&15,activations=after['activations'])
                self.input_events.append(event);self.soc.trace.event('input',**event)
            if self.enabled and before['state'] in (7,8):
                assert self.cursor<len(self.program),'Unexpected extra neuron computation'
                n=self.program[self.cursor];lane=self.cursor%8;layer=self.cursor//8
                detail=n.evaluate(self.expected)
                context=dict(layer=layer,neuron=lane)
                if before['state']==7:
                    self.errors.check('instruction decode',before['instruction'],n.encode()[0],**context)
                    self.errors.check('weight order',before['weights'],[w&15 for w in n.weights],**context)
                    self.errors.check('source activations',before['activations'],self.expected,**context)
                    self.errors.check('neuron index',before['neuron'],lane,**context)
                    self.errors.check('MAC',before['mac'],detail['preactivation'],**context)
                    self.errors.check('MAC latch',after['preactivation'],detail['preactivation'],**context)
                    self.soc.trace.event('mac',inputs=self.expected,weights=n.weights,bias=n.bias,
                        actual=after['preactivation'],expected=detail,**context)
                else:
                    self.partial[lane]=detail['output']
                    self.errors.check('neuron output',after['temporary'][lane],detail['output'],**context)
                    # Other temporary lanes must not be overwritten at this stage.
                    for other in range(8):
                        if other!=lane:self.errors.check('temporary lane isolation',after['temporary'][other],before['temporary'][other],other=other,**context)
                    self.records.append(dict(**context,actual=after['temporary'][lane],expected=detail['output']))
                    self.soc.trace.event('neuron',function=n.function,actual=after['temporary'][lane],expected=detail['output'],**context)
                    if lane==7:
                        self.expected=self.partial[:];self.layers.append(self.expected[:])
                        self.errors.check('layer commit',after['activations'],self.expected,layer=layer)
                        self.errors.check('weights cleared',after['weights'],[0]*8,layer=layer)
                        self.soc.trace.event('layer',layer=layer,actual=after['activations'],expected=self.expected)
                    else:self.errors.check('atomic layer commit',after['activations'],before['activations'],**context)
                    self.cursor+=1
        self.previous=after


class SoC(Clocked):
    def __init__(self,dut,trace,image,records=()):
        super().__init__(dut,trace)
        dut.rst_n.value=0;dut.ena.value=1;dut.ui_in.value=0;dut.uio_in.value=0
        self.image=bytes(image);self.program=list(records)
        self.flash=Flash(dut,trace,image);self.monitor=Monitor(self)
        self.peers=[self.flash,self.monitor]
        (trace.directory/(trace.name+'.flash.bin')).write_bytes(self.image)

    async def save(self,index,data,high=2,low=2):
        self.dut.ui_in.value=(index<<4)|(data&15)|128
        await self.tick(high)
        self.dut.ui_in.value=(index<<4)|(data&15)
        await self.tick(low)

    async def load(self,inputs,order=None):
        self.monitor.arm(inputs,self.program)
        for index in (order or range(8)):await self.save(index,inputs[index])

    async def rearm(self):
        assert value(self.dut.state)==9
        await self.save(0,0)
        assert value(self.dut.state)==0

    async def done(self,limit=None,check=True):
        await self.until(lambda:value(self.dut.state)==9,limit or (1000+len(self.image)*30),'network END')
        await self.tick(3)
        assert value(self.dut.uio_out)&5==4,'Flash not deselected with idle SCLK'
        if check:
            assert self.monitor.cursor==len(self.program),'Missing neuron computations'
            self.monitor.errors.finish()

    async def read_outputs(self):
        outputs=[];temporary=[]
        for index in range(8):
            self.dut.ui_in.value=index<<4
            await Timer(2,unit='ns')
            outputs.append(value(self.dut.uo_out)&15);temporary.append(value(self.dut.uo_out)>>4)
            self.trace.event('check',output_address=index,output=outputs[-1],temporary=temporary[-1])
        return outputs,temporary
