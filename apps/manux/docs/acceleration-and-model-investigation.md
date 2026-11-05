# Training acceleration and model direction

Research snapshot: 2026-11-05. **Proposal, not an implementation or adopted decision.**
Baseline: preview `589cc9a8d338e40df8edc005825caf0c9a6329fc`.
PR [#72](https://github.com/intel7-ai/manux/pull/72) is concurrent learning-center work;
its source/packaged worker claims must be verified after merge. This investigation
does not modify its checkout, contracts, training code, or model store.

## Recommendation

Keep the modular Qt/Python application and replaceable recognizer boundary. Keep
the small multiclass causal TCN as the baseline until independent user/session
tests justify changing it. First improve supervision and measure end-to-end errors.
For training, prototype optional PyTorch CUDA, then Intel XPU. Treat OpenVINO NPU
as a separate inference experiment. An accelerator being present does not establish
that this small workload will train faster.

## What the code actually does

| Component | Evidence at baseline | Implication |
| --- | --- | --- |
| `tools/train_tcn.py:train` | Three causal Conv1d layers, 64 channels, dilations 1/2/4; 15 frames of 133 features; weighted cross entropy; Adam; batch 256 | Small supervised landmark classifier; no natural-language understanding |
| Training device | Model, weights, inputs, labels, permutations and augmentation tensors are constructed on CPU | Installing a GPU runtime alone changes nothing |
| `train_tcn.py:main` | Retrains for every available held-out person/posture/condition group, then final fit | User wait time includes many fits, data preparation, evaluation and ONNX export |
| `tcn_data.py:samples` | Builds overlapping windows, filters invalid hand rows; `transition`, `idle`, `no_hand` are excluded classes | Number of windows is not independent attempts; transitions remain an important evaluation blind spot |
| `perception/local3d.py` | Hand-local coordinates/deltas, fingertip distances, image palm velocity, validity flag; missing world coordinates yield zero features | Classifier cannot recover visual information lost by tracking; finger deltas also depend on frame cadence |
| `ml/predictor.py:_onnx_session` | Explicit `CPUExecutionProvider`; existing `Predictor`/`session_factory` boundary | Runtime acceleration can stay inside `ml`; no UI or output rewrite required |
| `features/tcn` | Neutral arming, confidence/count thresholds, tap/span state, mode eligibility, reset on loss/gap | Recognition score alone does not specify live action behavior |
| `tcn_data.py:evaluate` | Attempt hits and neutral misfires using prediction runs | Does not reproduce the complete direct-TCN/arbiter/router/output state machine or measure action latency |
| `docs/next-steps.md` | One-person comparison favors multiclass retraining over independent binary models/frozen head | Useful preliminary evidence; not a generalization result or a newly reproduced benchmark |

The receptive field is `1 + 2*(1+2+4) = 15` frames. At 30 fps this covers
about half a second; at 15 fps about one second. It is **history**, not an
unconditional half-second delay per decision. Initial warm-up and confirmation
add delay; event timestamps must establish actual gesture-to-action latency.

## Hardware options: training and inference are different jobs

| Hardware/backend | Training sessions | Inference | Priority |
| --- | --- | --- | --- |
| CPU/PyTorch | Current portable baseline | Current ONNX Runtime CPU | Always supported baseline |
| NVIDIA/PyTorch CUDA | Appropriate first optional training backend, with compatible wheel/driver | Separate ORT CUDA option | First training prototype [1] |
| Supported Intel GPU/PyTorch XPU | Official Windows training examples exist; exact GPU/driver/wheel must qualify | OpenVINO Intel GPU or other validated runtime | Second training prototype [2] |
| Intel NPU/OpenVINO | Not the backend for this PyTorch backward/optimizer loop | Compile exported model for NPU, verify ops, precision and latency | Inference research only [3] |
| Other DirectX 12 GPUs/torch-directml | Microsoft documents training in public preview; dependency/operator compatibility needs isolated proof | ORT DirectML exists, with new Windows feature development moving to WinML | Experimental; do not promise any GPU [4,5] |
| AMD GPU/native Windows ROCm | Matrix is restricted; linked Radeon limitations say no ML training support | Some PyTorch inference configurations supported | Do not advertise training from device presence alone [6,7] |

OpenVINO is not a universal NVIDIA/AMD training API. Intel GPU and Intel NPU are
different devices. ONNX Runtime execution providers accelerate an exported graph;
adding one to `Predictor` does not accelerate `loss.backward()` in the trainer.
Likewise, accelerating the TCN does not automatically accelerate MediaPipe tracking.
Do not promise Windows MediaPipe GPU support from the existence of a delegate enum.

### Proposed implementation boundaries

Scope: `tools/` trainer/comparison/worker; optional packaging; `ml` for inference.
Feature IDs stay `tcn`, `tcn_gate`; contracts §§4/10 (features/models), §12
(worker/UI after #72) and architecture §4 remain the boundaries. No contract changes
are made by this research. Update contracts first in an implementation PR.

1. Add a worker-owned training device option `auto|cpu|cuda|xpu`. Probe the installed
   runtime, device availability and a representative forward/backward/Adam step.
   Record requested device, actual device, torch build and fallback reason. An explicit
   unavailable device should fail clearly; `auto` may choose CPU with a visible reason.
   Start with one device, FP32, and a conservative benchmarked policy, not multi-GPU.
2. Move model **before** optimizer creation, loss weights, minibatches and labels
   consistently. Augmentation currently uses bare `rand/ones/zeros`; use the batch's
   device/dtype. Handle batch indices and RNG deliberately. Preserve seeded CPU
   behavior; record seeds rather than promising cross-device identical training.
3. Avoid sending every overlapping window to VRAM at once. Measure host preparation,
   cached features, window expansion, transfer, fit, folds and evaluation separately.
   Compare minibatch transfer against full-device caching only where memory permits.
4. Update `predict` to use the model device and `.cpu().numpy()`. Export a CPU eval
   copy with CPU dummy input. Check exported ONNX against the trained model and then
   run the existing independent-eval/promotion checks; acceleration bypasses none.
5. Share device handling with `compare_tcn.py`, which has its own model/training
   implementation. Warm up and synchronize the selected accelerator when timing.
6. Integrate through #72's separate MANUXWorker after merge. Ship CPU by default;
   evaluate optional backend distributions in isolated environments. Do not install
   multiple incompatible torch/ORT wheel variants into one worker or assume a
   source-Python experiment validates the frozen Windows executable.
7. Later, test an optional OpenVINO/ORT inference factory behind `Predictor`.
   Probe actual devices, compile this exact ONNX, record assigned execution and CPU
   fallback, compare outputs **and live events**, measure cold compile and warm p95.
   Preserve model hashes, neutral rearming and fail-safe resets. Never fall back to
   a different recognizer when a personal model rejects input. Start with explicit
   NPU selection so the experiment proves NPU execution, rather than relying on AUTO.

Suggested acceptance for a training prototype: same held-out protocol passes on
CPU and each claimed backend; exported runtime predictions remain within a declared
tolerance; cancel/failure preserves active model; clean packaged worker succeeds;
median **whole-session** wait improves meaningfully on representative datasets.
A provisional optimization target is at least 20% less total wait over three runs;
this is a proposed engineering gate, not an existing product requirement or result.

## Helping the model learn what the user intends

The labels are the instructions this model receives. A gesture name does not explain
its intended action or timing. Keep recognition separate from gesture-to-action
bindings. For a click, for example, demonstrate neutral → deliberate pinch → release,
and separately demonstrate touching fingers incidentally while moving the pointer.

Priorities, in order:

1. **Correct demonstrated intent.** Audit whether guided `do` labels match when the
   person actually performs the gesture. Allow review/retry of a mistaken attempt;
   log phase/onset rather than assuming the countdown produces correct labels.
2. **Real negatives.** Include pointer travel, resting hands, scratching, reaching,
   entering/leaving view, wrong fingers and returning from each gesture. Score
   transitions even if excluded from classifier training. Evaluate an explicit
   transition/rejection policy before adding a new label or changing the feature contract.
3. **Independent variation.** Separate recording attempts, sessions and people;
   vary speed, distance, illumination, hand orientation, posture and camera. Synthetic
   landmark augmentation cannot recreate occlusion or failed tracking in the image.
4. **Targeted corrections.** Use “that was wrong” markers to select candidate hard
   negatives/positives for human confirmation. Do not automatically relabel or train
   on every correction and then use the same example as independent evaluation.
5. **Calibrate action policy on validation data.** Compare class thresholds, ambiguity
   margin, neutral rearming and phase timing at a fixed false-action budget. Freeze
   settings before a final held-out test; never tune against that final test repeatedly.
6. **Then compare representation/models.** Test distance/angle baseline or a small MLP,
   current TCN, then longer/time-normalized TCN or a small GRU if temporal mistakes
   persist. Use a new feature version for changed values. A pretrained hand-sequence
   encoder may eventually reduce examples, but the existing two-gesture frozen-head
   failure does not evaluate such pretraining. Transformers/RGB models need a measured
   reason and enough independent data, not simply a larger architecture.

An LLM could help explain setup or map a user's phrase to a reviewed catalog/binding
later. It would not supply missing hand landmarks, correct noisy demonstration
labels, or replace deterministic real-time action/release control.

## Is the architecture worth pursuing?

**Application architecture: yes, continue.** The recognizer interface, replayable
observations, gesture/action separation, single Windows output boundary, immutable
snapshots and atomic model promotion make alternatives testable. Qt versus another UI
is not a current recognition bottleneck. Keep heavy training in the separate worker;
the engine should continue to process the latest frame without building a queue.

**Model architecture: reasonable baseline, still unproven.** Causal temporal
convolution is an established sequence-modeling starting point [8]; that paper does
not validate this particular small implementation or webcam control. Current
one-person evidence cannot establish robustness across people, lighting or cameras.
Independent binary models also need shared arbitration and new-gesture negatives;
adding a file does not prevent cross-triggering.

| Observation in held-out testing | Next direction |
| --- | --- |
| Missing/unstable world landmarks in dim light or at intended distance | Improve capture/tracking or camera setup; consider different sensing only if the intended setting remains unobservable |
| Stable landmarks, confused hand shapes | Better labels/negatives and discriminative features; compare simple baseline with TCN |
| Correct shapes, wrong starts/releases/repeated actions | Phase/arming/state policy and temporal context; replay the complete engine |
| Correct replay, unreliable live tasks | Tracking latency/cadence, integration, feedback or ergonomics |
| Good recognition, slow retraining | Profile folds/data preparation before adding training hardware |
| Fine at desk, unusable reclining | Reassess product interaction/camera placement; a desk-only score does not validate the final use case |

## Testing the current app can give direction, with the right protocol

1. **Freeze a tested build.** After #72, identify exact commit, package, tracker/model,
   settings, bindings and device. Finish practice/no-output smoke checks before OS
   tasks. Exercise pause, hand loss, cancel and model-swap release behavior.
2. **Small pilot:** 3–5 people on their own cameras, at least two sessions on different
   occasions, separate train/validation/final eval attempts. This is an early direction
   finding exercise, not a release-level sample-size claim. Include intended reclining
   distance/lighting as well as desk conditions. Do not change all factors at once.
3. **Controlled tasks:** pointer target acquisition, click/double-click, drag/drop,
   scroll, mode changes, long neutral/ordinary hand movement, and hand re-entry. Include
   ordinary-mouse reference tasks and record comfort/fatigue and setup/training burden.
4. **Paired replay:** same held-out landmark streams through rules, rules+TCN gate,
   and direct TCN, with identical bindings/settings; record emitted commands in practice
   output. Report per-person/per-condition attempts, wrong-action counts, misses,
   duplicate actions, neutral exposure minutes, and stuck/late releases. Classifier-only
   `evaluate_tcn.py` is useful but cannot substitute for this full-pipeline comparison.
5. **Live confirmation:** counterbalance recognizer order; measure task success/time,
   recovery, unintended OS actions, and onset-to-action median/p95. Record tracking
   loss and frame age separately from TCN time. Landmark replay cannot compare camera
   or tracker alternatives because it starts after tracking.
6. **Hardware benchmark:** small/medium/growing dataset sizes, three runs each, cold
   and warm cache, same epochs/folds/seeds, actual CPU/CUDA/XPU; isolate backends so
   simultaneous training does not distort comparison. Report wall time, memory,
   export/promotion success, and app responsiveness while training. Separately benchmark
   TCN CPU/NPU inference and camera-to-action latency; no hardware speedup is measured here.

Agree product tolerances before the final eval: false actions per neutral minute,
per-gesture attempt recall, p95 latency, task completion and fatigue. For perspective,
zero false actions in 10 neutral minutes is weak evidence: under a stationary Poisson
assumption the one-sided 95% upper rate is approximately `3 / exposure_minutes`.
Zero over 30 minutes bounds the rate near 0.1/min under that assumption; correlated
failures and different users/conditions require separate reporting. Any stuck button
or missing safety release blocks release regardless of aggregate accuracy.

The first decision should be **which stage fails**, then which model/hardware to
change. Testing the present app is valuable precisely because these stages are
separated; a single “accuracy” percentage would hide that direction.

## Sources checked 2026-11-05

1. [PyTorch Windows/CUDA installation](https://docs.pytorch.org/get-started/locally/) — choose compatible builds; no package version inferred from search snippets.
2. [PyTorch Intel GPU/XPU guide](https://docs.pytorch.org/docs/2.15/notes/get_start_xpu.html) — Windows hardware matrix and forward/backward training examples.
3. [OpenVINO NPU device](https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html) — inference compilation, caching, precision and device properties.
4. [Microsoft PyTorch with DirectML](https://learn.microsoft.com/en-us/windows/ai/directml/pytorch-windows) — native Windows training/inference in public preview; not a compatibility guarantee for MANUX dependencies.
5. [ONNX Runtime DirectML](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html) — inference provider; sustained engineering and WinML direction.
6. [AMD Windows compatibility matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html) — restricted hardware/framework combinations.
7. [AMD Radeon limitations](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/limitations/limitationsrad.html) — Windows training limitations; recheck the pinned SDK before any prototype.
8. [Bai, Kolter, Koltun: empirical evaluation of convolutional/recurrent sequence models](https://arxiv.org/abs/1803.01271) — general motivation for a TCN baseline, not MANUX performance evidence.
