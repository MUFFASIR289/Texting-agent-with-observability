import { LoopRing } from "./LoopRing";
import { Parallax } from "./Parallax";
import styles from "./Hero.module.css";

/* The one screen that has to work (§8). Three display lines with the loop ring
   occupying the middle line's parentheses, side labels breaking the symmetry,
   lede beneath, scroll cue bottom-right. */

export function Hero() {
  return (
    <section className={`lit ${styles.hero}`} id="top">
      <div className={`label ${styles.labels}`} aria-hidden="true">
        <span className={styles.labelL}>Twelve stages<br />One loop</span>
        <span className={styles.labelR}>Read-only<br />by construction</span>
      </div>

      <div className={styles.inner}>
        {/* A5 — the headline is real text, never an image. */}
        <h1 className="display">
          <span className={styles.line}>Stop Churn Before</span>
          <span className={`${styles.line} ${styles.parenLine}`}>
            <span className={styles.paren} aria-hidden="true">(</span>
            <Parallax className={styles.object}><LoopRing /></Parallax>
            <span className={styles.paren} aria-hidden="true">)</span>
          </span>
          <span className={styles.line}>It Starts</span>
        </h1>

        <p className={`lede ${styles.lede}`}>
          An agent that finds the customers slipping away, writes each of them a
          different message, and never sends one without a human saying yes.
        </p>
      </div>

      <div className={`label ${styles.cue}`}>
        <span>Scroll</span>
        <span className={styles.cueRule} aria-hidden="true" />
      </div>
    </section>
  );
}
