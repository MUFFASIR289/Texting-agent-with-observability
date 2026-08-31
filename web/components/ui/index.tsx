import styles from "./ui.module.css";

export function Card({ title, action, children, pad = true }: {
  title?: string; action?: React.ReactNode; children: React.ReactNode; pad?: boolean;
}) {
  return (
    <section className={styles.card}>
      {title && (
        <header className={styles.cardHead}>
          <h2 className={`ui ${styles.cardTitle}`}>{title}</h2>
          {action}
        </header>
      )}
      <div className={pad ? styles.cardPad : undefined}>{children}</div>
    </section>
  );
}

export type Tone = "neutral" | "ok" | "warn" | "error";

export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: Tone }) {
  return <span className={`${styles.badge} ${styles[tone]}`}>{children}</span>;
}

export function Table({ head, children }: { head: React.ReactNode[]; children: React.ReactNode }) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead><tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className={`ui ${styles.empty}`}>{children}</p>;
}

export function ErrorBox({ children }: { children: React.ReactNode }) {
  return <div className={`ui ${styles.errorBox}`} role="alert">{children}</div>;
}

export function KeyValues({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className={styles.kv}>
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt className={styles.kvKey}>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export { styles as ui };
