import styles from "./Footer.module.css";

export function Footer() {
  return (
    <footer className={`ui ${styles.footer}`}>
      <span>Texting Agent</span>
      <ul className={styles.links}>
        <li><a href="#loop">The loop</a></li>
        <li><a href="#guardrails">Guardrails</a></li>
        <li><a href="/console">Console</a></li>
        <li><a href="/tokens">Design tokens</a></li>
      </ul>
    </footer>
  );
}
