// =============================================================================
// made with AI because I was too lazy :(
// spi_master.v
//
// Parameterized SPI Master (Mode 0: CPOL=0, CPHA=0)
//   - SCLK idles low, data changes on falling edge, sampled on rising edge
//   - MSB-first, full-duplex
//   - SCLK frequency = clk / (2*CLK_DIV)
//
// Handshake:
//   - Pulse `send` for 1 cycle with `din` valid to start an 8-bit (WIDTH-bit)
//     transfer. `busy` goes high immediately.
//   - `done` pulses for exactly 1 cycle, coincident with the final SCLK rising
//     edge (the edge that samples the last MISO bit). `busy` deasserts on the
//     same cycle. `dout` is valid on that same cycle.
//
// Edge-case choices (spec explicitly leaves these open):
//   1) send while busy (mid-frame): IGNORED. din is not re-sampled and the
//      in-flight transfer is unaffected.
//   2) Back-to-back transfers: SCLK does NOT need to return to idle between
//      bytes. After the final rising edge, `busy` drops but the module is
//      still finishing the trailing falling edge that returns SCLK to 0. If a
//      new `send` arrives at any point during that trailing half-period
//      (i.e. "immediately after done"), the new byte is latched and clocking
//      continues seamlessly on that same falling edge with no idle gap -
//      matching how a real SPI master free-runs SCLK across back-to-back
//      bytes when chip-select stays asserted. If no new `send` arrives in
//      that window, SCLK settles low and the module returns to idle.
// =============================================================================

module spi_master #(
    parameter integer CLK_DIV = 4,   // SCLK = clk / (2*CLK_DIV)
    parameter integer WIDTH   = 8    // shift register width
) (
    input  wire             clk,
    input  wire             rst_n,   // async, active-low

    output reg              sclk = 1'b0,
    output wire             mosi,
    input  wire             miso,
    output wire             cs_n,    // active-low chip select; low for the whole

    input  wire             send,    // duration of a frame (or a back-to-back run)
    input  wire [WIDTH-1:0] din,
    output reg  [WIDTH-1:0] dout = {WIDTH{1'b0}},
    output reg              done = 1'b0,
    output reg              busy = 1'b0
);

    // cs_n tracks xfer_active (declared below): asserted (low) from `send`
    // until the trailing falling edge settles with no follow-on transfer.
    // It stays low continuously across back-to-back/pipelined frames, and
    // only rises once the bus actually goes idle.
    assign cs_n = ~xfer_active;

    // ---------------------------------------------------------------------
    // Internal state
    // ---------------------------------------------------------------------
    localparam integer DIVCNT_W = (CLK_DIV <= 1) ? 1 : $clog2(CLK_DIV);
    localparam integer BITCNT_W = $clog2(WIDTH + 1);

    reg [DIVCNT_W-1:0] div_cnt;      // half-SCLK-period tick counter
    reg [BITCNT_W-1:0] bit_cnt;      // rising edges completed this frame
    reg [WIDTH-1:0]    shift_out;    // TX shift register, MSB-first
    reg [WIDTH-1:0]    shift_in;     // RX shift register, MSB-first

    reg                xfer_active;  // high from `send` until trailing fall completes
    reg                pend_valid;   // a follow-on `send` was latched in the tail window
    reg [WIDTH-1:0]    pend_data;

    assign mosi = shift_out[WIDTH-1];

    // ---------------------------------------------------------------------
    // Main sequential logic
    // ---------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sclk        <= 1'b0;
            dout        <= {WIDTH{1'b0}};
            done        <= 1'b0;
            busy        <= 1'b0;
            div_cnt     <= {DIVCNT_W{1'b0}};
            bit_cnt     <= {BITCNT_W{1'b0}};
            shift_out   <= {WIDTH{1'b0}};
            shift_in    <= {WIDTH{1'b0}};
            xfer_active <= 1'b0;
            pend_valid  <= 1'b0;
            pend_data   <= {WIDTH{1'b0}};
        end else begin
            done <= 1'b0; // default: only pulses explicitly below

            if (!xfer_active) begin
                // ------------------------------------------------ IDLE ---
                if (send) begin
                    shift_out   <= din;      // MSB (din[WIDTH-1]) drives mosi now
                    busy        <= 1'b1;
                    xfer_active <= 1'b1;
                    bit_cnt     <= {BITCNT_W{1'b0}};
                    div_cnt     <= {DIVCNT_W{1'b0}};
                    pend_valid  <= 1'b0;
                    // sclk already 0 (idle)
                end
            end else begin
                // --------------------------------------------- ACTIVE ---
                // Latch a follow-on send if it shows up after busy has
                // already dropped (i.e. during the trailing tail window).
                if (send && !busy) begin
                    pend_valid <= 1'b1;
                    pend_data  <= din;
                end

                if (div_cnt == CLK_DIV - 1) begin
                    div_cnt <= {DIVCNT_W{1'b0}};

                    if (sclk == 1'b0) begin
                        // ----------------------- Rising edge: sample MISO
                        sclk     <= 1'b1;
                        shift_in <= {shift_in[WIDTH-2:0], miso};

                        if (bit_cnt == WIDTH - 1) begin
                            // Final bit sampled - transfer complete
                            dout <= {shift_in[WIDTH-2:0], miso};
                            done <= 1'b1;
                            busy <= 1'b0;
                        end else begin
                            bit_cnt <= bit_cnt + 1'b1;
                        end

                    end else begin
                        // ---------------------------------- Falling edge
                        sclk <= 1'b0;

                        if (busy) begin
                            // Mid-frame: shift out the next bit
                            shift_out <= {shift_out[WIDTH-2:0], 1'b0};
                        end else begin
                            // Trailing falling edge (frame already "done")
                            if (pend_valid || send) begin
                                // Seamless back-to-back: no idle gap
                                shift_out  <= pend_valid ? pend_data : din;
                                busy       <= 1'b1;
                                bit_cnt    <= {BITCNT_W{1'b0}};
                                pend_valid <= 1'b0;
                                // xfer_active stays high, div_cnt already cleared above
                            end else begin
                                xfer_active <= 1'b0;
                                shift_out   <= {WIDTH{1'b0}};
                            end
                        end
                    end
                end else begin
                    div_cnt <= div_cnt + 1'b1;
                end
            end
        end
    end

endmodule
