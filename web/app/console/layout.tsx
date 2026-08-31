import Link from "next/link";
import { health } from "@/lib/api";
import { signOut } from "@/lib/auth-actions";
import { Badge } from "@/components/ui";
import styles from "./layout.module.css";

/* data-mode="console" is set here rather than on <html>: the two surfaces share
   one token set and differ only in the application rules (§3). The console
   inherits the ramp and the typeface, and none of the atmosphere. */

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const status = await health();

  return (
    <div data-mode="console" className={styles.shell}>
      <header className={`ui ${styles.bar}`}>
        <Link className={styles.brand} href="/console">Texting<span>.</span>Agent</Link>
        <nav className={styles.links}>
          <Link href="/console">Campaigns</Link>
          <Link href="/console/new">New</Link>
          <Link href="/console/query">Ask</Link>
        </nav>
        <div className={styles.right}>
          <span className={styles.status}>
            {status
              ? <Badge tone={status.boundary_intact ? "ok" : "error"}>
                  {status.boundary_intact ? "BOUNDARY INTACT" : "BOUNDARY BREACHED"}
                </Badge>
              : <Badge tone="error">SERVICE UNREACHABLE</Badge>}
          </span>
          <form action={signOut}>
            <button className={`ui ${styles.signout}`} type="submit">Sign out</button>
          </form>
        </div>
      </header>
      <main id="main" className={styles.main}>{children}</main>
    </div>
  );
}
