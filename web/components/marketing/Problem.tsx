import { Reveal } from "./Reveal";
import section from "./Section.module.css";
import styles from "./Problem.module.css";

/* The three panels named in §8, taken from PRD §1 rather than reworded. */
const PROBLEMS = [
  {
    n: "01",
    title: "Detection is late",
    body: "Churn gets noticed at reporting time — weeks after the behavioural signal actually appeared. By then the customer has already gone quiet.",
  },
  {
    n: "02",
    title: "Analysis does not scale",
    body: "Understanding why a particular cohort is disengaging means an analyst reading behaviour patterns customer by customer. Nobody has that week.",
  },
  {
    n: "03",
    title: "The loop never closes",
    body: "Campaign results are rarely fed back into the next campaign's strategy, so the same blanket discount goes out again next quarter.",
  },
];

export function Problem() {
  return (
    <section className={section.section} id="problem">
      <div className={section.inner}>
        <Reveal>
          <span className={`label ${section.eyebrow}`}>The gap</span>
          <h2 className={`headline ${section.title}`}>
            The data is already there. The action is what is missing.
          </h2>
        </Reveal>

        <div className={styles.grid}>
          {PROBLEMS.map((p, i) => (
            <Reveal key={p.n} as="article" className={styles.panel} delay={i * 90}>
              <span className={`label ${styles.n}`}>{p.n}</span>
              <h3 className={styles.panelTitle}>{p.title}</h3>
              <p className={styles.panelBody}>{p.body}</p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
