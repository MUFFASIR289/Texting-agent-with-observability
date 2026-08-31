import Link from "next/link";
import { api } from "@/lib/api";
import type { CampaignDetail, Variant } from "@/lib/types";
import { Badge, ErrorBox, ui } from "@/components/ui";
import { canApprove, canReject, num, stateTone } from "@/lib/states";
import { decide } from "@/lib/campaign-actions";
import styles from "./page.module.css";

/* The approval screen — the product's centre of gravity (§9).
 *
 * It shows the exact content *and* the audience count the hash covers, because
 * that pair is what the approver is signing (FR-42). A screen showing only the
 * copy would quietly undo the guarantee the backend works to provide.
 */

export default async function Approval({
  params, searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ error?: string }>;
}) {
  const { id } = await params;
  const { error } = await searchParams;

  const [{ campaign: c, segments }, { variants }] = await Promise.all([
    api<CampaignDetail>(`/campaigns/${id}`),
    api<{ variants: Variant[]; count: number }>(`/campaigns/${id}/messages`),
  ]);

  const audience = segments.reduce((sum, s) => sum + s.customer_count, 0);
  const bySegment = new Map<string, Variant[]>();
  for (const v of variants) bySegment.set(v.segment_name, [...(bySegment.get(v.segment_name) ?? []), v]);

  return (
    <>
      <div className={styles.head}>
        <h1 className={`headline ${styles.title}`}>Review and approve</h1>
        <p className={`ui ${styles.sub}`}>
          <Link href={`/console/c/${id}`}>← {c.goal}</Link>
        </p>
      </div>

      {error && <div style={{ marginBottom: "var(--s-6)" }}><ErrorBox>{error}</ErrorBox></div>}

      <div className={styles.signing}>
        <div className={styles.stat}>
          <span className={`ui ${styles.statK}`}>State</span>
          <span><Badge tone={stateTone(c.state)}>{c.state}</Badge></span>
        </div>
        <div className={styles.stat}>
          <span className={`ui ${styles.statK}`}>Audience</span>
          <span className={styles.statN}>{num(audience)}</span>
        </div>
        <div className={styles.stat}>
          <span className={`ui ${styles.statK}`}>Variants</span>
          <span className={styles.statN}>{variants.length}</span>
        </div>
        <div className={styles.stat}>
          <span className={`ui ${styles.statK}`}>Content hash</span>
          <span className={styles.hash}>{c.content_hash ?? "not yet bound"}</span>
        </div>
      </div>

      {[...bySegment.entries()].map(([name, list]) => (
        <section key={name} className={styles.segment}>
          <h2 className={`ui ${styles.segTitle}`}>
            {name}
            <Badge>{num(segments.find((s) => s.name === name)?.customer_count ?? 0)} customers</Badge>
          </h2>

          <div className={styles.pair}>
            {list.map((v) => (
              <article key={v.variant_id} className={styles.variant}>
                <header className={styles.vHead}>
                  <Badge>{v.channel}</Badge>
                  <Badge tone="ok">Variant {v.label}</Badge>
                </header>

                <div className={styles.vBody}>
                  {v.preview ? (
                    <div>
                      <p className={`label ${styles.sectionLabel}`}>As the customer receives it</p>
                      {v.preview.subject && <p className={styles.subject}>{v.preview.subject}</p>}
                      <p className={styles.rendered}>{v.preview.body}</p>
                      {v.preview.cta_text && (
                        <p style={{ marginTop: "var(--s-3)" }}>
                          <span className={`ui ${styles.cta}`}>{v.preview.cta_text}</span>
                        </p>
                      )}
                    </div>
                  ) : (
                    <ErrorBox>Preview unavailable{v.preview_unavailable ? `: ${v.preview_unavailable}` : ""}</ErrorBox>
                  )}

                  <div>
                    <p className={`label ${styles.sectionLabel}`}>Template the agent wrote</p>
                    <pre className={styles.template}>
                      {v.subject_template ? `${v.subject_template}\n\n` : ""}{v.body_template}
                    </pre>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}

      {canApprove(c.state) || canReject(c.state) ? (
        <form className={styles.decision} action={decide.bind(null, id)}>
          <label className="ui" htmlFor="note">Note (optional)</label>
          <input className={styles.noteInput} id="note" name="note" maxLength={500} />
          <div className={styles.buttons}>
            <button className={`ui ${ui.button} ${ui.primary}`} name="decision" value="approve">
              Approve {num(audience)} recipients
            </button>
            <button className={`ui ${ui.button} ${ui.danger}`} name="decision" value="reject">
              Reject
            </button>
            <button className={`ui ${ui.button}`} name="decision" value="revise">
              Send back for revision
            </button>
          </div>
          <p className={`ui ${styles.sub}`}>
            Approving binds this decision to a hash of the content above and the
            audience beside it. If either changes afterwards, the send stops.
          </p>
        </form>
      ) : (
        <p className={`ui ${styles.settled}`}>
          This campaign is {c.state} — there is no decision left to make here.
        </p>
      )}
    </>
  );
}
