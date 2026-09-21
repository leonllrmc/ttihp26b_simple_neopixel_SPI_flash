# Small integer-network training and flash-export tool

`network_tool.py` trains directly on this NPU's discrete arithmetic, evaluates
network JSON, and exports flash images. It uses only the Python standard library.
There is no machine-code generation, framework dependency or floating-point
weight file that must later be guessed into the hardware format.

## Train and reproduce the supplied XOR example

From `test/validation/`:

```sh
python network_tool.py train examples/xor-dataset.json --output results-training --weight-domain nonnegative --require-exact --verbose
python network_tool.py infer results-training/network.json --dataset examples/xor-dataset.json
python network_tool.py export results-training/network.json --output results-training/reexport.bin
```

Outputs:

| File | Content |
|---|---|
| `network.json` | Layer/weight/bias/function description and training metadata. |
| `flash.bin` | Binary flash bytes starting at address zero. |
| `flash.hex` | The same bytes as hexadecimal text, one byte per line. |
| `flash.listing.txt` | Byte offsets, layer/neuron indices, signed weights, biases and functions. |
| `training-report.json` | Seed, hyperparameters, loss, exact matches, predictions, dataset hash and epoch history. |

With the default seed `260920`, training finds neuron 0 weights
`[4,7,0,0,0,0,0,0]`, bias −1, step activation. The other seven neurons produce
zero. The image is 41 bytes. Output 0 reproduces all four labelled XOR cases:

| Inputs 0,1 | Expected | Trained model | RTL |
|---|---:|---:|---:|
| 0,0 | 0 | 0 | 0 |
| 0,4 | 1 | 1 | 1 |
| 4,0 | 1 | 1 | 1 |
| 4,4 | 0 | 0 | 0 |

This one-layer XOR relies on four-bit **wraparound**: the last preactivation is
code 10, interpreted as −6 before the step function. It is not a claim that a
conventional linear perceptron learns XOR. The result is specific to input codes
0 and 4; no accuracy outside the supplied cases is claimed.

`examples/xor-trained.json` is the stored generated fixture. The host test
re-trains it and compares the emitted bytes. `soc_trained_xor_flash_export`
executes all four cases through the flash pins, using explicit rearm between
inferences. `soc_train_two_layers_and_execute` also trains and runs a two-layer
network for a binary-input example.

## Your own dataset

The dataset format is JSON with equally many `inputs` and `targets` rows:

```json
{
  "inputs": [[0,0,0,0,0,0,0,0], [4,0,0,0,0,0,0,0]],
  "targets": [[0], [1]]
}
```

Every input row has exactly eight integers in −8..7. Pad unused inputs with zero.
Each target row has the same width, from one to eight outputs. Training scores
those first output neurons; every emitted layer still contains eight neurons.

For sigmoid outputs (functions 5/7), targets are interpreted as unsigned 0..15.
Other output functions use signed −8..7, including the wraparound of abs/negate
at −8. Inputs to subsequent layers are always signed nibbles. Use the activation
whose output interpretation matches your labels.

```sh
python network_tool.py train my-data.json --output results-my-network --depth 2 --activation step --epochs 20 --restarts 16 --seed 123 --verbose
python network_tool.py train my-data.json --validation held-out.json --output results-with-validation --depth 2
python network_tool.py infer results-my-network/network.json --inputs '4,0,0,0,0,0,0,0'
```

`--depth` supports one to eight eight-wide layers. `--activation` fixes the same
activation across trained neurons; exported/evaluated JSON can describe different
functions per neuron. `--weight-domain signed` searches −8..7 and is the default;
`nonnegative` limits weights to 0..7. Biases remain signed in either case.

## Method and practical limits

Training uses coordinate descent over integer weights and biases, seeded neutral
moves on quantization plateaus, and random restarts. Every candidate is scored by
the same documented integer MAC/activation contract. It minimizes squared error
on the semantic outputs and reports exact sample matches as a separate metric.
The host tests include impossible conflicting labels so imperfect fits are not
reported as exact.

This is intended for small datasets and small networks. It provides no guarantee
of finding the global optimum, no automatic architecture search, and no scaling
to large datasets. Increasing depth/restarts can be expensive. `--require-exact`
returns exit code 2 when the best fit is imperfect, while preserving the best
model and its report. A held-out dataset is evaluated only when supplied; fit on
the training examples alone is not a generalization result.

The current RTL has a confirmed signed-multiplication defect. The trainer targets
the intended **signed** arithmetic; it does not silently reproduce that defect.
The supplied RTL demonstrations deliberately use nonnegative weights and inputs,
and step outputs remain nonnegative in later layers. Negative biases are covered
by the validated modular bias addition. Arbitrary networks with negative MAC
operands require the RTL fix and regression before their exported predictions
can be relied upon on this chip.
