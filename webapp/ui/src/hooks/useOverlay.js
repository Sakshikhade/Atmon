import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Pose skeleton overlay — hold landmarks, never interpolate (instrument UI).
 */
export function useOverlay(videoRef, canvasRef) {
  const [skeletonOn, setSkeletonOn] = useState(
    () => localStorage.getItem('skeleton') !== 'off',
  );
  const cfgRef = useRef({ enabled: false, mirror: true, max_age_ms: 1200, edges: [] });
  const openCountRef = useRef(0);
  const lastDrawRef = useRef(0);
  const staleTimerRef = useRef(null);

  const syncSize = useCallback(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    const w = video.videoWidth || 640;
    const h = video.videoHeight || 480;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
  }, [videoRef, canvasRef]);

  const clear = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
  }, [canvasRef]);

  const draw = useCallback((points) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const cfg = cfgRef.current;
    if (!skeletonOn || !cfg.enabled) {
      clear();
      return;
    }
    syncSize();
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!points) return;

    const W = canvas.width;
    const H = canvas.height;
    const px = (p) => [p[0] * W, p[1] * H];
    const line = Math.max(2, W / 220);
    const dot = Math.max(2, W / 260);
    const stroke = openCountRef.current > 0 ? '#007aff' : 'rgba(255,255,255,0.92)';

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (const pass of [0, 1]) {
      ctx.strokeStyle = pass === 0 ? 'rgba(0,0,0,0.38)' : stroke;
      ctx.lineWidth = pass === 0 ? line + 2 : line;
      ctx.beginPath();
      for (const [a, b] of cfg.edges) {
        const pa = points[a];
        const pb = points[b];
        if (!pa || !pb) continue;
        const [ax, ay] = px(pa);
        const [bx, by] = px(pb);
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
      }
      ctx.stroke();
    }

    ctx.fillStyle = stroke;
    for (const p of points) {
      if (!p) continue;
      const [x, y] = px(p);
      ctx.beginPath();
      ctx.arc(x, y, dot, 0, Math.PI * 2);
      ctx.fill();
    }

    lastDrawRef.current = performance.now();
    clearTimeout(staleTimerRef.current);
    staleTimerRef.current = setTimeout(() => {
      if (performance.now() - lastDrawRef.current >= cfg.max_age_ms) clear();
    }, cfg.max_age_ms + 50);
  }, [skeletonOn, clear, syncSize, canvasRef]);

  const applyConfig = useCallback((overlay) => {
    cfgRef.current = Object.assign(cfgRef.current, overlay || {});
    const canvas = canvasRef.current;
    if (canvas) {
      canvas.style.transform = cfgRef.current.mirror ? 'scaleX(-1)' : 'none';
      canvas.style.display = cfgRef.current.enabled ? 'block' : 'none';
    }
  }, [canvasRef]);

  const toggleSkeleton = useCallback(() => {
    setSkeletonOn((on) => {
      const next = !on;
      localStorage.setItem('skeleton', next ? 'on' : 'off');
      if (!next) clear();
      return next;
    });
  }, [clear]);

  const bumpOpen = useCallback((delta) => {
    openCountRef.current = Math.max(0, openCountRef.current + delta);
  }, []);

  const resetOpen = useCallback(() => {
    openCountRef.current = 0;
  }, []);

  useEffect(() => {
    const onResize = () => syncSize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      clearTimeout(staleTimerRef.current);
    };
  }, [syncSize]);

  return {
    skeletonOn,
    toggleSkeleton,
    draw,
    clear,
    syncSize,
    applyConfig,
    bumpOpen,
    resetOpen,
  };
}
