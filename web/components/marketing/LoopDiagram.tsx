import { STAGES } from "@/lib/loop";
import { Reveal } from "./Reveal";
import section from "./Section.module.css";
import styles from "./LoopDiagram.module.css";

/* The page's second peak (§8). Same light language as the hero, carrying
   actual information: these are the real campaign states from the backend,
   not twelve invented steps. */

export function LoopDiagram() {
  return (
    <section className={`lit ${section.section}`} id="loop">
      <div className={section.inner}>
        <Reveal>
          <span className={`label ${section.eyebrow}`}>The loop</span>
          <h2 className={`headline ${section.title}`}>
            Twelve stages, and a human standing in the middle of them.
          </h2>
          <p className={`lede ${section.intro}`}>
            Each stage is a real state a campaign moves through. Nothing skips
            ahead: a campaign that fails validation never reaches a person, and a
            campaign a person has not approved never reaches a customer.
          </p>
        </Reveal>

        <ol className={styles.spine}>
          {STAGES.map((s) => (
            <Reveal key={s.n} as="li" className={styles.stage}>
              <span className={styles.dot} aria-hidden="true" />
              <div>
                <div className={styles.head}>
                  <span className={`label ${styles.n}`}>{String(s.n).padStart(2, "0")}</span>
                  <h3 className={styles.name}>{s.name}</h3>
                  {s.state && <span className={styles.state}>{s.state}</span>}
                </div>
                <p className={styles.note}>{s.note}</p>
              </div>
            </Reveal>
          ))}
        </ol>

        <p className={styles.close}>
          <span className={styles.closeMark} aria-hidden="true">↺</span>
          <span className="ui">Twelve feeds one. What worked becomes next month&rsquo;s starting point.</span>
        </p>
      </div>
    </section>
  );
}
