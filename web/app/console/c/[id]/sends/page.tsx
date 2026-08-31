import Link from "next/link";
import { api } from "@/lib/api";
import type { CampaignDetail, Send } from "@/lib/types";
import { Badge, Card, Empty, Table, ui } from "@/components/ui";
import { num, when } from "@/lib/states";
import styles from "./page.module.css";

/* The send log — the audit trail. Note what is *not* here: no name, no email,
   no phone. The trail identifies a customer the same way the agent does, by an
   id it cannot resolve to a person. */

const SHOWN = 250;

export default async function Sends({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [{ campaign }, { sends, count }] = await Promise.all([
    api<CampaignDetail>(`/campaigns/${id}`),
    api<{ sends: Send[]; count: number }>(`/campaigns/${id}/sends`),
  ]);

  // Counting the rows the API returned — a tally of what came back, not a
  // business figure. Anything that has to be *correct* stays in the backend.
  const byStatus = new Map<string, number>();
  const bySkip = new Map<string, number>();
  for (const s of sends) {
    byStatus.set(s.status, (byStatus.get(s.status) ?? 0) + 1);
    if (s.skip_reason) bySkip.set(s.skip_reason, (bySkip.get(s.skip_reason) ?? 0) + 1);
  }

  return (
    <>
      <div className={styles.head}>
        <h1 className={`headline ${styles.title}`}>Send log</h1>
        <p className={`ui ${styles.sub}`}>
          <Link href={`/console/c/${id}`}>← {campaign.goal}</Link>
        </p>
      </div>

      <div className={styles.summary}>
        <div className={styles.tile}>
          <span className={`ui ${styles.tileK}`}>Attempts</span>
          <span className={styles.tileN}>{num(count)}</span>
        </div>
        {[...byStatus].map(([status, n]) => (
          <div key={status} className={styles.tile}>
            <span className={`ui ${styles.tileK}`}>{status}</span>
            <span className={styles.tileN}>{num(n)}</span>
          </div>
        ))}
        {[...bySkip].map(([reason, n]) => (
          <div key={reason} className={styles.tile}>
            <span className={`ui ${styles.tileK}`}>Skipped · {reason}</span>
            <span className={styles.tileN}>{num(n)}</span>
          </div>
        ))}
      </div>

      <Card pad={false} title={`Attempts · showing ${Math.min(SHOWN, sends.length)} of ${num(count)}`}>
        {sends.length === 0 ? (
          <Empty>Nothing has been attempted for this campaign yet.</Empty>
        ) : (
          <Table head={["Customer", "Channel", "Status", "Skip reason", "Provider id", "Attempted"]}>
            {sends.slice(0, SHOWN).map((s, i) => (
              <tr key={`${s.customer_id}-${s.channel}-${i}`}>
                <td className={ui.mono}>{s.customer_id}</td>
                <td>{s.channel}</td>
                <td>
                  <Badge tone={s.status === "SENT" ? "ok" : s.status === "FAILED" ? "error" : "warn"}>
                    {s.status}
                  </Badge>
                </td>
                <td>{s.skip_reason ?? "—"}</td>
                <td className={ui.mono}>{s.provider_message_id ?? "—"}</td>
                <td>{when(s.attempted_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <p className={`ui ${styles.note}`}>
        Customers appear as ids. No name, email address or phone number is
        exposed here, or anywhere the agent can reach.
      </p>
    </>
  );
}
