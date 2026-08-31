import { signIn } from "@/lib/auth-actions";
import styles from "./page.module.css";

/* Not an account, and not a password: the operator pastes the API key they
   already hold, and the role on that key is what the API will enforce. */

export default async function SignIn({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; expired?: string }>;
}) {
  const { error, expired } = await searchParams;

  return (
    <div data-mode="console" className={styles.wrap}>
      <main className={styles.card}>
        <h1 className={`headline ${styles.title}`}>Operator console</h1>
        <p className={`ui ${styles.hint}`}>Sign in with your API key.</p>

        {expired && <p className={`ui ${styles.error}`}>That key was rejected. Sign in again.</p>}
        {error === "empty" && <p className={`ui ${styles.error}`}>Enter a key.</p>}

        <form action={signIn}>
          <label className={`ui ${styles.label}`} htmlFor="key">API key</label>
          <input
            className={styles.input}
            id="key"
            name="key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            required
          />
          <button className={`ui ${styles.button}`} type="submit">Sign in</button>
        </form>

        <p className={`ui ${styles.note}`}>
          The key is held in an httpOnly cookie and used server-side only — it is
          never readable from the browser. Your role travels with it: an operator
          key cannot approve, and an approver key cannot send.
        </p>
      </main>
    </div>
  );
}
