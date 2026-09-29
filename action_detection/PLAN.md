# Plan of action

Supersedes the "what next" reasoning scattered through `proj_desc.md` §11.
`proj_desc.md` remains the specification; this is the working plan against it.

Last updated after Phase B was measured on eval3 (held out, unseen room).

---

## 1. Where this actually stands

Built and tested (125 passing): Phases 1–4, the event log with per-event clip
saving, live capture, and three fused streams (V-JEPA 2 appearance, MediaPipe
pose via DTW, MediaPipe hand landmarks via DTW).

**Measured, held out across two videos:**

```
fit eval1 → test eval2    P 1.00  R 0.40  F1 0.57  FA/hr    0.0   mAP@0.5 0.500
fit eval2 → test eval1    P 0.33  R 0.20  F1 0.25  FA/hr  120.8   mAP@0.5 0.167
```

Same-video (optimistic): eval1 R 0.20, eval2 R 0.60.

**Not deployable.** Three of five events in eval1 showed *no score elevation at
all* during the action — not a threshold problem.

### What the diagnostics established

| Finding | Evidence |
| :--- | :--- |
| The hand stream is what makes `hair_twirling` detectable | AUROC 0.58 → 0.83; pose alone 0.60 |
| Pose is strongest for `ear_cover` | d′ +2.60, AUROC 0.96 |
| The appearance stream tracks the **room**, not the behaviour | eval2: +1σ for 21 chunks, −1σ after, switching exactly at the 62 s room change |
| Detection generalises by **hand position**, not by scene | Only the temple-height instances score, including in the reference room |
| Hand descriptor is not rotation-invariant | Canonicalising drops failing events from 0.48/0.41 → 0.29/0.28 |
| A pairwise-distance descriptor separates far better | background/action 2.40× vs 1.44× today |
| Cropping is currently a no-op | Subject already fills the frame; boxes come back full-frame |

---

## 2. Goals, as they now stand

- **Reference clips are the source of truth.** Not text prompts.
- Detect in **long** video, not 60-second samples.
- Generalise across **surroundings, clothing, and person**.
- Action set may broaden to coarse whole-body actions (jumping, moving in
  circles) alongside the current fine-grained hand-to-head ones.
- **Do not build on pose/hand landmarks as the primary signal.** They may remain
  as optional streams; the system must not depend on them.
- No training when actions are added or changed.

---

## 3. The core tension

A reference clip pins an appearance. Text does not mention the room; a clip
shows one. With clips as the source of truth, **invariance has to be engineered
in — it will not come from the reference.**

Everything in Phase A and B below is a way of getting appearance out of the
comparison while keeping the clip as the query.

Second tension, from the broadened action set: a jump cycle is ~1 s and circling
a room is 10–20 s. A single global `W_base` already fails across `ear_cover`
(2–4 s) and `hair_twirling` (5–12 s). A third scale makes it untenable.

---

## 4. Phase A — measured, and it did not work

Implemented in `phase_a/` and measured on both eval videos at two token
resolutions. **No lever changed held-out event-level performance at all** --
every fused variant returns the identical P 1.00 / R 0.40 / mAP@0.5 0.500 one
way and P 0.33 / R 0.20 / 0.167 the other.

Appearance-stream-only, which is where these levers act:

```
                        ear_cover              hair_twirling
pooled cosine       d' +1.03  AUC 0.768     d' +0.22  AUC 0.500
A2 scene-sub        d' +1.13  AUC 0.801     d' +0.00  AUC 0.482
A3 per-class        d' +1.03  AUC 0.768     d' +0.22  AUC 0.500   (no-op)
A1 maxsim (g=8)     d' +0.44  AUC 0.592     d' +0.18  AUC 0.541
A1 maxsim (g=4)     d' +0.50  AUC 0.628     d' +0.15  AUC 0.556
```

**A1 MaxSim makes things worse, at both grid resolutions.** The reasoning below
was wrong in a specific way: late interaction helps when a target contains the
reference's content *plus distractors*. Here the "distractor" -- the same person,
the same room, the same shirt -- is present in BOTH reference and target, so
token-level max-matching amplifies precisely the shared nuisance content that
mean pooling was averaging away.

**A2 scene subtraction is roughly redundant with z-scoring.** The scene
component is near-constant within a video, and standardizing already removes a
constant. It would only pay where the scene *varies*, and a single background
prototype per video cannot represent eval2's two rooms.

**A3 was never actually tested.** Both reference clips were trimmed to 3.10s, so
per-class `W_base` derives the same value for both classes and the lever is an
exact no-op. The real fix -- scales from each behaviour's expected DURATION
rather than its reference clip's length -- remains untested.

**The deeper result:** the appearance stream contributes so little that changing
it cannot move the system. Appearance-only AUROC for `hair_twirling` is 0.500 --
exactly chance. Fused results are unmoved because pose and hands carry the
signal and appearance holds 0.30-0.35 weight over noise.

Improving how the appearance stream is *matched* cannot help when the stream has
nothing to match on. That points past Phase A to Phase B.

### Original Phase A rationale (retained for the record)

Cheap, all on cached features, and together they answer *how much of the room
problem is matching rather than representation*. Do these before any backbone
migration.

**A1. Token-level late interaction (MaxSim).** Keep a reduced patch-token grid
per chunk instead of one pooled vector; score as mean-over-reference-tokens of
max-over-target-tokens. Action tokens find their counterparts, background tokens
fail to match and contribute little — so a reference shot in one room can match
an instance in another. Doubly motivated: it is also the fix for small-in-frame
actions. Parameter-free. Costs a heavier cache and slower scoring.

**A2. Explicit scene subtraction.** Score a window as
`sim(window, reference) − sim(window, background model of this video)`, where the
background model is the low-percentile embedding of the video's own chunks. Both
terms carry the shared scene component, so it cancels. Unsupervised, adapts per
video, so a new room self-corrects.

**A3. Per-class temporal scales.** `W_base` becomes per class rather than the
median of all reference durations. Required, not optional, once durations span
1 s to 20 s.

**A4. Tighter crop — head and active hand, not the whole person.** The current
person-level box is a no-op because the subject fills the frame. A head+wrist box
is perhaps 15% of frame area, so most of the room never enters the encoder.

**A5. Rotation-invariant hand descriptor** (pairwise distances). Only if the hand
stream is retained; measured to improve separation 1.44× → 2.40×.

---

## 5. Phase B — measured on genuinely held-out data, and it works

**Headline: X-CLIP appearance-only reaches mAP@0.5 = 0.750 with zero false
alarms on eval3** -- 9.3 minutes, an unseen room, 74% background, never used to
fit a threshold or a weight. Three of four events found, no false positives, and
nothing fired on the 64-second chin-rest hard negative.

```
fit tau on eval1+eval2  ->  test on eval3        (tau = 1.681 sigma)

vjepa  / appearance   P 0.00  R 0.00  mAP.5 0.000  FA/h 19.3
vjepa  / fused        P 0.25  R 0.25  mAP.5 0.125  FA/h 19.3
xclip  / appearance   P 1.00  R 0.75  mAP.5 0.750  FA/h  0.0   <-- best
xclip  / fused        P 0.25  R 0.25  mAP.5 0.125  FA/h 19.3
siglip / appearance   P 0.67  R 0.50  mAP.5 0.500  FA/h  6.4
siglip / fused        P 0.25  R 0.25  mAP.5 0.125  FA/h 19.3
```

For context, spec §13 expects 55-68 mAP@0.5 on long well-separated actions.

**The keypoint streams are now actively harmful.** Every fused row collapses to
the same 0.25/0.125/19.3 regardless of backbone, because the weights hold
appearance at 0.30-0.35 and let pose and hands dominate -- and pose and hands
fail on eval3, where the subject is seated looking down at a desk rather than
upright and frontal as in every reference clip. They helped on eval1/eval2
because they were the only streams that worked at all; on held-out data they
destroy a result the appearance stream gets right.

**The pooled chunk-level numbers mislead here and are worth ignoring.** `xclip /
fused` shows AUC 0.974 on ear_cover while returning mAP 0.125. Chunk-level
separability says nothing about extent or false alarms.

Detections on eval3:

```
hair_twirling  196.0-268.0s  score +2.28  tIoU 0.96 against gt 196-265
ear_cover      368.0-396.0s  score +2.89  tIoU 0.79 against gt 370-392
ear_cover      530.0-560.0s  score +2.97  tIoU 0.77 against gt 534-557
MISSED         hair_twirling 85-118s
```

The miss is the shorter (33s) hair-twirling instance. The chin-rest span
17-81s produced zero detections.

Caveats: four instances, so recall moves in steps of 0.25; FA/h 0.0 over 7
minutes of background means "below roughly 8/hour", not zero; and one held-out
video is one video.

### Superseded: measurement on eval1+eval2 only

Implemented in `phase_b/` with interchangeable backbones fed identical chunks,
identical crop and identical reference variants, so the encoder is the only
variable.

```
                             ear_cover              hair_twirling
vjepa  / appearance     d' +1.03  AUC 0.768     d' +0.22  AUC 0.500   <- chance
xclip  / appearance     d' +2.73  AUC 0.971     d' +2.08  AUC 0.951
siglip / appearance     d' +0.37  AUC 0.641     d' +0.41  AUC 0.675

vjepa  / fused          d' +3.27  AUC 0.978     d' +1.34  AUC 0.842
xclip  / fused          d' +2.81  AUC 0.974     d' +1.94  AUC 0.938
```

**X-CLIP takes the appearance stream from chance to 0.951 on `hair_twirling`** --
appearance ALONE now beats the entire three-stream V-JEPA system (0.842). Its
cross-class similarity is 0.834 against V-JEPA's 0.942, i.e. markedly less
shared nuisance. The text-supervision argument held.

**SigLIP is the informative control.** Per-frame image-text embeddings, mean
pooled, reach only 0.675 -- better than V-JEPA's chance but far below X-CLIP.
So this is not purely about appearance abstraction: X-CLIP's cross-frame
attention is doing real work, and temporal modelling matters for these
behaviours.

**The fusion weights are now wrong.** They were tuned when appearance was noise.
With X-CLIP, weighting it at 0.30 actively dilutes the best stream:

```
xclip/pose/hands            ear_cover          hair_twirling
0.30/0.00/0.70 (current)  AUC 0.971            AUC 0.936
0.70/0.10/0.20            AUC 0.986            AUC 0.938
1.00/0.00/0.00            AUC 0.971            AUC 0.951
```

Also: X-CLIP encodes at **0.36 s/clip against V-JEPA's 1.68 s**, and SigLIP at
0.16 s. A backbone swap that improves accuracy AND is 4.7x faster changes the
live-mode and long-video arithmetic as well.

Caveat: these weights are tuned on the same two videos they are measured on,
which is the protocol failure flagged in §7. The AUROC gap is large enough to
survive that; the specific weight values are not.

### Original Phase B rationale

**B1. Swap V-JEPA 2 for a video-language model's *visual* encoder**
(InternVideo2, ViCLIP, or similar), keeping clip references and writing no
prompts.

Rationale: self-supervised video models learn predictive features, and appearance
is highly predictive — which is why the current stream acts as a room detector.
Video-language models are trained to align with captions, which forces them to
discard room-specific detail. You inherit text-supervised abstraction while
keeping clips as the query.

It is a drop-in at the encoder boundary: everything downstream operates on
vectors. Constraint: 8 GB unified memory limits which checkpoint fits.

---

## 6. Phase C — scale to long video

**C1. Proposal then verify.** A cheap class-agnostic stage proposes candidate
spans; the heavy encoder runs only on those. An hour is ~1,800 chunks ≈ 50
minutes of encoding today, and background grows linearly so false alarms do too.
For rare behaviours this is a 10–100× reduction, and it is the cascade shape
§11.3 already endorses.

**C2. Per-class calibration** against a false-alarm budget, on background-heavy
footage.

---

## 7. The binding constraint is data, not code

None of Phase A or B can be *compared* on what exists today.

| Need | Why | Current state |
| :--- | :--- | :--- |
| Reference clips from ≥2 settings per action | Cannot claim cross-domain generalisation from single-setting references | 1 clip, 1 setting, per class |
| Eval video from a setting never referenced | The only honest test of generalisation | eval2 is partly the reference room |
| Background-heavy footage (≥10 min, no performed actions) | FA/hour decides deployability and is currently computed on 29 s and 37 s | 51% and 66% action |
| ≥20 instances per class | spec §8.1; below this a threshold is not trustworthy | 5 per video |

**Reference clips should vary hand position and configuration**, which the
measurements identify as the failure axis — not room, which pose and hand
normalisation already discard.

---

## 8. Deliberately not doing

- **Cross-attention few-shot matchers (TRX, HyRSM, MoLo, MUPPET).** All require
  meta-training on a base-class dataset, none ship usable checkpoints, and the
  recognition ones classify trimmed clips rather than localising in untrimmed
  video. §11.4 already says this.
- **Text-conditioned open-vocabulary TAD** (T3AL, FreeZAD). Training-free and
  code exists, but conditions on text, which contradicts "references are the
  source of truth". Keep as a fallback if clip-conditioning stalls.
- **A trained linear head** (§11.2). Ruled out by the no-training constraint,
  though it remains the best available *diagnostic* for whether the
  representation contains the signal at all.
- **Ordered temporal alignment (DTW/OTAM) on the appearance stream.** Sounds
  sophisticated, but both current behaviours are stationary or repetitive, so
  order carries little class information, and 2 s chunks give only ~3 elements
  per window.

---

## 9. Open questions

1. Are jumping and circling representative of where this is going, or
   exploratory? Coarse whole-body actions are the *easy* case for an appearance
   encoder, and would change which phase matters most.
2. Is single-subject still the assumption? §1's "assume one subject" holds until
   a second person enters frame, after which detection and tracking are
   unavoidable.
3. What false-alarm rate is actually acceptable? `max_false_alarms_per_hour: 5`
   was inherited from the spec and never examined against how the alerts get used.
