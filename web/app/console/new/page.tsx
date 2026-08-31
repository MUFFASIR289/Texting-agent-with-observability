import { createCampaign } from "@/lib/campaign-actions";
import { Card, ErrorBox, ui } from "@/components/ui";
import styles from "./page.module.css";

/* Create a campaign. The operator supplies an account and a goal, and nothing
   else: account_id is bound by the orchestrator and is never something the
   model can name (constraint 3), so there is no segment, offer or channel
   field here to override it with. */

export default async function NewCampaign({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;

  return (
    <>
      <h1 className={`headline ${styles.title}`}>New campaign</h1>
      <p className={`ui ${styles.sub}`}>
        Name the account and say what you are trying to achieve. The agent does
        the rest: it scores the customers, proposes the segments, picks a
        playbook and an offer for each, and writes the messages.
      </p>

      {error && <div style={{ marginBottom: "var(--s-6)" }}><ErrorBox>{error}</ErrorBox></div>}

      <Card>
        <form className={styles.form} action={createCampaign}>
          <div>
            <label className={`ui ${styles.label}`} htmlFor="account_id">Account</label>
            <input className={styles.input} id="account_id" name="account_id" defaultValue="ACC_A" required />
          </div>
          <div>
            <label className={`ui ${styles.label}`} htmlFor="goal">Goal</label>
            <textarea
              className={styles.textarea} id="goal" name="goal" rows={3} maxLength={500} required
              placeholder="Win back lapsed customers with a limited-time offer"
            />
          </div>
          <div>
            <button className={`ui ${ui.button} ${ui.primary}`} type="submit">Create campaign</button>
            <p className={`ui ${styles.warn}`} style={{ marginTop: "var(--s-3)" }}>
              This runs the model across every segment and takes several minutes.
              The page waits for it. Nothing sends until someone approves it.
            </p>
          </div>
        </form>
      </Card>
    </>
  );
}
