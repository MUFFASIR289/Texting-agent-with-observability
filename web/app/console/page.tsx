import Link from "next/link";
import { api } from "@/lib/api";
import type { Campaign } from "@/lib/types";
import { Badge, Card, Empty, Table, ui } from "@/components/ui";
import { num, shortId, stateTone, when } from "@/lib/states";
import styles from "./page.module.css";

/* Campaigns list — GET /campaigns (§9). */

export default async function Campaigns() {
  const { campaigns, count } = await api<{ campaigns: Campaign[]; count: number }>("/campaigns");

  return (
    <>
      <div className={styles.head}>
        <h1 className={`display ${styles.title}`}>Campaigns</h1>
        <span className={`ui ${styles.count}`}>{count} total</span>
      </div>

      <Card pad={false}>
        {campaigns.length === 0 ? (
          <Empty>
            No campaigns yet. <Link className={styles.link} href="/console/new">Create one</Link>.
          </Empty>
        ) : (
          <Table head={["Campaign", "State", "Goal", "Account", "Excluded", "Tokens", "Created", ""]}>
            {campaigns.map((c) => (
              <tr key={c.campaign_id}>
                <td className={ui.mono}>{shortId(c.campaign_id)}</td>
                <td>
                  <div className={styles.needs}>
                    <Badge tone={stateTone(c.state)}>{c.state}</Badge>
                  </div>
                </td>
                <td className={styles.goal}>{c.goal}</td>
                <td className={ui.mono}>{c.account_id}</td>
                {/* Stale plus unknown-risk: the customers the run declined to target. */}
                <td className={ui.num}>
                  {num((c.excluded_stale_count ?? 0) + (c.excluded_unknown_count ?? 0))}
                </td>
                <td className={ui.num}>
                  {num((c.tokens_in ?? 0) + (c.tokens_out ?? 0))}
                </td>
                <td>{when(c.created_at)}</td>
                <td>
                  <Link className={styles.link} href={`/console/c/${c.campaign_id}`}>Open</Link>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
