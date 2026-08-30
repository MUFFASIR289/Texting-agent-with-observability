import { ImageResponse } from "next/og";

/* Generated at build time, so the OG card costs no runtime JS and no
   third-party request. Same luminance ramp as the page (§4). */

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Texting Agent — stop churn before it starts";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px",
          color: "hsl(152 20% 98%)",
          background:
            "radial-gradient(120% 80% at 50% 0%, hsl(152 40% 44%) 0%, hsl(152 44% 28%) 28%, hsl(152 42% 16%) 55%, hsl(152 38% 9%) 100%)",
        }}
      >
        <div style={{ fontSize: 22, letterSpacing: 6, opacity: 0.75 }}>TEXTING AGENT</div>
        <div style={{ fontSize: 92, lineHeight: 1.05, marginTop: 28, letterSpacing: -2 }}>
          Stop churn before it starts
        </div>
        <div style={{ fontSize: 30, marginTop: 32, opacity: 0.8, maxWidth: 900 }}>
          Twelve stages, one read-only table, and a human on the last gate.
        </div>
      </div>
    ),
    size,
  );
}
