import { STAGES } from "@/lib/loop";
import styles from "./LoopRing.module.css";

/* R1 — the object inside the parentheses. The twelve-stage loop as a slowly
   rotating ring whose nodes light in sequence, so the hero's object is the
   product rather than decoration beside it.

   A5 — decorative. The headline beside it carries the meaning, so this is
   hidden from assistive tech rather than narrated as twelve unlabelled dots. */

export function LoopRing({ className }: { className?: string }) {
  const R = 34;
  return (
    <div className={`${styles.wrap} ${className ?? ""}`} aria-hidden="true">
      <svg className={styles.svg} viewBox="0 0 100 100" role="presentation">
        <circle className={styles.bloom} cx="50" cy="50" r={R} />
        <g className={styles.ring}>
          <circle className={styles.track} cx="50" cy="50" r={R} />
          {STAGES.map((s, i) => {
            const a = (i / STAGES.length) * Math.PI * 2 - Math.PI / 2;
            return (
              <circle
                key={s.n}
                className={styles.node}
                cx={50 + R * Math.cos(a)}
                cy={50 + R * Math.sin(a)}
                r={1.6}
                style={{ animationDelay: `${(i / STAGES.length) * 9}s` }}
              />
            );
          })}
        </g>
      </svg>
    </div>
  );
}
