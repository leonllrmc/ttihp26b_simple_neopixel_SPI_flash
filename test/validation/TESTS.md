# Test descriptions

45 cocotb tests in the default run. Every test supports `--trace quiet|steps|cycles`; SPI protocol events are enabled with `--protocol`.

Statuses below describe RTL commit `ad804f87f964325e06132a3c2f75ca132b8e9706`, with the accepted `rtl` input contract. See [BASELINE.md](BASELINE.md) for defect grouping; tests remain ordinary assertions.

## mac

| Test | Description | Baseline |
|---|---|---|
| `mac_all_signed_pairs_each_lane` | All 16×16 signed operand pairs on each of eight independently selected lanes. | FAIL |
| `mac_nonnegative_pairs_and_lane_isolation` | Positive-domain baseline and zero-weight lanes isolate routing from signedness. | PASS |
| `mac_every_sum_and_bias_wrap` | Every four-bit accumulated sum × every signed bias; overflow wraps, not saturates. | PASS |
| `mac_quantizes_each_product_before_sum` | Discard each product's low two bits before summing; expose sum-then-shift mistakes. | PASS |
| `mac_negative_fraction_rounds_down` | Negative products -1,-2,-3 must contribute -1, with arithmetic floor division. | FAIL |
| `mac_eight_lane_extrema_and_cancellation` | Wide products, wrap, bias extremes and cancellation across all lanes. | FAIL |
| `mac_seeded_mixed_vectors` | Deterministic random eight-lane products, sum and bias against signed integer math. | FAIL |

## activation

| Test | Description | Baseline |
|---|---|---|
| `activation_identity` | All sixteen four-bit inputs for identity. | PASS |
| `activation_relu` | All sixteen four-bit inputs for relu. | PASS |
| `activation_step` | All sixteen four-bit inputs for step. | PASS |
| `activation_abs` | All sixteen four-bit inputs for abs. | PASS |
| `activation_negate` | All sixteen four-bit inputs for negate. | PASS |
| `activation_hard_sigmoid` | All sixteen four-bit inputs for hard_sigmoid. | PASS |
| `activation_tanh2` | All sixteen four-bit inputs for tanh2. | PASS |
| `activation_sigmoid_lut` | All sixteen four-bit inputs for sigmoid_lut. | PASS |
| `activation_unsigned_outputs_reenter_signed_domain` | Characterize sigmoid nibble outputs reinterpreted as signed inputs in a next layer. | PASS |

## spi

| Test | Description | Baseline |
|---|---|---|
| `spi_all_bytes_full_duplex` | All TX bytes and a permutation of all RX bytes; bit order, clock, CS and done. | PASS |
| `spi_seeded_data_and_idle_gaps` | Independently randomized TX/RX values and variable gaps between transactions. | PASS |
| `spi_send_during_busy_is_ignored` | Busy commands and changing din cannot corrupt the active frame. | PASS |
| `spi_tail_window_pipeline` | Three queued bytes share CS; acceptance includes the trailing falling-edge window. | PASS |
| `spi_reset_at_every_transfer_phase` | Reset at each clock position, verify idle levels and a fresh successful exchange. | PASS |

## soc

| Test | Description | Baseline |
|---|---|---|
| `soc_input_all_nibbles_and_addresses` | All sixteen patterns at each of eight input addresses; address 7 triggers inference. | PASS |
| `soc_input_no_strobe_no_write` | Changing input data/address without save does not write RAM or start SPI. | PASS |
| `soc_input_overwrite_and_shuffled_order` | Random address order, repeated writes and varying stable pulse lengths; address 7 last. | PASS |
| `soc_input_held_save_is_one_event` | A held-high save captures once; later address changes while still high are not writes. | PASS |
| `soc_input_capture_contract` | Characterize delayed live-data capture, or require first-edge capture with --input-contract edge. | PASS |
| `soc_address_seven_starts_with_missing_inputs` | Current protocol has no full-vector validity mask: writing slot 7 starts immediately. | PASS |
| `soc_bias_activation_instruction_matrix` | All 128 non-END instruction headers: every bias with every activation function. | PASS |
| `soc_weight_lane_and_nibble_order` | Walk each input lane; varied signed weight nibbles expose serialization and routing mistakes. | PASS |
| `soc_signed_input_weight_and_cancellation` | Negative inputs, negative weights and cancellation through actual flash/MAC/output stages. | FAIL |
| `soc_multilayer_positive_relu` | Three dense layers and atomic double-buffering in the domain unaffected by signed multiplication. | PASS |
| `soc_mixed_activation_multilayer_signed` | A sigmoid layer feeds signed multiplication; high output codes must be reinterpreted correctly. | FAIL |
| `soc_long_stream_crosses_byte_address_boundary` | Nine layers cross flash byte 255 without restarting the READ command or CS frame. | PASS |
| `soc_every_end_header` | Every instruction with bit 7 set terminates without fetching weights, even before a layer. | PASS |
| `soc_partial_layer_end_characterization` | END after one to seven neurons exposes partial temporary outputs but does not commit a layer. | PASS |
| `soc_output_mux_and_stability_after_end` | All output addresses, both nibbles, stable stored results and no SPI clocks after END. | PASS |
| `soc_rearm_then_multiple_inferences` | Explicit rearm followed by complete vectors restarts flash at zero without resetting the chip. | PASS |
| `soc_first_write_after_end_contract` | Characterize rearm-only first strobe, or require it to store the next input with --input-contract edge. | PASS |
| `soc_save_during_each_busy_stage` | Input strobes in READ/address/instruction/weight/MAC/activation stages cannot overwrite the live layer. | PASS |
| `soc_reset_clears_pending_save` | A pending save must not survive reset and write/start inference without a new event. | FAIL |
| `soc_reset_pending_save_no_phantom_inference` | Pin-visible regression: reset must prevent a stale slot-7 save from launching a flash READ. | FAIL |
| `soc_reset_each_state_and_recovery` | Reset in every control state; clear RAM/output/busy, abort serial frame and run a new inference. | PASS |
| `soc_seeded_full_networks` | Random one-to-four-layer signed networks; collect failures per trial rather than stopping at the first vector. | FAIL |
| `soc_trained_xor_flash_export` | Execute the trainer's exported XOR model through real flash pins and check all four labelled cases. | PASS |
| `soc_train_two_layers_and_execute` | Train an eight-wide two-layer model, serialize it, then verify both examples on the RTL. | PASS |

## Host-side checks

`python -m unittest -v test_tools_host` runs ten additional tool/reference checks, separate from the 45 HDL tests.
