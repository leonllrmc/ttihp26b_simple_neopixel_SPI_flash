`timescale 1ns/1ps
`ifndef NPU_SPI_DIV
`define NPU_SPI_DIV 1
`endif
module tb_mac;
  reg [31:0] inputs, weights;
  reg [3:0] bias;
  wire [3:0] result;
  weight_compute dut(inputs,weights,bias,result);
  wire [63:0] products;
  wire [3:0] sum = dut.out_sum;
  genvar i;
  generate for(i=0;i<8;i=i+1) begin
    assign products[8*i+:8] = dut.mul_result[i];
  end endgenerate
endmodule
module tb_activation;
  reg [3:0] x;
  reg [2:0] function_id;
  wire [3:0] result = ComputeActivation(x,function_id);
endmodule
module tb_spi;
  reg clk,rst_n,send,miso;
  reg [7:0] din;
  wire [7:0] dout;
  wire sclk,mosi,cs_n,busy,done;
  spi_master #(.CLK_DIV(`NPU_SPI_DIV)) dut(clk,rst_n,sclk,mosi,miso,cs_n,send,din,dout,done,busy);
endmodule
module tb_soc;
  reg clk,rst_n,ena;
  reg [7:0] ui_in,uio_in;
  wire [7:0] uo_out,uio_out,uio_oe;
  tt_um_llr_simplenpu dut(ui_in,uo_out,uio_in,uio_out,uio_oe,ena,clk,rst_n);
  wire [3:0] state = dut.simpleNPUTop.currentState;
  wire [31:0] activations = dut.simpleNPUTop.activations_out_memory;
  wire [31:0] temporary = dut.simpleNPUTop.activations_tmp;
  wire [31:0] weights = dut.simpleNPUTop.weights;
  wire [7:0] instruction = dut.simpleNPUTop.instruction;
  wire [3:0] mac_result = dut.simpleNPUTop.act_calc_out;
  wire [3:0] preactivation = dut.simpleNPUTop.tmp_activation;
  wire [2:0] neuron_index = dut.simpleNPUTop.calc_current_neuron;
  wire [1:0] weight_index = dut.simpleNPUTop.weight_fetch_index;
  wire save_pulse = dut.simpleNPUTop.data_save_pulse;
  wire save_old = dut.simpleNPUTop.data_save_old;
  wire spi_busy = dut.simpleNPUTop.SPI_BUSY;
  wire spi_done = dut.simpleNPUTop.SPI_DONE_PULSE;
endmodule
