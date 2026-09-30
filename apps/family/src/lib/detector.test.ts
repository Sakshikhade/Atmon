/**
 * Smoke test for action_detection → DraftEvent mapping.
 * Run: npx --yes tsx --test apps/family/src/lib/detector.test.ts
 */
import assert from "node:assert/strict"
import { describe, it } from "node:test"
import { mapActionDetections } from "./detector.ts"
import { closedLiveSpans, eventsForTake, liveSpanToVideoSec, upsertLiveSpan } from "./liveDetect.ts"

describe("mapActionDetections", () => {
  it("maps class, times, and confidence bands", () => {
    let n = 0
    const events = mapActionDetections(
      [
        { class: "ear_cover", start_sec: 1.5, end_sec: 3.0, score: 2.1 },
        { class: "hair_twirling", start_sec: 4.0, end_sec: 5.5, score: 1.2 },
        { class: "unknown_class", start_sec: 0, end_sec: 1, score: 9 },
      ],
      10000,
      "video",
      () => `id-${(n += 1)}`,
    )
    assert.equal(events.length, 2)
    assert.equal(events[0].classKey, "ear_cover")
    assert.equal(events[0].onsetMs, 1500)
    assert.equal(events[0].durationMs, 1500)
    assert.equal(events[0].confidence, "confident")
    assert.equal(events[1].classKey, "hair_twirling")
    assert.equal(events[1].confidence, "needs_a_look")
  })

  it("clamps spans to the clip duration", () => {
    const events = mapActionDetections(
      [{ class: "head_nodding", start_sec: 9.5, end_sec: 12.0, score: 1.0 }],
      10000,
      "both",
      () => "fixed",
    )
    assert.equal(events.length, 1)
    assert.equal(events[0].onsetMs, 9500)
    assert.equal(events[0].durationMs, 500)
    assert.equal(events[0].channels, "both")
  })
})

describe("upsertLiveSpan", () => {
  it("keeps one row when the same gesture opens and closes", () => {
    const spans = new Map()
    upsertLiveSpan(spans, { event_id: "a", class: "ear_cover", start: 1, score: 1 }, "open")
    upsertLiveSpan(spans, { event_id: "a", class: "ear_cover", start: 1, end: 4, score: 3 }, "close")
    upsertLiveSpan(spans, { event_id: "a", class: "ear_cover", start: 1, end: 4, score: 3 }, "close")
    upsertLiveSpan(spans, { event_id: "b", class: "head_nodding", start: 5, end: 7, score: 2 }, "close")
    const closed = closedLiveSpans(spans.values())
    assert.equal(closed.length, 2)
    assert.equal(closed[0].end, 4)
    assert.equal(closed[0].score, 3)
    assert.equal(closed[1].class, "head_nodding")
  })
})

describe("eventsForTake", () => {
  it("keeps gestures from this take and closes one that is still open", () => {
    const events = eventsForTake(
      [
        { class: "ear_cover", start: 4, end: 9, score: 2 },
        { class: "head_nodding", start: 12, end: 18, score: 4 },
        { class: "hair_twirling", start: 20, end: null, score: 3 },
      ],
      10,
      22,
      [],
    )
    assert.equal(events.length, 2)
    assert.equal(events[0].class, "head_nodding")
    assert.equal(events[0].start_sec, 2)
    assert.equal(events[1].class, "hair_twirling")
    assert.equal(events[1].end_sec, 12)
  })
})

describe("liveSpanToVideoSec", () => {
  it("shifts for detector startup and drops paused time", () => {
    const pauses = [{ start: 2, end: 5 }]
    assert.equal(liveSpanToVideoSec(0, 1.5, pauses), 1.5)
    assert.equal(liveSpanToVideoSec(4, 1.5, pauses), 2.5)
  })
})
