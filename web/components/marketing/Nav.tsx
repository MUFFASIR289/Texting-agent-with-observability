import styles from "./Nav.module.css";

export function Nav() {
  return (
    <nav className={styles.nav}>
      <a className={styles.wordmark} href="#top">
        Texting<span>.</span>Agent
      </a>
      <ul className={`ui ${styles.links}`}>
        <li><a href="#loop">The loop</a></li>
        <li><a href="#guardrails">Guardrails</a></li>
        <li><a href="#proof">Proof</a></li>
        <li><a href="/console">Console</a></li>
      </ul>
      <a className={`ui ${styles.cta}`} href="#cta">Request access</a>
    </nav>
  );
}
