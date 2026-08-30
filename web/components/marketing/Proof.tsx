import { Reveal } from "./Reveal";
import section from "./Section.module.css";
import styles from "./Proof.module.css";

/* Real responses from a real run against 4,844 targetable customers. */
const CALLS = [
  {
    req: "POST /campaigns",
    res: `{
  "state": "AWAITING_APPROVAL",
  "targetable_customers": 4844,
  "violations": [],
  "variant_count": 16,
  "frozen_audience": 3327,
  "tokens_used": 56934
}`,
    caption: "Four segments, sixteen variants, zero policy violations. The audience is frozen at this moment so approval covers a fixed set of people.",
  },
  {
    req: "POST /campaigns/{id}/approve",
    res: `{
  "state": "APPROVED",
  "approved_by": "appr-1",
  "content_hash": "0c848abe90fa123c…"
}`,
    caption: "The approver key is a different key from the one that created the campaign. An operator asking to approve their own campaign gets a 403.",
  },
  {
    req: "POST /campaigns/{id}/send",
    res: `{
  "state": "SENT",
  "sent": 5235,
  "failed": 0,
  "skipped": 1419,
  "skip_reasons": {
    "NO_CONSENT": 1075,
    "FREQUENCY_CAP": 339,
    "SUPPRESSED": 5
  }
}`,
    caption: "Consent, suppression and frequency are re-checked at send time, not at approval time — state can change in between, and 1,419 people were spared a message because it did.",
  },
  {
    req: "GET /campaigns/{id}/sends",
    res: `{
  "customer_id": "A00001",
  "channel": "EMAIL",
  "status": "SENT",
  "provider_message_id": "eml_7bb9e9e24e14…"
}`,
    caption: "No name, no email address, no phone number. The audit trail identifies a customer the same way the agent does — by an id it cannot resolve to a person.",
  },
];

export function Proof() {
  return (
    <section className={section.section} id="proof">
      <div className={section.inner}>
        <Reveal>
          <span className={`label ${section.eyebrow}`}>Proof</span>
          <h2 className={`headline ${section.title}`}>One campaign, end to end.</h2>
          <p className={`lede ${section.intro}`}>
            Actual responses from the service, not a mockup. The operator console
            is still being built — this is the API underneath it.
          </p>
        </Reveal>

        <Reveal className={styles.frame}>
          <div className={styles.chrome}>
            <span className={styles.dot} aria-hidden="true" />
            <span className={styles.dot} aria-hidden="true" />
            <span className={styles.dot} aria-hidden="true" />
            <span className={`label ${styles.chromeLabel}`}>texting-agent · account ACC_A</span>
          </div>
          <div className={styles.body}>
            {CALLS.map((c) => (
              <div key={c.req} className={styles.call}>
                <p className={styles.req}>{c.req}</p>
                <pre className={`data ${styles.res}`}>{c.res}</pre>
                <p className={`ui ${styles.caption}`}>{c.caption}</p>
              </div>
            ))}
          </div>
        </Reveal>
      </div>
    </section>
  );
}
