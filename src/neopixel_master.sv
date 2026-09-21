//==============================================================================
// ws2812b_master.sv
//
// WS2812B ("NeoPixel") serial LED master / driver.
//
// Interface
// ---------
//   clk          : 50 MHz nominal system clock (see Timing below if you're
//                  on a different frequency).
//   rst_n        : async active-low system reset for the module's logic.
//   data_in      : color word, shifted out MSB-first. Default width is 24
//                  bits; WS2812B expects G[7:0],R[7:0],B[7:0] in that bit
//                  order (i.e. drive data_in = {green, red, blue}).
//   send         : pulse (1 clk) while idle to transmit data_in.
//   reset_pulse  : pulse (1 clk) while idle to hold DOUT low for
//                  RESET_CYCLES clocks, which is how WS2812B strings latch
//                  whatever's been shifted into them.
//   busy         : high while a color word is being shifted out, or while
//                  the reset/latch pulse is being generated. data_in/send
//                  are ignored (not merely "should be" -- the FSM will not
//                  act on them) any time busy is high.
//   dout         : the WS2812B serial data line.
//
// Usage
// -----
//   - To shift a whole strip: pulse `send` once per pixel, waiting for
//     `busy` to fall between pixels, then pulse `reset_pulse` once at the
//     end to latch the whole strip.
//   - `send` / `reset_pulse` are edge-detected, so it's safe to hold them
//     high for more than one cycle -- they won't re-trigger.
//   - If both `send` and `reset_pulse` land on the same idle cycle,
//     reset_pulse wins.
//
// Timing
// ------
// WS2812B bit timing (typical, within datasheet tolerance of ~+-150ns):
//     symbol   target      cycles @ 50MHz (20ns/cyc)
//     T0H      400ns       20
//     T0L      880ns       44
//     T1H      800ns       40
//     T1L      480ns       24
//     RESET    >=300us     15000   (300us margin covers clones that want
//                                   more than the original 50us spec)
// T0H+T0L and T1H+T1L are both held at a fixed 64-cycle (1280ns) bit
// period -- only where the falling edge lands inside that period changes
// between a '0' and a '1'. That keeps the FSM a single counter compare
// instead of two independently-timed edges.
//
// If your clock isn't 50MHz, recompute the *_CYCLES parameters as
// round(time_ns * clk_freq_hz / 1e9) and override them at instantiation.
//==============================================================================

module ws2812b_master #(
    parameter int BIT_PERIOD_CYCLES = 64,    // total cycles per bit, 0 or 1
    parameter int T0H_CYCLES        = 20,    // high time for a '0' bit
    parameter int T1H_CYCLES        = 40,    // high time for a '1' bit
    parameter int RESET_CYCLES      = 15000, // low time for latch/reset
    parameter int DATA_WIDTH        = 24     // bits per LED (24 = GRB)
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic [DATA_WIDTH-1:0] data_in,
    input  logic                  send,
    input  logic                  reset_pulse,
    output logic                  busy,
    output logic                  dout
);

  localparam int CYC_CNT_MAX = (RESET_CYCLES > BIT_PERIOD_CYCLES) ? RESET_CYCLES : BIT_PERIOD_CYCLES;
  localparam int CYC_W       = $clog2(CYC_CNT_MAX);
  localparam int BIT_CNT_W   = $clog2(DATA_WIDTH);

  typedef enum logic [1:0] {
    S_IDLE,
    S_SEND,
    S_RESET
  } state_t;

  state_t                state;
  logic [DATA_WIDTH-1:0] shift_reg;
  logic [BIT_CNT_W-1:0]  bit_cnt;
  logic [CYC_W-1:0]      cyc_cnt;

  // Edge-detect send/reset_pulse so holding either high doesn't
  // re-trigger once the FSM returns to idle.
  logic send_d, reset_pulse_d;
  wire  send_edge  = send        & ~send_d;
  wire  reset_edge = reset_pulse & ~reset_pulse_d;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      send_d        <= 1'b0;
      reset_pulse_d <= 1'b0;
    end else begin
      send_d        <= send;
      reset_pulse_d <= reset_pulse;
    end
  end

  wire               cur_bit     = shift_reg[DATA_WIDTH-1];
  wire [CYC_W-1:0]    high_thresh = cur_bit ? CYC_W'(T1H_CYCLES) : CYC_W'(T0H_CYCLES);

  // -------------------------------------------------------------------
  // FSM state / counters
  // -------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state     <= S_IDLE;
      shift_reg <= '0;
      bit_cnt   <= '0;
      cyc_cnt   <= '0;
    end else begin
      case (state)
        S_IDLE: begin
          cyc_cnt <= '0;
          if (reset_edge) begin
            state <= S_RESET;
          end else if (send_edge) begin
            shift_reg <= data_in;
            bit_cnt   <= BIT_CNT_W'(DATA_WIDTH - 1);
            state     <= S_SEND;
          end
        end

        S_SEND: begin
          if (cyc_cnt == CYC_W'(BIT_PERIOD_CYCLES - 1)) begin
            cyc_cnt <= '0;
            if (bit_cnt == 0) begin
              state <= S_IDLE;
            end else begin
              bit_cnt   <= bit_cnt - 1'b1;
              shift_reg <= {shift_reg[DATA_WIDTH-2:0], 1'b0};
            end
          end else begin
            cyc_cnt <= cyc_cnt + 1'b1;
          end
        end

        S_RESET: begin
          if (cyc_cnt == CYC_W'(RESET_CYCLES - 1)) begin
            cyc_cnt <= '0;
            state   <= S_IDLE;
          end else begin
            cyc_cnt <= cyc_cnt + 1'b1;
          end
        end

        default: state <= S_IDLE;
      endcase
    end
  end

  // -------------------------------------------------------------------
  // Outputs -- combinational on current state, so dout/busy react the
  // same cycle the FSM enters SEND/RESET, with no extra latency.
  // -------------------------------------------------------------------
  assign busy = (state != S_IDLE);

  always_comb begin
    dout = 1'b0;
    if (state == S_SEND) dout = (cyc_cnt < high_thresh);
  end

endmodule