import type { Metadata } from "next";
import { Inter_Tight } from "next/font/google";
import "@/styles/globals.css";

/* Self-hosted by next/font — no external font CDN, no layout shift, no
   third-party request on a page whose whole job is a first impression (§5).
   The variable file carries 300/400/500 in one download; three static weights
   would cost three (§11 asks for a small font payload, §5 asks for three
   weights, and this is how both hold). */
const interTight = Inter_Tight({
  subsets: ["latin"],
  display: "swap",
  variable: "--font",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://texting-agent.local"),
  title: "Texting Agent — Stop churn before it starts",
  description:
    "An agentic retention loop: detect at-risk customers, segment them, write the message, and prove what worked. Deterministic where it must be correct.",
  openGraph: {
    title: "Texting Agent — Stop churn before it starts",
    description:
      "An agentic retention loop that reads one read-only table, never puts customer data in a prompt, and needs a human approval before anything sends.",
    type: "website",
  },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      data-mode="cinematic"
      className={interTight.variable}
      suppressHydrationWarning   /* the script below sets data-js before hydration */
    >
      <head>
        {/* Motion is opted into, never assumed: this flips the flag the
            animation rules key off, so a page without JS renders complete
            and visible rather than waiting for a reveal that never comes (§6). */}
        <script
          dangerouslySetInnerHTML={{
            __html: 'document.documentElement.dataset.js="on"',
          }}
        />
      </head>
      <body>
        <a className="skip-link" href="#main">Skip to content</a>
        {children}
      </body>
    </html>
  );
}
