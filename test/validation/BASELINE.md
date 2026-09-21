# simple_NPU validation baseline — 20 September 2026

## Verdict

**The signed arithmetic and reset behavior require correction before functional
approval.** The new default regression executes **45 tests: 36 pass, 9 fail,
0 skipped**, with all four Verilator builds successful. The nine failing tests
map to **two confirmed RTL defects**, not nine independent problems.

RTL revision:
[`ad804f87f964325e06132a3c2f75ca132b8e9706`](https://github.com/leonllrmc/ttihp26b_simple_NPU/commit/ad804f87f964325e06132a3c2f75ca132b8e9706).
No RTL changes are included in this work. Tests, model, trainer, documentation
and CI configuration are additions on `codex/simple-npu-validation`.

| Suite | Pass | Fail | Result |
|---|---:|---:|---|
| MAC | 3 | 4 | Negative operands produce incorrect products and quantized sums. |
| Activation | 9 | 0 | All function/input combinations and subsequent signed interpretation checked. |
| SPI | 5 | 0 | Byte data, bit order, clock, completion, busy, pipeline and reset checks pass. |
| Full chip | 19 | 5 | Signed arithmetic errors propagate; a pending input save survives reset. |
| **Total** | **36** | **9** | **Two underlying confirmed defects.** |

Configuration: Python 3.12.13, cocotb 2.0.1, Verilator 5.052, core clock 50 MHz,
SPI divider 1, seed `260920`, accepted input contract `rtl`, trace level `steps`,
protocol logging enabled. `results/summary.json` contains source/fixture hashes,
configuration and each test result. All failures are visible assertions; none
are suppressed with expected-failure annotations.

## F01 — High: multiplication evaluates selected nibbles as unsigned

Location: [`src/neuron.sv`](../../src/neuron.sv), line 14.

The outer packed arrays are declared signed, but the selected nibble operands
in `prev_activation[j] * weight[j]` are evaluated as unsigned by both tested
simulators. The independent model interprets each four-bit operand as signed
−8..7 before multiplying.

The exhaustive test exercises all 16×16 operand pairs on each of eight lanes:
**2,048 selected-lane cases**, with the other seven zero-weight lanes also checked.
It finds:

- **1,400 incorrect selected-lane product bit patterns**;
- **1,152 incorrect quantized sums/output nibbles**.

Some product errors are hidden by subsequent truncation/wrap. That is why the
bench observes products as well as the final nibble.

A minimal example is `input=-1`, `weight=+1`, other weights zero, bias 0:

| Stage | Expected signed contract | Observed RTL |
|---|---|---|
| Product byte | `ff` (−1) | `0f` (+15) |
| Per-product floor division by four | −1 | +3 |
| Identity output nibble | `f` | `3` |

This also reproduces through the package pins in
`soc_signed_input_weight_and_cancellation`. Mixed-activation and random multi-layer
networks demonstrate downstream impact. The random test completes all eight
default trials and collects discrepancies rather than stopping at its first
wrong output.

The reference preserves existing arithmetic choices: each product is divided
by four separately, the sum wraps to four bits, and bias addition wraps. It does
not substitute sum-then-divide or saturation. The failure is therefore not
caused by an invented rounding/saturation contract.

**Correction direction:** ensure each selected operand is explicitly signed
before multiplication, retaining a sufficiently wide product, then re-run the
same tests. For example, review casts on both selected nibbles. No correction
has been applied to the delivered RTL.

```sh
python run.py --suite mac --test 'mac_all_signed_pairs_each_lane$' --trace steps --output results-f01-mac
python run.py --suite soc --test 'soc_signed_input_weight_and_cancellation$' --trace steps --protocol --output results-f01-soc
python trace_view.py results-f01-soc/soc/soc_signed_input_weight_and_cancellation.jsonl --kind mac
```

## F02 — High: save-detector state survives reset

Location: [`src/project.sv`](../../src/project.sv), declarations at lines 198–199,
reset branch at lines 210–225, detector updates at lines 227–228.

`data_save_pulse` and `data_save_old` are not initialized by the reset branch.
The tests first create a pending save, then assert reset before the request is
consumed. They establish the state before resetting, so the result does not
depend on simulator power-on initialization.

Two independent observations are checked:

1. `soc_reset_clears_pending_save`: the detector state remains set during reset.
2. `soc_reset_pending_save_no_phantom_inference`: after reset is released with
   save low and address 7/data 5 still present, **slot 7 becomes 5 and flash CS
   becomes 0**, even though no new save event was issued. The sampled state is
   2 (`ADDRESS_HIGH`), demonstrating a phantom inference.

**Correction direction:** reset both detector registers and define behavior for
a save level already high when reset is released. Re-run warm-reset, every-state
recovery and subsequent input-loading checks after the fix.

```sh
python run.py --suite soc --test 'soc_reset_(clears_pending_save|pending_save_no_phantom_inference)$' --trace cycles --protocol --output results-f02
```

## Accepted input protocol and separate alternative checks

The requester confirmed that the default should document current behavior:
address/data are held through delayed capture, and a dedicated rearm pulse is
used after END. Those default checks pass and are not counted as defects.

A separate run with `--input-contract edge` deliberately asks for the stricter
first-edge/first-next-write behavior. It produces **two failures, 22 unselected
tests skipped**, as follows:

| Alternative requirement | Current behavior |
|---|---|
| Capture address 2/data 5 on the first save edge | If pins change on the following clock, address 3/data 6 is stored instead. |
| First save after END stores the next vector's first sample | The save only rearms. Reloading `[2]*8` after `[1]*8` yields `[1,2,2,2,2,2,2,2]`. |

These results are contract comparisons, **not two extra default-contract bugs**.
The exact safe input sequence is in [README.md](README.md#input-handshake-confirmed-for-this-validation).

```sh
python run.py --suite soc --test 'soc_input_capture_contract$|soc_first_write_after_end_contract$' --input-contract edge --trace cycles --protocol --output results-edge
```

## Mapping of the nine default failures

| Test | Finding |
|---|---|
| `mac_all_signed_pairs_each_lane` | F01 |
| `mac_negative_fraction_rounds_down` | F01 |
| `mac_eight_lane_extrema_and_cancellation` | F01 |
| `mac_seeded_mixed_vectors` | F01 |
| `soc_signed_input_weight_and_cancellation` | F01 |
| `soc_mixed_activation_multilayer_signed` | F01 |
| `soc_seeded_full_networks` | F01 |
| `soc_reset_clears_pending_save` | F02 |
| `soc_reset_pending_save_no_phantom_inference` | F02 |

## Passing checks and interpretation

The MAC suite isolates positive-domain multiplication, quantize-before-sum and
**all 16 sum codes × all 16 bias codes**. The activation tests cover all 128
function/input combinations, plus function chaining. Full-chip tests exercise
all 128 non-END headers, all 128 END bytes, lane/nibble order, temporary versus
committed outputs, all input codes/addresses, overwrites, held save, shuffled
input order, explicit rearm, busy-stage saves, and reset/recovery in all ten FSM
states when no save is pending.

Multi-layer checks cover atomic eight-neuron commits, positive-domain dense ReLU
networks, a nine-layer stream spanning byte address 255 in one SPI transaction,
and output stability after END. Partial-layer END is characterized separately:
temporary results are visible while the previous complete layer remains stored.
The encoder rejects partial-layer images for normal training/export.

The SPI suite passes at divider **1 and 4**, five tests each. All 256 TX byte
values and a permutation of all 256 RX byte values are tested, supplemented by
200 independent random exchanges, busy-send collisions, three-byte tail-window
pipelining and reset at every transfer clock position. Full-chip SPI checks
command 03, the 24-bit zero address, FF dummy MOSI, streaming data, bit timing,
CS framing and exact consumed bytes.

The activation functions are validated against their documented integer formulas
and LUT, not a floating-point approximation-quality target. Abs/negate at −8
wrap to code 8, and sigmoid codes above 7 become negative when reused as signed
MAC inputs. These are explicit characterizations, not new failures.

## Training/export result

The optional tool trains directly in integer parameter space. The supplied XOR
example reaches **4/4 exact cases** and exports a **41-byte** image. It is replayed
through the flash pins and checked against both labels and the reference model.
A separately trained two-layer model also passes RTL execution for its two
labelled examples. See [TRAINING.md](TRAINING.md) for method, dataset format and
commands.

The demonstrations use nonnegative MAC operands so they remain valid on this
revision. The general trainer targets intended signed arithmetic; its arbitrary
negative-operand models cannot be assumed to execute correctly until F01 is
fixed. The single-layer XOR exploits four-bit wraparound and the specified 0/4
input encoding. Training-set fit is not a generalization claim.

## Additional verification and evidence

- Ten host tests pass: arithmetic/activation anchors, byte-order golden value,
  encode/decode round trip, malformed inputs, training replay and impossible labels.
- Icarus 13.0 independently runs the MAC and activation suites: **12 pass / 4 fail**,
  reproducing the same four arithmetic failing tests.
- SPI divider 4: **5/5 pass**.
- Strict input-contract comparison: **0 pass / 2 fail / 22 unselected**.
- An empty test filter returns a deliberate infrastructure error, preventing an
  empty run from being reported as success.
- Detailed cycle and protocol modes, readable trace rendering, training, inference
  and export CLIs were exercised. The workflow is authored but not run remotely.

The main `results/` directory is the authoritative 45-test baseline. Each
supplementary run has its own folder/summary; do not add those overlapping tests
to the default total. The delivery archive preserves the baseline XML/logs/traces,
the source snapshot, patch, tools and training evidence.

## Limits and next action

This campaign establishes functional RTL findings. It does not perform new
physical signoff, DRC/LVS, static timing/SDF, CDC/metastability or real-flash/pad
margin validation. The baseline uses two-state Verilator; secondary arithmetic
checks do not constitute full-chip four-state power-on validation. No exhaustive
all-network/all-interleaving or formal proof is claimed.

Correct F01 and F02, then re-run the unchanged assertions and investigate any
subsequent failures. The default input protocol can remain as confirmed provided
its timing and rearm requirements are documented for users.
