import { useCallback, useEffect, useRef, useState } from 'react';

const FRAME_INTERVAL_MS = 125;

/**
 * Browser webcam + JPEG pump to /api/frame.
 * HIG: camera is mid-screen content; controls stay in panel toolbars.
 */
export function useCamera() {
  const streamRef = useRef(null);
  const pumpRef = useRef(null);
  const canvasRef = useRef(null);
  const liveVideoRef = useRef(null);
  const recordVideoRef = useRef(null);

  const stopPump = useCallback(() => {
    if (pumpRef.current) {
      clearInterval(pumpRef.current);
      pumpRef.current = null;
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

  const startPump = useCallback(() => {
    stopPump();
    if (!canvasRef.current) canvasRef.current = document.createElement('canvas');
    pumpRef.current = setInterval(async () => {
      if (!streamRef.current) return;
      const track = streamRef.current.getVideoTracks()[0];
      if (!track || track.readyState !== 'live') return;
      const videoEl =
        liveVideoRef.current?.srcObject ? liveVideoRef.current
          : recordVideoRef.current?.srcObject ? recordVideoRef.current
            : null;
      if (!videoEl || videoEl.readyState < 2) return;
      const w = videoEl.videoWidth || 640;
      const h = videoEl.videoHeight || 480;
      const canvas = canvasRef.current;
      if (canvas.width !== w) canvas.width = w;
      if (canvas.height !== h) canvas.height = h;
      canvas.getContext('2d').drawImage(videoEl, 0, 0, w, h);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.7));
      if (!blob) return;
      try {
        await fetch('/api/frame', {
          method: 'POST',
          headers: { 'Content-Type': 'image/jpeg' },
          body: blob,
        });
      } catch {
        /* drop */
      }
    }, FRAME_INTERVAL_MS);
  }, [stopPump]);

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
