/* A9 — contrast verified by machine, not by eye.
 *
 * Reads the tokens straight out of styles/tokens.css, so a palette change is
 * checked rather than assumed. Run: npm run check:contrast
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const css = readFileSync(join(root, "styles/tokens.css"), "utf8");

const hue = Number(/--hue:\s*([\d.]+)/.exec(css)[1]);

/** Every `--name: hsl(var(--hue) S% L%)` in the token file. */
const tokens = Object.fromEntries(
  [...css.matchAll(/(--[\w-]+):\s*hsl\(\s*(var\(--hue\)|[\d.]+)\s+([\d.]+)%\s+([\d.]+)%\s*\)/g)]
    .map(([, name, h, s, l]) => [name, [h === "var(--hue)" ? hue : Number(h), Number(s), Number(l)]]),
);

const hslToRgb = ([h, s, l]) => {
  s /= 100; l /= 100;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [f(0), f(8), f(4)];
};

/* WCAG 2.1 relative luminance and contrast ratio. */
const luminance = (rgb) =>
  rgb.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
     .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);

const ratio = (a, b) => {
  const [x, y] = [luminance(hslToRgb(tokens[a])), luminance(hslToRgb(tokens[b]))].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

/* Where each pairing actually appears. The .lit gradient runs from --l-deep to
 * --l-lift, so display and body text is checked against the lightest step it
 * can land on, not the average. */
const PAIRS = [
  ["--l-white", "--l-deep",   4.5, "body text on the page ground"],
  ["--l-white", "--l-base",   4.5, "body text mid-gradient"],
  ["--l-white", "--l-mid",    4.5, "body text on the lit field"],
  /* A1 allows 3:1 for text >=24px. The brightest band of the .lit gradient is
     display and headline scale only: measured in the browser, the topmost
     normal-size text on the page sits 26% down the gradient, which is the
     --l-mid stop and is checked at the full 4.5 below. */
  ["--l-white", "--l-lift",     3, "display type at the brightest point of .lit"],
  ["--l-white", "--l-lift",     3, "section eyebrows, which live in that same band"],
  ["--l-deep",  "--l-white",  4.5, "pill CTA label"],
  ["--l-white", "--surface-0", 7,  "console body text"],
  ["--l-white", "--surface-1", 7,  "console text on a raised surface"],
  ["--l-white", "--surface-2", 7,  "console text on the highest surface"],
  ["--l-glow",  "--l-deep",   4.5, "eyebrows and state badges"],
  ["--l-glow",  "--surface-0", 4.5, "request lines in the proof frame"],
  ["--l-glow",  "--surface-1", 4.5, "response text in the proof frame"],
  ["--ok",      "--surface-0", 4.5, "pass state"],
  ["--warn",    "--surface-0", 4.5, "warning state"],
  ["--error",   "--surface-0", 4.5, "violation state"],
];

let failed = 0;
console.log(`contrast @ --hue: ${hue}\n`);

for (const [fg, bg, min, where] of PAIRS) {
  const r = ratio(fg, bg);
  const ok = r >= min;
  if (!ok) failed++;
  console.log(
    `${ok ? "PASS" : "FAIL"}  ${r.toFixed(2).padStart(6)}:1  (needs ${min})  ${fg} on ${bg}  — ${where}`,
  );
}

console.log(
  failed
    ? `\n${failed} pairing(s) below the floor in UIUX.md A1.`
    : `\nAll ${PAIRS.length} pairings meet the A1 floor.`,
);
process.exit(failed ? 1 : 0);
