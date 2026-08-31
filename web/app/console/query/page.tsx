import { ask } from "@/lib/query-actions";
import { Badge, Card, ErrorBox, ui } from "@/components/ui";
import styles from "./page.module.css";

/* Natural-language box, grounded answer, and the tools the agent actually
   called (§9). The tool list is the point: it is what makes the answer
   checkable rather than merely fluent. */

export default async function Query({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const sp = await searchParams;

  return (
    <>
      <h1 className={`headline ${styles.title}`}>Ask the agent</h1>
      <p className={`ui ${styles.sub}`}>
        Questions are answered from the customer table only. If the tools do not
        contain the answer the agent says so rather than guessing, and it names
        the tools it used either way.
      </p>

      <form className={styles.form} action={ask}>
        <div className={styles.row}>
          <input
            className={styles.input} name="account_id" placeholder="ACC_A"
            defaultValue={sp.a ?? "ACC_A"} required aria-label="Account id"
          />
          <textarea
            className={styles.textarea} name="query" rows={2} maxLength={1000} required
            placeholder="How many customers are at critical churn risk?"
            defaultValue={sp.q ?? ""} aria-label="Question"
          />
        </div>
        <div>
          <button className={`ui ${ui.button} ${ui.primary}`} type="submit">Ask</button>
          <p className={`ui ${styles.slow}`}>This calls the model, so it takes a few seconds.</p>
        </div>
      </form>

      {sp.error && <ErrorBox>{sp.error}</ErrorBox>}

      {sp.answer && (
        <Card title="Answer">
          {sp.q && <p className={`ui ${styles.asked}`}>“{sp.q}”</p>}
          <p className={styles.answer}>{sp.answer}</p>
          <div className={styles.meta}>
            <span className={`ui ${styles.metaK}`}>Grounded in</span>
            {(sp.grounded ?? "").split(",").filter(Boolean).map((t) => <Badge key={t} tone="ok">{t}</Badge>)}
            <span className={`ui ${styles.metaK}`}>Tokens</span>
            <Badge>{Number(sp.tokens ?? 0).toLocaleString()}</Badge>
            {sp.truncated === "true" && <Badge tone="warn">TRUNCATED</Badge>}
          </div>
        </Card>
      )}
    </>
  );
}
