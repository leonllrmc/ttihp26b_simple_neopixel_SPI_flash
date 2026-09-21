import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Edge, Timer
from neopixel_slave import WS2812BSlave

# ==============================================================================
# 1. SPI Flash Emulator
# ==============================================================================


def unpack_packed_array(signal, num_elements=8, element_width=4, as_int=True):
    """
    Unpacks a cocotb packed array signal into a normal Python list.
    
    Args:
        signal: The cocotb DUT signal object (e.g., dut.my_packed_array)
        num_elements (int): Number of array elements (e.g., 4 for [3:0])
        element_width (int): Bit width of each element (e.g., 8 for [7:0])
        as_int (bool): If True, attempts to convert elements to integers. 
                       If False or if the element contains X/Z, returns the binary string.
                       
    Returns:
        list: A normal Python list containing the unpacked elements.
    """
    # Get the raw binary string representation (e.g., "1100101011110000...")
    # This is safe even if the array contains 'x' or 'z'
    flat_str = signal.value.binstr
    
    # Pad or handle edge cases where the simulator didn't return the full length
    expected_len = num_elements * element_width
    if len(flat_str) < expected_len:
        flat_str = flat_str.zfill(expected_len)
        
    unpacked_list = []
    
    for i in range(num_elements):
        # Slice the string from left to right (MSB chunk to LSB chunk)
        start = i * element_width
        end = start + element_width
        chunk = flat_str[start:end]
        
        if as_int:
            try:
                # Convert the binary chunk to an integer
                unpacked_list.append(int(chunk, 2))
            except ValueError:
                # Fallback to string if chunk contains 'x', 'z', 'u', etc.
                unpacked_list.append(chunk)
        else:
            unpacked_list.append(chunk)
            
    return unpacked_list

_upa = unpack_packed_array

async def spi_flash_emulator(dut, rom_data, cs_idx=0, sclk_idx=1, mosi_idx=2, miso_idx=0):
    """
    Emulates an external SPI Flash chip (Standard SPI Mode 0, Read Cmd 0x03).
    Listens to `uo_out` for master signals and drives `ui_in` with MISO.
    """
    # Helper functions to extract specific bits from TT I/O busses
    def get_cs():   return (int(dut.uio_out.value) >> cs_idx) & 1 if dut.uio_out.value.is_resolvable else 1
    def get_sclk(): return (int(dut.uio_out.value) >> sclk_idx) & 1 if dut.uio_out.value.is_resolvable else 0
    def get_mosi(): return (int(dut.uio_out.value) >> mosi_idx) & 1 if dut.uio_out.value.is_resolvable else 0
    
    def set_miso(bit):
        try:
            val = int(dut.uio_in.value)
        except ValueError:
            val = 0 # Fallback if ui_in contains 'X' or 'Z'
            
        if bit: val |= (1 << miso_idx)
        else:   val &= ~(1 << miso_idx)
        dut.uio_in.value = val

    state = "CMD"
    bit_count_rx = 0
    bit_count_tx = 0
    shift_reg_rx = 0
    shift_reg_tx = 0
    addr = 0
    last_sclk = 0

    dut._log.info("SPI Flash Emulator: Online")

    while True:
        ## Wait for any change on the output pins
        await dut.uio_out.value_change

        #print(dut.uo_out.value.is_resolvable)
        #if dut.uo_out.value.is_resolvable:
        #    print(bin(int(dut.uo_out.value)))
        
        cs = get_cs()
        
        # If Chip Select is high (inactive), reset state
        if cs == 1:
            set_miso(0)
            state = "CMD"
            bit_count_rx = 0
            bit_count_tx = 0
            shift_reg_rx = 0
            shift_reg_tx = 0
            last_sclk = get_sclk()
            continue
            
        sclk = get_sclk()
        
        # --- RISING EDGE (Master drives MOSI, Slave Samples) ---
        if sclk == 1 and last_sclk == 0:
            mosi = get_mosi()
            shift_reg_rx = ((shift_reg_rx << 1) | mosi) & 0xFFFFFFFF
            bit_count_rx += 1

            #print("bcr", bit_count_rx)
            #print(state)

            #print("SCLK rising")
            
            if state == "CMD" and bit_count_rx == 8:
                cmd = shift_reg_rx & 0xFF
                #print("Got cmd", hex(cmd))
                if cmd == 0x03:
                    dut._log.debug("SPI: Standard Read (0x03) received")
                state = "ADDR"
                bit_count_rx = 0
                shift_reg_rx = 0
                
            elif state == "ADDR" and bit_count_rx == 24:
                addr = shift_reg_rx & 0xFFFFFF
                #dut._log.debug(f"SPI: Fetching from address 0x{addr:06X}")
                #print(f"SPI: Fetching from address 0x{addr:06X}")
                state = "DATA"
                bit_count_tx = 0
                shift_reg_rx = 0
                
        # --- FALLING EDGE (Slave drives MISO, Master Samples next cycle) ---
        elif sclk == 0 and last_sclk == 1:
            #print("SCLK falling")
            #print("bct", bit_count_tx)
            #print(state)


            if state == "DATA":
                if bit_count_tx % 8 == 0:
                    # Fetch next byte from ROM data
                    if addr < len(rom_data["content"]):
                        #print(f"Loading from addr 0x{addr:06X}")
                        shift_reg_tx = rom_data["content"][addr]
                        #print(f"instruction byte: 0x{shift_reg_tx:02X}")
                    else:
                        shift_reg_tx = 0x00 # Out of bounds returns zero
                    addr += 1
                    
                # Shift out MSB first
                #miso_bit = (shift_reg >> (7 - (bit_count_tx % 8))) & 1
                miso_bit = (shift_reg_tx & 0x80) >> 7
                #print("miso bit", miso_bit)
                #print("0b" + bin(shift_reg_tx)[2:].rjust(8, '0'))
                shift_reg_tx = (shift_reg_tx << 1) & 0xFF
                set_miso(miso_bit)

            bit_count_tx += 1

        last_sclk = sclk


async def reset_design(dut):
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    
    for _ in range(5):
        await RisingEdge(dut.clk)
        
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    dut._log.info("Reset complete.")

# ==============================================================================
# 3. Main Testbench
# ==============================================================================
async def set_input_data(dut, datas):
    for i in range(8):
        # VAAADDDD
        # V = validate pulse, A = addr, D = data
        dut.ui_in.value = 0b00000000
        
        for _ in range(2):
            await RisingEdge(dut.clk)

        dut.ui_in.value = 0x80 | (i << 4) | (datas[i] & 0x0F)
        for _ in range(2):
            await RisingEdge(dut.clk)

    
    dut.ui_in.value = 0b00000000

async def set_output_data(dut):
    datas = [0] * 8
    for i in range(8):
        # VAAADDDD
        # V = validate pulse, A = addr, D = data
        dut.ui_in.value = (i << 4)
        
        await RisingEdge(dut.clk)

        datas[i] = dut.uo_out.value & 0x0F

    
    dut.ui_in.value = 0b00000000

    return datas

import ctypes

import ctypes

# Method 1: Using ctypes bitfields (Total = 8 bits / 1 byte)
class InstructionHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("is_end", ctypes.c_ubyte, 1),       # 1 bit
        ("act_function", ctypes.c_ubyte, 3), # 3 bits
        ("bais", ctypes.c_ubyte, 4)          # 4 bits
    ]

    def __init__(self, is_end, act_function, bais):
        super().__init__()
        self.is_end = is_end
        self.act_function = act_function
        self.bais = bais

def to_instruction(is_end, act_function, bais):
    return (is_end << 7) | (act_function << 4) | bais

def to_signed_4bit(val) -> int:
    val = int(val)
    val &= 0x0F  # Ensure the value is masked to 4 bits
    return val - 16 if (val & 0x08) else val

@cocotb.test()
async def single_neopixel_data(dut):
    return None
    # 1. Start the CPU clock (e.g., 50 MHz)
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    rom_bytes = {"content": 0}

    # 2. Define the program (32-bit RISC-V machine code)
    # This will be converted to bytes and served by the SPI emulator.
    dummy_program_words = []
    dummy_program_words.append(0x00) # no wait
    dummy_program_words.append(0x01) # 1 led
    dummy_program_words.append(0x17)
    dummy_program_words.append(0x38)
    dummy_program_words.append(0x42)

    rom_bytes["content"] = dummy_program_words

    neopixel_slave = WS2812BSlave(dut.neopixel_din, num_leds=1)

    # 3. Start the SPI Flash Emulator concurrently
    # ---> UPDATE THESE INDICES to match your project's info.yaml pin mapping <---
    cocotb.start_soon(spi_flash_emulator(
        dut, 
        rom_data = rom_bytes,
        cs_idx   = 2,  # e.g., uo_out[0]
        sclk_idx = 0,  # e.g., uo_out[1]
        mosi_idx = 1,  # e.g., uo_out[2]
        miso_idx = 3   # e.g., ui_in[0]
    ))


    print("Starting bais only test")
    await reset_design(dut)
    
    max_cycles = 10  # Give it enough cycles to perform SPI transactions
    current_cycle = 0
    while 1:
        await RisingEdge(dut.clk)

        if dut.user_project.flash_neopixel_top.debug_ITS_pulse.value:
            current_cycle += 1
            if True:
                dut._log.info(f"led count: {dut.user_project.flash_neopixel_top.led_count.value} wait time: {dut.user_project.flash_neopixel_top.wait_cycle_count.value}")
                dut._log.info(f"R: {dut.user_project.flash_neopixel_top.led_count.value}")
                w_log_str = ""
                for i in range(8):
                    w_log_str += f"W {i} : {to_signed_4bit(_upa(dut.user_project.simpleNPUTop.weights)[i])} | "
                dut._log.info(w_log_str)
                a_log_str = ""
                for i in range(8):
                    a_log_str += f"A {i} : {to_signed_4bit(_upa(dut.user_project.simpleNPUTop.activations_out_memory)[i])} | "
                dut._log.info(a_log_str)
                a2_log_str = ""
                for i in range(8):
                    a2_log_str += f"AT {i} : {to_signed_4bit(_upa(dut.user_project.simpleNPUTop.activations_tmp)[i])} | "
                dut._log.info(a2_log_str)

        if current_cycle >= max_cycles:
            break
        
        if dut.user_project.simpleNPUTop.debug_DONE.value:
            break
            

    for i in range(8):
        assert(int(_upa(dut.user_project.simpleNPUTop.activations_out_memory)[7-i]) == (i+1))  

    dut._log.info("simple bais test finished")

