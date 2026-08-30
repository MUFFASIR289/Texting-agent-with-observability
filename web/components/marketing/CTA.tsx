import { Reveal } from "./Reveal";
import styles from "./CTA.module.css";

/* Single action, restated headline treatment (§8). */
export function CTA() {
  return (
    <section className={`lit ${styles.cta}`} id="cta">
      <div className={styles.inner}>
        <Reveal>
          <h2 className={`display ${styles.title}`}>See it run</h2>
          <p className={`lede ${styles.lede}`}>
            On your data, against your policy caps, with your approver holding the
            last gate.
          </p>
          <a className={styles.button} href="mailto:hello@texting-agent.example">
            Request access
          </a>
          <p className={`label ${styles.note}`}>No customer data leaves your database</p>
        </Reveal>
      </div>
    </section>
  );
}
