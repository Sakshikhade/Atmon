import { useCallback, useEffect, useRef } from 'react';

const FRAME_INTERVAL_MS = 125;
/** After a successful frame POST, this many 409s in a row means ingest ended. */
const CONFLICT_STOP_AFTER = 12;

/**
 * Browser webcam + JPEG pump to /api/frame.
 * Early 409s (server still loading) are ignored so warmup does not kill the pump.
 * After ingest has accepted frames once, sustained 409s stop the pump.
 */
export function useCamera() {
  const streamRef = useRef(null);
  const pumpTimerRef = useRef(null);
  const pumpGenRef = useRef(0);
  const canvasRef = useRef(null);
  const liveVideoRef = useRef(null);
  const recordVideoRef = useRef(null);
  const pumpingRef = useRef(false);
  const acceptedOnceRef = useRef(false);
  const conflictStreakRef = useRef(0);

  const stopPump = useCallback(() => {
    pumpingRef.current = false;
    pumpGenRef.current += 1;
    acceptedOnceRef.current = false;
    conflictStreakRef.current = 0;
    if (pumpTimerRef.current) {
      clearTimeout(pumpTimerRef.current);
      pumpTimerRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    stopPump();
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    for (const ref of [liveVideoRef, recordVideoRef]) {
      if (ref.current) {
        ref.current.srcObject = null;
      }
    }
  }, [stopPump]);

  const open = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('This browser cannot access a webcam (needs HTTPS + getUserMedia).');
    }
    stop();
    streamRef.current = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } },
    });
    return streamRef.current;
  }, [stop]);

  const tick = useCallback(async (gen) => {
    if (gen !== pumpGenRef.current || !pumpingRef.current) return;
    if (!streamRef.current) {
      stopPump();
      return;
    }

    const track = streamRef.current.getVideoTracks()[0];
    if (!track || track.readyState !== 'live') {
      stopPump();
      return;
    }

    const videoEl =
      liveVideoRef.current?.srcObject ? liveVideoRef.current
        : recordVideoRef.current?.srcObject ? recordVideoRef.current
          : null;

    if (videoEl && videoEl.readyState >= 2) {
      if (!canvasRef.current) canvasRef.current = document.createElement('canvas');
      const w = videoEl.videoWidth || 640;
      const h = videoEl.videoHeight || 480;
      const canvas = canvasRef.current;
      if (canvas.width !== w) canvas.width = w;
      if (canvas.height !== h) canvas.height = h;
      canvas.getContext('2d').drawImage(videoEl, 0, 0, w, h);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.7));
      if (gen !== pumpGenRef.current || !pumpingRef.current) return;
      if (blob) {
        try {
          const res = await fetch('/api/frame', {
            method: 'POST',
            headers: { 'Content-Type': 'image/jpeg' },
            body: blob,
          });
          if (res.ok) {
            acceptedOnceRef.current = true;
            conflictStreakRef.current = 0;
          } else if (res.status === 409) {
            // Warmup / not-yet-bound ingest: keep trying.
            // After ingest has worked once, sustained 409 ⇒ session ended.
            if (acceptedOnceRef.current) {
              conflictStreakRef.current += 1;
              if (conflictStreakRef.current >= CONFLICT_STOP_AFTER) {
                stopPump();
                return;
              }
            }
          }
        } catch {
          /* network blip — keep trying until stop */
        }
      }
    }

    if (gen !== pumpGenRef.current || !pumpingRef.current) return;
    pumpTimerRef.current = setTimeout(() => tick(gen), FRAME_INTERVAL_MS);
  }, [stopPump]);

  const startPump = useCallback(() => {
    stopPump();
    pumpingRef.current = true;
    const gen = pumpGenRef.current;
    pumpTimerRef.current = setTimeout(() => tick(gen), 0);
  }, [stopPump, tick]);

  const attach = useCallback(async (videoEl) => {
    if (!videoEl || !streamRef.current) return;
    videoEl.srcObject = streamRef.current;
    const play = videoEl.play();
    if (play?.catch) play.catch(() => {});
    startPump();
  }, [startPump]);

  useEffect(() => () => stop(), [stop]);

  return { open, stop, attach, liveVideoRef, recordVideoRef };
}
