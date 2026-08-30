"use client";

import { useEffect, useRef } from "react";

/* Scroll-linked drift for the hero object (§6): translateY at 0.25x scroll with
   a subtle scale-down. Throttled to requestAnimationFrame (§11), and it simply
   does not run under reduced motion — the object stays where it was painted. */

export function Parallax({ children, className }: { children: React.ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const y = window.scrollY;
        el.style.transform = `translateY(${y * 0.25}px) scale(${Math.max(0.8, 1 - y / 4000)})`;
      });
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return <div ref={ref} className={className}>{children}</div>;
}
