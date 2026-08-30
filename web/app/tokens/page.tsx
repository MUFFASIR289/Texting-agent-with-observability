import styles from "./page.module.css";

/* U0's done-when: this page renders every ramp step and every type role, so
   changing --hue in tokens.css visibly rebrands the whole system in one line. */

const RAMP = [
  ["--l-void", "vignette corners"],
  ["--l-deep", "page ground"],
  ["--l-base", "mid field"],
  ["--l-mid", "lit field"],
  ["--l-lift", "near the light source"],
  ["--l-glow", "bloom"],
  ["--l-white", "type, highlights"],
];

const SURFACES = [
  ["--surface-0", "console ground"],
  ["--surface-1", "raised"],
  ["--surface-2", "raised further"],
  ["--border", "hairline"],
];

const SEMANTIC = [
  ["--ok", "pass"],
  ["--warn", "caution"],
  ["--error", "violation"],
];

const SPACE = ["--s-1", "--s-2", "--s-3", "--s-4", "--s-6", "--s-8", "--s-12", "--s-16", "--s-24", "--s-32", "--s-48"];

function Swatch({ token, note }: { token: string; note: string }) {
  const dark = token === "--l-white" || token === "--l-glow";
  return (
    <div className={styles.swatch} style={{ background: `var(${token})`, color: dark ? "var(--l-deep)" : "var(--l-white)" }}>
      <span className={`data ${styles.swatchName}`}>{token}</span>
      <span className="label">{note}</span>
    </div>
  );
}

export default function Tokens() {
  return (
    <main id="main" className={styles.page}>
      <h1 className={`headline ${styles.h}`}>Design tokens</h1>
      <p className="lede">
        Every ramp step and type role in one place. Change <code>--hue</code> in
        <code> styles/tokens.css</code> and everything below rebrands.
      </p>

      <section className={styles.section}>
        <h2 className={`label ${styles.sectionTitle}`}>Luminance ramp — this is the palette</h2>
        <div className={styles.ramp}>
          {RAMP.map(([t, n]) => <Swatch key={t} token={t} note={n} />)}
        </div>
      </section>

      <section className={styles.section}>
        <h2 className={`label ${styles.sectionTitle}`}>Console surfaces — flat, no atmosphere</h2>
        <div className={styles.ramp}>
          {SURFACES.map(([t, n]) => <Swatch key={t} token={t} note={n} />)}
        </div>
      </section>

      <section className={styles.section}>
        <h2 className={`label ${styles.sectionTitle}`}>Semantic — the only other hues allowed</h2>
        <div className={styles.ramp}>
          {SEMANTIC.map(([t, n]) => <Swatch key={t} token={t} note={n} />)}
        </div>
      </section>

      <section className={styles.section}>
        <h2 className={`label ${styles.sectionTitle}`}>Type roles</h2>
        <div className={styles.roles}>
          <div className={styles.roleRow}>
            <p className={`label ${styles.roleMeta}`}>display · 300 · −0.025em · 0.94</p>
            <p className="display">Stop Churn</p>
          </div>
          <div className={styles.roleRow}>
            <p className={`label ${styles.roleMeta}`}>headline · 300 · −0.02em · 1.05</p>
            <p className="headline">One table, read-only</p>
          </div>
          <div className={styles.roleRow}>
            <p className={`label ${styles.roleMeta}`}>lede · 400 · 1.6</p>
            <p className="lede">The agent interprets. It never computes.</p>
          </div>
          <div className={styles.roleRow}>
            <p className={`label ${styles.roleMeta}`}>body · 400 · 1.65</p>
            <p>Deterministic code owns anything that must be correct.</p>
          </div>
          <div className={styles.roleRow}>
            <p className={`label ${styles.roleMeta}`}>ui · 500 · console</p>
            <p className="ui">Approve campaign</p>
          </div>
          <div className={styles.roleRow}>
            <p className={`label ${styles.roleMeta}`}>data · 500 · tabular-nums · console</p>
            <p className="data">5,235 sent · 0 failed · 1,419 skipped</p>
          </div>
          <div className={styles.roleRow}>
            <p className={`label ${styles.roleMeta}`}>label · 500 · 0.14em · decorative only (A2)</p>
            <p className="label">Frozen audience</p>
          </div>
        </div>
      </section>

      <section className={styles.section}>
        <h2 className={`label ${styles.sectionTitle}`}>Spacing — 4px base</h2>
        <div className={styles.scale}>
          {SPACE.map((t) => (
            <div key={t}>
              <div className={styles.step} style={{ width: `var(${t})`, height: `var(${t})` }} />
              <span className={`label ${styles.stepLabel}`}>{t.replace("--s-", "")}</span>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
