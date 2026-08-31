import Link from "next/link";
import { api } from "@/lib/api";
import type { CampaignDetail } from "@/lib/types";
import { Badge, Card, ErrorBox, KeyValues, Table, ui } from "@/components/ui";
import {
  canCancel, canSend, channelList, num, parseJson, shortId, stateTone, when,
  type Offer, type Predicate,
} from "@/lib/states";
import { cancel, send, simulateEvents } from "@/lib/campaign-actions";
import styles from "./page.module.css";

/* Campaign detail and segments (§9): state, validation outcome, exclusion
   counts, token spend, and what the agent decided for each segment. */

export default async function CampaignPage({
  params, searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ error?: string }>;
}) {
  const { id } = await params;
  const { error } = await searchParams;
  const { campaign: c, segments, agent_runs } = await api<CampaignDetail>(`/campaigns/${id}`);

  const tokens = (c.tokens_in ?? 0) + (c.tokens_out ?? 0);
  const audience = segments.reduce((sum, s) => sum + s.customer_count, 0);

  return (
    <>
      <div className={styles.head}>
        <div className={styles.titleGroup}>
          <span className={`ui ${styles.id}`}>{shortId(c.campaign_id)} · {c.account_id}</span>
          <h1 className={`headline ${styles.goal}`}>{c.goal}</h1>
          <div><Badge tone={stateTone(c.state)}>{c.state}</Badge></div>
        </div>

        <div className={styles.actions}>
          <Link className={`ui ${ui.button}`} href={`/console/c/${id}/approval`}>Review content</Link>
          <Link className={`ui ${ui.button}`} href={`/console/c/${id}/sends`}>Send log</Link>
          {canSend(c.state) && (
            <form action={send.bind(null, id)}>
              <button className={`ui ${ui.button} ${ui.primary}`} type="submit">Send</button>
            </form>
          )}
          {c.state === "SENT" && (
            <form action={simulateEvents.bind(null, id)}>
              <button className={`ui ${ui.button}`} type="submit">Simulate events</button>
            </form>
          )}
          {canCancel(c.state) && (
            <form action={cancel.bind(null, id)}>
              <button className={`ui ${ui.button} ${ui.danger}`} type="submit">Cancel</button>
            </form>
          )}
        </div>
      </div>

      {error && <div className={styles.failure}><ErrorBox>{error}</ErrorBox></div>}

      {c.failure_code && (
        <div className={styles.failure}>
          <ErrorBox>
            <strong>{c.failure_code}</strong>
            {c.failure_detail ? ` — ${c.failure_detail}` : ""}
          </ErrorBox>
        </div>
      )}

      <div className={styles.grid}>
        <div className={styles.stack}>
          <Card title={`Segments · ${segments.length}`} pad={false}>
            {segments.map((s) => {
              const offer = parseJson<Offer>(s.offer_json);
              const predicate = parseJson<Predicate>(s.predicate_json);
              return (
                <div key={s.segment_id} className={styles.segment}>
                  <div className={styles.segHead}>
                    <span className={`ui ${styles.segName}`}>{s.name}</span>
                    <Badge>{num(s.customer_count)} customers</Badge>
                    {s.playbook_id && <Badge>{s.playbook_id}</Badge>}
                  </div>

                  <div className={styles.chips}>
                    {channelList(s.channels).map((ch) => <Badge key={ch}>{ch}</Badge>)}
                    {offer && (
                      <Badge tone="ok">
                        {offer.type}
                        {offer.value ? ` ${offer.value}%` : ""}
                        {offer.code ? ` · ${offer.code}` : ""}
                      </Badge>
                    )}
                  </div>

                  {predicate && (
                    <p className={styles.pred}>
                      risk {(predicate.risk_levels ?? []).join("/") || "any"}
                      {" · "}value {(predicate.value_tiers ?? []).join("/") || "any"}
                      {(predicate.required_reason_codes ?? []).length
                        ? ` · reasons ${(predicate.required_reason_codes ?? []).join("/")}` : ""}
                    </p>
                  )}

                  {/* FR-24: the channel choice has to cite measured engagement. */}
                  {s.rationale && <p className={`ui ${styles.rationale}`}>{s.rationale}</p>}
                </div>
              );
            })}
          </Card>

          <Card title={`Agent runs · ${agent_runs.length}`} pad={false}>
            <Table head={["Stage", "Model", "In", "Out", "Latency", "Status"]}>
              {agent_runs.map((r) => (
                <tr key={r.run_id}>
                  <td>{r.stage}</td>
                  <td className={ui.mono}>{r.model_id ?? "—"}</td>
                  <td className={ui.num}>{num(r.tokens_in)}</td>
                  <td className={ui.num}>{num(r.tokens_out)}</td>
                  <td className={ui.num}>{r.latency_ms ? `${num(r.latency_ms)} ms` : "—"}</td>
                  <td>
                    <Badge tone={r.status === "OK" ? "ok" : "error"}>{r.status}</Badge>
                    {r.error ? ` ${r.error}` : ""}
                  </td>
                </tr>
              ))}
            </Table>
          </Card>
        </div>

        <Card title="Run">
          <KeyValues rows={[
            ["Audience", num(audience)],
            ["Excluded, stale", num(c.excluded_stale_count)],
            ["Excluded, unknown risk", num(c.excluded_unknown_count)],
            ["Tokens", num(tokens)],
            ["Cost", c.llm_cost_usd === null ? "not recorded" : `$${c.llm_cost_usd}`],
            ["Model", c.model_id ?? "—"],
            ["Created by", c.created_by],
            ["Created", when(c.created_at)],
            ["Updated", when(c.updated_at)],
            ["Revised from", c.revised_from
              ? <Link className={styles.link} href={`/console/c/${c.revised_from}`}>{shortId(c.revised_from)}</Link>
              : "—"],
            /* FR-42: the hash binds the approval to this exact content and audience. */
            ["Content hash", c.content_hash
              ? <span className={styles.hash}>{c.content_hash}</span>
              : "not yet approved"],
          ]} />
        </Card>
      </div>
    </>
  );
}
