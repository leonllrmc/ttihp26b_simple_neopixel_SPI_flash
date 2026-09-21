# simple_NPU functional validation

Independent cocotb tests for the signed MAC, activation functions, SPI engine,
and complete flash-streaming NPU. Each full-chip inference is checked at the
MAC latch, neuron output, layer commit and package output pins. Failures are
ordinary assertions: there are no expected-failure markers hiding design defects.

The baseline is commit `ad804f87f964325e06132a3c2f75ca132b8e9706`.
See [BASELINE.md](BASELINE.md) for measured results and reproduction of defects,
[TESTS.md](TESTS.md) for every test's description, and [TRAINING.md](TRAINING.md)
for the optional discrete training/export tool. The existing `test/test.py`
remains a separate upstream testbench.

## Install and run

Requirements: Python 3.11–3.13, make, a C++ compiler, and Verilator **5.036+**.
The local baseline uses Python 3.12.13, cocotb 2.0.1 and Verilator 5.052.
The minimum simulator version follows
[cocotb 2.0.1's simulator requirements](https://docs.cocotb.org/en/v2.0.1/simulator_support.html#verilator).

From the repository root:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r test/requirements.txt
cd test/validation
python -m unittest -v test_tools_host
python run.py --suite all --trace steps --protocol
```

Suites are `mac`, `activation`, `spi` and `soc`. The runner continues after a
failed suite, writes `results/summary.json`, and returns nonzero for test failures,
build failures or an empty test selection. Each suite has `sim.log`, JUnit
`results.xml`, and one JSONL trace per test. The summary includes the RTL commit,
tool versions, seed, options, source/fixture hashes and each test outcome.

```sh
python run.py --suite mac --test 'mac_all_signed_pairs_each_lane$' --trace steps --output results-mac
python run.py --suite soc --test 'soc_trained_xor_flash_export$' --trace steps --protocol --output results-xor
python run.py --suite spi --spi-div 4 --protocol --output results-spi4
python run.py --suite soc --test 'soc_seeded_full_networks$' --seed 0x1234 --stress --trace steps
```

`--test` is a regular expression over the full cocotb name, such as
`test_soc.soc_trained_xor_flash_export`. The runner safely transports regexes
through make. Unselected tests appear as skipped in filtered-run XML; they are
not reported as passes. Use different output folders when preserving multiple
runs. Default seeds are fixed; `--stress` increases random MAC vectors from
2,000 to 20,000 and random full networks from 8 to 40.

## Detailed execution and protocol events

Every test supports `--trace quiet`, `--trace steps`, or `--trace cycles`.

- `quiet`: metadata, summary and a last-100-events context buffer on failure.
- `steps`: vectors/products, input writes, neuron computations, layer commits,
  expected/observed values and scenario checks.
- `cycles`: all events plus clock-cycle records; full-chip records include state,
  instruction, weights, activations, save detector, neuron index and outputs.

`--protocol` adds SPI CS transitions, command/address/data bytes and sampled
bits. Full-chip flash responses depend on **package pins only**. Internal state
is observed by the arithmetic scoreboard, never used to decide which flash bit
to send. Clock periods and high widths are checked against the configured clock.

Read traces without a waveform viewer:

```sh
python trace_view.py results-xor/soc/soc_trained_xor_flash_export.jsonl
python trace_view.py results-xor/soc/soc_trained_xor_flash_export.jsonl --kind mac
python trace_view.py results-xor/soc/soc_trained_xor_flash_export.jsonl --kind spi --tail 30
python run.py --suite soc --test 'soc_reset_pending_save_no_phantom_inference$' --trace cycles --protocol --output results-reset
```

The reader labels the model's expected products separately from observed product
bytes. For repeated scenarios, traces contain all steps; the `.flash.bin` next
to a trace holds the most recently created image in that test. Seeds, test source
and trace data reproduce earlier scenarios. Each test has an outer simulator
timeout, and all transfer/inference waits have explicit clock limits.

## Architecture and arithmetic contract

The design consumes eight inputs and evaluates layers of eight neurons. Values,
weights and biases are four-bit two's-complement codes. Neuron arithmetic is:

```text
p[i]  = signed4(input[i]) * signed4(weight[i])
q[i]  = floor(p[i] / 4)                     # separately for each lane
pre   = (sum(q[0..7]) + signed4(bias)) mod 16
out   = activation(pre, function)
```

This preserves the implemented truncation and wrap behavior; it does not invent
saturation or postpone division until after summation. There are no assumptions
about an external real-number scale beyond this explicit integer contract.
Raw output nibbles are interpreted as signed again when used by a later layer.

| Code | Activation | Contract |
|---|---|---|
| 0 | Identity | Preserve input nibble. |
| 1 | ReLU | Negative signed input → 0. |
| 2 | Step | Negative → 0; zero and positive → 1. |
| 3 | Absolute | Absolute magnitude modulo 16; −8 becomes code 8. |
| 4 | Negation | Negate modulo 16; −8 remains code 8. |
| 5 | Hard sigmoid | `floor(signed_x/2)+8`, output codes 4..11. |
| 6 | Gain-2 tanh | `2*x` clipped to signed −8..7. |
| 7 | Sigmoid LUT | Exact 16-entry table implemented by the design, output codes 2..14. |

The function tests verify these integer formulas/LUT entries, not approximation
accuracy against a floating-point sigmoid. In particular, sigmoid codes above 7
become negative at the next signed MAC input. That interpretation is explicitly
characterized, and should be considered when choosing networks.

### Input handshake confirmed for this validation

The default `--input-contract rtl` documents the current protocol:

1. Present `ui_in[6:4] = address`, `ui_in[3:0] = nibble`, and assert save on bit 7.
2. Keep address/data valid through **two rising core-clock edges**. The first
   edge detects the save transition; the next consumes it using live data/address.
3. Lower save before another event. The supplied driver uses two low clocks.
4. Writing address 7 launches inference. Write the other seven locations first;
   there is no hardware full-vector validity mask.
5. After END, one dedicated save pulse returns to input mode **without storing
   its payload**. Then provide the new eight input writes, address 7 last.

A held save is one event, not a write on every clock. The testbench exercises
variable valid pulse lengths, overwrites, address order and busy-stage strobes.
As agreed, the stricter first-edge/first-next-write alternative is isolated:

```sh
python run.py --suite soc --test 'soc_input_capture_contract$|soc_first_write_after_end_contract$' --input-contract edge --trace cycles --output results-edge
```

Failures in that alternative run are protocol deviations from the alternative
contract, not additional defects under the accepted default.

### Flash format and outputs

A single mode-0, MSB-first transaction sends `03 00 00 00`, then clocks the whole
network sequentially with FF dummy bytes. Core clock is 50 MHz; full-chip SCLK
is 25 MHz. One neuron occupies five bytes:

```text
byte 0: 0 fff bbbb     function and bias
byte 1: w0 w1         high nibble, low nibble
byte 2: w2 w3
byte 3: w4 w5
byte 4: w6 w7
```

Eight records form a layer. A byte with bit 7 set ends execution without fetching
weights; the exporter emits `80`. Exported images contain complete nonempty
layers. Tests also characterize END before a layer or after a partial layer.
Completed layers update all eight stored activations together; partial results
are separately visible through the temporary-output nibble.

`ui_in[6:4]` selects the output address. `uo_out[3:0]` is the committed activation,
`uo_out[7:4]` the temporary activation. `uio[0:2]` are SCLK/MOSI/CS outputs,
`uio[3]` is MISO input; the other bidirectional pins must remain inputs.
There is no dedicated completion output; the tests observe END internally and
also verify flash deselection and stable package outputs.

## Implementation and limits

`model.py` contains the independent arithmetic and validated network encoder;
`benches.sv` adds observation wires only. No architectural state is forced.
`test_mac.py` uses exhaustive signed pairs per lane and separate bias/sum tests;
`test_activation.py` covers every function/input pair; `test_spi.py` scores pins;
`test_soc.py` checks the combined path. Ten host tests anchor arithmetic, byte
order, file validation and training reproducibility.

The original RTL is unchanged. Verilator is the supported full-suite baseline.
`--sim icarus` allows secondary checks; the MAC/activation results are reproduced
with Icarus 13.0. The GitHub workflow runs suites independently, repeats SPI at
divider 4 and uploads evidence even on failure; hosted execution is not claimed.

These are functional RTL tests, not DRC/LVS, SDF/static-timing, CDC/metastability,
pad electrical or real-flash timing signoff. Pin inputs are driven synchronously
with valid digital setup time. Verilator's default initialization is not evidence
of safe silicon power-up. Coverage names exercised scenarios, not exhaustive
CPU/NPU state-space or all-interleaving proof.
