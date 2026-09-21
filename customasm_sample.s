
#subruledef reladdr
{
	{addr: u8} =>
	{
		reladdr = addr - $
		;assert(reladdr <=  0x7f)
		;assert(reladdr >= !0x7f)
		(reladdr)`8
	}
}

#subruledef absaddr
{
	{addr: u8} =>
	{
		(addr)`8
	}
}


#ruledef
{
	add  r{regout: u3}, r{reg1: u3}, r{reg2: u3} => 0x0 @ regout @ reg1 @ reg2 @ 0b000
    sub  r{regout: u3}, r{reg1: u3}, r{reg2: u3} => 0x1 @ regout @ reg1 @ reg2 @ 0b000
    lsl  r{regout: u3}, r{reg1: u3}=> 0x2 @ regout @ reg1 @ 0b000 @ 0b000
    lsr  r{regout: u3}, r{reg1: u3} => 0x3 @ regout @ reg1 @ 0b000 @ 0b000
    asr  r{regout: u3}, r{reg1: u3} => 0x4 @ regout @ reg1 @ 0b000 @ 0b000
    and  r{regout: u3}, r{reg1: u3}, r{reg2: u3} => 0x5 @ regout @ reg1 @ reg2 @ 0b000
	or  r{regout: u3}, r{reg1: u3}, r{reg2: u3} => 0x6 @ regout @ reg1 @ reg2 @ 0b000
	not  r{regout: u3}, r{reg1: u3} => 0x7 @ regout @ reg1 @ 0b000 @ 0b000
    
	ldconst r{reg: u3}, {value: i8} => 0x8 @ reg @ 0b0 @ value


	bra  {addrinc: reladdr} => 0xB @ 0b0000 @ addrinc
    brcond  {condmask: u4} {addrinc: reladdr}  => 0xA @ condmask @ addrinc
	
    bvc   {addrinc: reladdr}  => 0xA @ 0b0000 @ addrinc
    bvs   {addrinc: reladdr}  => 0xA @ 0b0001 @ addrinc
	
    bcs   {addrinc: reladdr}  => 0xA @ 0b0010 @ addrinc
    bgeu   {addrinc: reladdr}  => 0xA @ 0b0010 @ addrinc
    bcc   {addrinc: reladdr}  => 0xA @ 0b0011 @ addrinc
    bltu   {addrinc: reladdr}  => 0xA @ 0b0011 @ addrinc
	
	bns   {addrinc: reladdr}  => 0xA @ 0b0100 @ addrinc
    bnc   {addrinc: reladdr}  => 0xA @ 0b0101 @ addrinc
	
	bgt   {addrinc: reladdr}  => 0xA @ 0b0110 @ addrinc
    bge   {addrinc: reladdr}  => 0xA @ 0b0111 @ addrinc
	
	bzs   {addrinc: reladdr}  => 0xA @ 0b1000 @ addrinc
	beq   {addrinc: reladdr}  => 0xA @ 0b1000 @ addrinc
    bzc   {addrinc: reladdr}  => 0xA @ 0b1001 @ addrinc
    bne   {addrinc: reladdr}  => 0xA @ 0b1001 @ addrinc

	blt   {addrinc: reladdr}  => 0xA @ 0b1010 @ addrinc
    ble   {addrinc: reladdr}  => 0xA @ 0b1011 @ addrinc
	bgtu   {addrinc: reladdr}  => 0xA @ 0b1100 @ addrinc
    bleu   {addrinc: reladdr}  => 0xA @ 0b1101 @ addrinc
    
	
	bl r{reglink: u3} {addr: absaddr} => 0xE @ reglink @ 0b0 @ addr
	br r{reglink: u3} =>  0xF @ reglink @ 0b000000000
	
	bir =>  0xF @ 0x001
	
	ld r{regdata: u3}, (r{regaddr: u3}+{offset: i6}) => 0xc @ regdata @ regaddr @ offset 
	st r{regdata: u3}, (r{regaddr: u3}+{offset: i6}) => 0xd @ regdata @ regaddr @ offset 
	
	; regdest = regorigin & regorigin
	tr r{regdest: u3}, r{regorigin: u3} => 0x5 @ regdest @ regorigin @ regorigin @ 0b000 
	
	nop => 0x5000 ; R0 = R0 & R0
}

#bankdef rstvec   { addr = 0x00,    size = 0x01,   outp=0,   data=0xB010, bits=16  }
#bankdef irqvec   { addr = 0x01,    size = 0x0F,   outp=16*0x01, fill=true, bits=16 }
#bankdef programstart   { addr = 0x10,    size = 0xDF,   outp=16*0x10, bits=16 }

#bank rstvec
Reset:
; do not change
bra $10

#bank irqvec
Irq_Vector:
bir ; always add BIR at end
nop ; always put nop after to be sure nothing bad happends

#bank programstart
Start:

ldconst r1, 1
ldconst r2, 0x00
ldconst r3, 0x00
ldconst r4, 0x0F

.infloop:
;bra .skipwait
	ldconst r0, 0x00
	.wait_loop:
		ldconst r6, 0x00
		.wait_loop2:
			
				ldconst r7, 0x40
				.wait_loop3:
				add r7, r7, r1
				bzc .wait_loop3
			
				
			add r6, r6, r1
			bzc .wait_loop2
		;add r0, r0, r1
		;bzc .wait_loop
		

	add r3, r3, r1
	and r3, r3, r4
	st r3, (r2 + 0x17)

	bra .infloop
	
multiply3x4:
    ldconst r1, 0
    ldconst r2, 3
    ldconst r3, 4
    ldconst r4, 1
    
    .loop:
        add r1, r1, r2
        sub r3, r3, r4
        bzc .loop
	and r0, r1, r1
	bl r7 multiply3x4
    
    ;br r7