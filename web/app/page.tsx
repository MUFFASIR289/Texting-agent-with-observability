import { Nav } from "@/components/marketing/Nav";
import { Hero } from "@/components/marketing/Hero";
import { Problem } from "@/components/marketing/Problem";
import { LoopDiagram } from "@/components/marketing/LoopDiagram";
import { Guardrails } from "@/components/marketing/Guardrails";
import { Proof } from "@/components/marketing/Proof";
import { CTA } from "@/components/marketing/CTA";
import { Footer } from "@/components/marketing/Footer";

export default function Home() {
  return (
    <>
      <Nav />
      <main id="main">
        <Hero />
        <Problem />
        <LoopDiagram />
        <Guardrails />
        <Proof />
        <CTA />
      </main>
      <Footer />
    </>
  );
}
