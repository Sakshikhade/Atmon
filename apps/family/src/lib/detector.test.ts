/**
 * Smoke test for action_detection → DraftEvent mapping.
 * Run: npx --yes tsx --test apps/family/src/lib/detector.test.ts
 */
import assert from "node:assert/strict"
import { describe, it } from "node:test"
import { mapActionDetections } from "./detector.ts"

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
