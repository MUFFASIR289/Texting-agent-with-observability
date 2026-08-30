import { Reveal } from "./Reveal";
import section from "./Section.module.css";
import styles from "./Guardrails.module.css";

/* The security model is the product's strongest claim, so it gets a section
   rather than a bullet (§8). Each claim is enforced in code and covered by a
   test — none of these are promises about prompt wording. */

const GUARDRAILS = [
  {
    n: "01",
    claim: "The agent reads one table, read-only.",
    because:
      "A separate database file, opened read-only, with query_only set on the connection. The agent has no vocabulary for SQL and no second table to reach for.",
  },
  {
    n: "02",
    claim: "Customer data never enters a prompt.",
    because:
      "The model writes templates with placeholders. Code fills in the name, the email and the order count after the model is done — so fabricating a customer detail is not something it can get wrong, it is something it cannot express.",
  },
  {
    n: "03",
    claim: "A human approves the exact content and the exact audience.",
    because:
      "The approval is bound to a hash of both. Change a word or the audience after the fact and the approval no longer matches, so the send stops.",
  },
  {
    n: "04",
    claim: "Policy caps fail loudly, never quietly.",
    because:
      "A discount over its tier cap does not get clamped down to the limit and sent anyway. The campaign fails with the rule it broke, because a silent correction hides a drifting prompt.",
  },
];

export function Guardrails() {
  return (
    <section className={section.section} id="guardrails">
      <div className={section.inner}>
        <Reveal>
          <span className={`label ${section.eyebrow}`}>Guardrails</span>
          <h2 className={`headline ${section.title}`}>
            What the model is structurally unable to do.
          </h2>
          <p className={`lede ${section.intro}`}>
            Every one of these is enforced by code and covered by a test. None of
            them depend on asking the model nicely.
          </p>
        </Reveal>

        <div className={styles.list}>
          {GUARDRAILS.map((g) => (
            <Reveal key={g.n} className={styles.item}>
              <span className={`label ${styles.n}`}>{g.n}</span>
              <div>
                <h3 className={`headline ${styles.claim}`}>{g.claim}</h3>
                <p className={styles.because}>{g.because}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
