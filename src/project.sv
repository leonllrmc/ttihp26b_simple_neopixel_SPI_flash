/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none


module tt_um_llr_spiflashnexopixeldriver (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  // All output pins must be assigned. If not used, assign to 0.
  //assign uo_out  = ui_in + uio_in;  // Example: ou_out is the sum of ui_in and uio_in
  assign uio_oe[7:4]  = 0;

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, 1'b0};

  wire project_extflash_spi_cs;
  wire project_extflash_spi_mosi;
  wire project_extflash_spi_miso;
  wire project_extflash_spi_sck;

  assign uio_out[2:0] = {project_extflash_spi_cs, project_extflash_spi_mosi, project_extflash_spi_sck};
  assign uio_out[7:4] = 0;
  assign uio_oe[2:0] = 3'b111;
  assign uio_oe[3] = 1'b0;


  simple_npu_top flash_neopixel_top (
  .CLK(clk),
  .rst_n_ext(rst_n),
  .extflash_spi_cs(project_extflash_spi_cs),
  .extflash_spi_mosi(project_extflash_spi_mosi),
  .extflash_spi_miso(uio_in[3]),
  .extflash_spi_sck(project_extflash_spi_sck),

  .neopixel_out_pin(uio_out[3])
);
endmodule



  // NOTE: to make compatible with windbond flash = would only need to make addr 24 bit
  typedef enum logic[3:0] {
    STATE_SPI_RD,
    STATE_SPI_ADDRBANK,
    STATE_SPI_ADDR0, STATE_SPI_ADDR1, // send addr MSB, then LSB

    STATE_FETCH_HEADER1, // fetch LED count and extra wait cycle count
    STATE_FETCH_HEADER2, // fetch LED count and extra wait cycle count

    // repeats for each leds from header
    STATE_FETCH_COLOR_R,
    STATE_FETCH_COLOR_G,
    STATE_FETCH_COLOR_B,
    STATE_NEOPIXEL_TX_GUARD, // check if TX not finished since last time
    STATE_NEOPIXEL_START_TX,

    STATE_PRELATCH_AWAIT_TX_END, // start latching process and wait
    STATE_LATCH_NEOPIXELS, // start latching process and wait
    STATE_WAIT_PERIOD,

    STATE_INSTRUCTION_END // only release CS there
  } exec_state;

module flash_neopixel_top (
  input wire CLK,
  input wire rst_n_ext,
  output reg extflash_spi_cs,
  output wire extflash_spi_mosi,
  input wire extflash_spi_miso,
  output wire extflash_spi_sck,

  output neopixel_out_pin
);
wire rst_n = rst_n_ext;


  wire SPI_BUSY;
  wire SPI_DONE = ~SPI_BUSY;
  reg [7:0] SPI_DATA_MOSI;
  wire [7:0] SPI_DATA_MISO;

  reg SPI_SEND_DATA;

  reg SPI_SEND_DATA_PULSE;
  reg SPI_SEND_DATA_OLD;
  reg SPI_DONE_PULSE;
  reg SPI_DONE_OLD;

  always_ff @(posedge CLK or negedge rst_n) begin
    if(~rst_n) begin
      SPI_SEND_DATA_PULSE <= 1'b0;
      SPI_SEND_DATA_OLD <= 1'b0;
    end else begin
      SPI_SEND_DATA_OLD <= SPI_SEND_DATA;
      SPI_SEND_DATA_PULSE <= (~SPI_SEND_DATA_OLD && SPI_SEND_DATA);
     // if(~SPI_SEND_DATA_OLD && SPI_SEND_DATA) begin
     //   SPI_SEND_DATA_PULSE <= 1'b1;
     // end else if(~extflash_spi_cs) begin//SPI_DONE) begin //SPI_DONE_PULSE) begin
     //   SPI_SEND_DATA_PULSE <= 1'b0; 
     // end
    end
  end


  // pulse each 100us
  // counter = 50MHz * 100us
  // => 5000 -> 8192 (13 bit)
  reg pulse_timer_expired;
  reg [12:0] pulse_timer_counter;
  always_ff @(posedge CLK or negedge rst_n) begin
    if(~rst_n) begin
      pulse_timer_counter <= 0;
      pulse_timer_expired <= 1'b0;
    end else begin
      pulse_timer_expired <= 1'b0;
      pulse_timer_counter <= pulse_timer_counter + 1;

      if(pulse_timer_counter == 13'h000) begin
        pulse_timer_expired <= 1'b1;
      end
    end
  end


  reg [23:0] flash_addr;


  exec_state currentState;


  reg [6:0] wait_cycle_count;
  reg [7:0] led_count;
  reg [7:0] currentLed;

  reg [7:0] neopixel_color_red;
  reg [7:0] neopixel_color_green;
  reg [7:0] neopixel_color_blue;

  reg [23:0] neopixel_data_TX;
reg neopixel_TX_pulse;
reg neopixel_reset_pulse;
wire neopixel_TX_busy;

reg [6:0] wait_counter;

wire debug_ITS_pulse = (currentState == STATE_FETCH_HEADER1) || (currentState == STATE_FETCH_HEADER2) || (currentState == STATE_FETCH_COLOR_R) || (currentState == STATE_FETCH_COLOR_G) || (currentState == STATE_FETCH_COLOR_B);

  // structure: {1 start/stop bit, 7 bit wait cycle count}, {8 bit led count}

  always_ff @(posedge CLK or negedge rst_n) begin
    if(~rst_n) begin
      currentState <= STATE_SPI_RD;

      SPI_SEND_DATA <= 1'b0;

      extflash_spi_cs <= 1'b1;

    flash_addr <= 24'h000000;

    neopixel_send_data <=1'b0;
    neopixel_do_reset <= 1'b0;


    wait_cycle_count <= 0;
    led_count <= 0;
    currentLed <= 0;

    wait_counter <= 0;

    end else begin

    case (currentState)
        STATE_SPI_RD: begin
          currentLed <= 0;

          if(SPI_DONE_PULSE) begin
            currentState <= STATE_SPI_ADDRBANK;
            SPI_SEND_DATA <= 1'b0;
          end else begin
            SPI_DATA_MOSI <= 8'h03; // READ command
            SPI_SEND_DATA <= 1'b1;
            extflash_spi_cs <= 1'b0;
          end
        end

        STATE_SPI_ADDRBANK: begin
          if(SPI_DONE_PULSE) begin
            currentState <= STATE_SPI_ADDR0;
            SPI_SEND_DATA <= 1'b0;
          end else begin
            SPI_DATA_MOSI <= flash_addr[23:16]; // addr[23:16]
            SPI_SEND_DATA <= 1'b1;
          end
        end

        STATE_SPI_ADDR0: begin
          if(SPI_DONE_PULSE) begin
            currentState <= STATE_SPI_ADDR1;
            SPI_SEND_DATA <= 1'b0;
          end else begin
            SPI_DATA_MOSI <= flash_addr[15:8]; // addr[15:8] command
            SPI_SEND_DATA <= 1'b1;
          end
        end
 
        STATE_SPI_ADDR1:  begin
          if(SPI_DONE_PULSE) begin
            currentState <= STATE_FETCH_HEADER1;
            SPI_SEND_DATA <= 1'b0;
          end else begin
            SPI_DATA_MOSI <= flash_addr[7:0]; // addr[15:8] command
            SPI_SEND_DATA <= 1'b1;
          end
        end

        
        STATE_FETCH_HEADER1: begin
          if(SPI_DONE_PULSE) begin
            currentState <= STATE_FETCH_HEADER2;
            if(SPI_DATA_MISO[7]) begin
              currentState <= STATE_INSTRUCTION_END;
            end
            wait_cycle_count <= SPI_DATA_MISO[6:0];
            SPI_SEND_DATA <= 1'b0;
          end else begin
            SPI_DATA_MOSI <= 8'hFF; // dummy data for read
            SPI_SEND_DATA <= 1'b1;
          end
        end        
        
        STATE_FETCH_HEADER2: begin
          if(SPI_DONE_PULSE) begin
            currentState <= STATE_FETCH_COLOR_R;

            led_count = SPI_DATA_MISO;
            SPI_SEND_DATA <= 1'b0;

            flash_addr <= flash_addr + 2 + (3*led_count);
          end else begin
            SPI_DATA_MOSI <= 8'hFF; // dummy data for read
            SPI_SEND_DATA <= 1'b1;
          end
        end
        
        STATE_FETCH_COLOR_R: begin
          if(SPI_DONE_PULSE) begin
            currentState <= STATE_FETCH_COLOR_G;

            neopixel_color_red <= SPI_DATA_MISO;
            SPI_SEND_DATA <= 1'b0;
          end else begin
            SPI_DATA_MOSI <= 8'hFF; // dummy data for read
            SPI_SEND_DATA <= 1'b1;
          end
        end        
        
        STATE_FETCH_COLOR_G: begin
          if(SPI_DONE_PULSE) begin
            currentState <= STATE_FETCH_COLOR_B;

            neopixel_color_green <= SPI_DATA_MISO;
            SPI_SEND_DATA <= 1'b0;
          end else begin
            SPI_DATA_MOSI <= 8'hFF; // dummy data for read
            SPI_SEND_DATA <= 1'b1;
          end
        end   
        
        STATE_FETCH_COLOR_B: begin
          if(SPI_DONE_PULSE) begin
            currentState <= STATE_NEOPIXEL_TX_GUARD;

            neopixel_color_blue <= SPI_DATA_MISO;
            SPI_SEND_DATA <= 1'b0;
          end else begin
            SPI_DATA_MOSI <= 8'hFF; // dummy data for read
            SPI_SEND_DATA <= 1'b1;
          end
        end

        STATE_NEOPIXEL_TX_GUARD: begin
          if(~neopixel_TX_busy) begin
            currentState <= STATE_NEOPIXEL_START_TX;
          end
        end

        STATE_NEOPIXEL_START_TX: begin
          wait_counter <= 0;
          neopixel_TX_pulse <= 1'b1;
          neopixel_data_TX <= {<<{neopixel_color_red, neopixel_color_green, neopixel_color_blue}}

          currentLed = currentLed + 1;
          if(currentLed >= led_count) begin
            extflash_spi_cs <= 1'b1; // release SPI flash TX
            currentState <= STATE_PRELATCH_AWAIT_TX_END;
          end else begin
            currentState <= STATE_FETCH_COLOR_R;
          end
        end


        STATE_PRELATCH_AWAIT_TX_END: begin
            if(~neopixel_TX_busy) begin
              neopixel_reset_pulse <= 1'b1;
              currentState <= STATE_LATCH_NEOPIXELS;
            end
        end

      STATE_LATCH_NEOPIXELS: begin
            neopixel_reset_pulse <= 1'b0;
            if(~neopixel_TX_busy) begin
              currentState <= STATE_WAIT_PERIOD;
            end
      end

    
      STATE_WAIT_PERIOD: begin
        if(wait_counter >= wait_cycle_count) begin
              currentState <= STATE_SPI_RD;
              wait_counter <= 0;
        end else if(pulse_timer_expired) begin
          wait_counter <= wait_counter + 1;
        end
      end

 
      STATE_INSTRUCTION_END: begin
      end

        default: currentState <= STATE_SPI_RD;
    endcase
  end
  end



 ws2812b_master #() neopixel_master (
    .clk(CLK),
    .rst_n(rst_n),
    .data_in(neopixel_data_TX),
    .send(neopixel_TX_pulse),
    .reset_pulse(neopixel_reset_pulse),
    .busy(neopixel_busy),
    .dout(neopixel_out_pin)
);


  wire SPI_CLK;

   spi_master #(
    .CLK_DIV(1)   // SCLK = clk / (2*CLK_DIV)
   ) spiMaster (
     .clk(CLK),
     .rst_n(rst_n),   // async, active-low

      .cs_n(),
     .sclk(extflash_spi_sck),
     .mosi(extflash_spi_mosi),
     .miso(extflash_spi_miso),

     .send(SPI_SEND_DATA_PULSE),
     .din(SPI_DATA_MOSI),
     .dout(SPI_DATA_MISO),
    .done(SPI_DONE_PULSE),
    .busy(SPI_BUSY)
);

endmodule
