/**
 * Generează DIRECT un "Necesar Producție" pentru un brand:
 * rulează Forecastul (velocity → suggestedQty) și creează Necesarul DRAFT
 * cu cantitățile sugerate — fără să treci prin UI.
 *
 * Dry-run (nu scrie nimic):
 *   npx tsx scripts/generate-necesar.ts --brand ESTEBAN
 * Creează Necesarul DRAFT:
 *   npx tsx scripts/generate-necesar.ts --brand ESTEBAN --yes
 *
 * Opțiuni: --lookback 60  --forecast-days 60  --lead 14  --round 50
 *          --min 1        (ignoră liniile cu suggestedQty sub prag)
 *          --title "..."  --notes "..."
 *
 * Formula (canonică, din schema):
 *   velocity        = netUnitsSold(lookback) / inStockDays  (sau /lookbackDays, vezi velocitySource)
 *   forecastDemand  = ceil(velocity × forecastDays)
 *   raw             = max(0, forecastDemand − onHand − pendingIncoming)
 *   suggestedQty    = roundUpToUnit(raw, brand.productionRoundingUnit)
 */
import "dotenv/config";
import { PrismaClient } from "../src/generated/prisma/client";
import { PrismaPg } from "@prisma/adapter-pg";
import { ForecastService } from "../src/lib/services/forecast.service";
import { ProductionRequirementService } from "../src/lib/services/production-requirement.service";

const prisma = new PrismaClient({
  adapter: new PrismaPg({ connectionString: process.env.DATABASE_URL! }),
});

const argv = process.argv;
const flag = (n: string) => argv.includes(`--${n}`);
const opt = (n: string, d?: string) => {
  const i = argv.indexOf(`--${n}`);
  return i > -1 && argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : d;
};

const APPLY = flag("yes");
const BRAND = (opt("brand") || "").toUpperCase();
const LOOKBACK = Number(opt("lookback", "60"));
const FDAYS = Number(opt("forecast-days", "60"));
const LEAD = opt("lead") ? Number(opt("lead")) : undefined;
const ROUND = opt("round") ? Number(opt("round")) : undefined;
const MIN = Number(opt("min", "1"));
const TITLE = opt("title");
const NOTES = opt("notes");

const n = (x: unknown) => Number(x ?? 0);

async function main() {
  const brands = await prisma.brand.findMany({
    where: { isActive: true },
    select: { id: true, brandCode: true, name: true },
    orderBy: { brandCode: "asc" },
  });

  if (!BRAND) {
    console.error("Lipsește --brand. Branduri active:");
    for (const b of brands) console.error(`   ${b.brandCode}  (${b.name})`);
    process.exit(1);
  }
  const brand = brands.find((b) => b.brandCode.toUpperCase() === BRAND);
  if (!brand) {
    console.error(`Brand necunoscut: ${BRAND}. Disponibile: ${brands.map((b) => b.brandCode).join(", ")}`);
    process.exit(1);
  }

  // 1. Readiness — STRICT: toate variantele trebuie mapate pe ScentMaster
  const rd = await ForecastService.readiness(brand.id);
  if (!rd.success) {
    console.error(`Readiness a eșuat: ${rd.error}`);
    process.exit(1);
  }
  if (!rd.data.ready) {
    console.error(`\n⛔ BLOCAT — ${rd.data.unmappedVariants} variante NEMAPATE la ${brand.name} (din ${rd.data.totalVariants}).`);
    console.error("   Forecastul e gated până le mapezi (Mapare Produse). Exemple:");
    for (const u of rd.data.unmappedSample.slice(0, 8)) {
      console.error(`     - ${u.sku ?? "(fără SKU)"}  ${u.productTitle} ${u.variantTitle}`);
    }
    process.exit(1);
  }

  // 2. Rulează forecastul
  console.log(`\nRulez forecast: ${brand.name} (${brand.brandCode}) — lookback ${LOOKBACK}z, acoperire ${FDAYS}z${LEAD ? `, lead ${LEAD}z` : ""}${ROUND ? `, rotunjire ${ROUND}` : ""}`);
  const run = await ForecastService.runForBrand({
    brandId: brand.id,
    lookbackDays: LOOKBACK,
    forecastDays: FDAYS,
    productionLeadTimeDays: LEAD,
    roundingUnit: ROUND,
    notes: NOTES ?? undefined,
  });
  if (!run.success) {
    console.error(`Forecast eșuat: ${run.error}`);
    process.exit(1);
  }
  const r = run.data;
  console.log(
    `  run ${r.runId} | sursă viteză: ${r.velocitySource} | variante ${r.mappedVariants}/${r.totalVariants} | ` +
      `vândute în fereastră ${r.unitsSoldInWindow} | total sugerat ${r.totalSuggestedQty} buc`,
  );

  // 3. Liniile cu sugestie
  const rows = await prisma.forecastRow.findMany({
    where: { forecastRunId: r.runId, suggestedQty: { gte: MIN } },
    orderBy: { suggestedQty: "desc" },
  });
  if (rows.length === 0) {
    console.log("\nNicio linie cu sugestie > 0 — nu e nimic de produs. (Stoc + în producție acoperă cererea.)");
    await prisma.$disconnect();
    return;
  }

  const total = rows.reduce((s, x) => s + x.suggestedQty, 0);
  console.log(`\n${APPLY ? "APLIC" : "DRY-RUN (nu scriu nimic)"} — ${rows.length} linii, total ${total} buc\n`);
  console.log(
    `${"SKU".padEnd(14)}${"Produs".padEnd(34)}${"vând.".padStart(6)}${"stoc".padStart(6)}${"în prod.".padStart(9)}${"vit/zi".padStart(8)}${"SUGERAT".padStart(9)}`,
  );
  console.log("-".repeat(86));
  for (const x of rows) {
    const name = `${x.productTitle}${x.variantTitle && x.variantTitle !== "Default Title" ? " / " + x.variantTitle : ""}`;
    console.log(
      `${(x.sku ?? "—").slice(0, 13).padEnd(14)}${name.slice(0, 33).padEnd(34)}` +
        `${String(x.netUnitsSold).padStart(6)}${String(x.onHand).padStart(6)}${String(x.pendingIncoming).padStart(9)}` +
        `${n(x.velocityPerDay).toFixed(2).padStart(8)}${String(x.suggestedQty).padStart(9)}`,
    );
  }
  console.log("-".repeat(86));
  console.log(`${"TOTAL".padEnd(77)}${String(total).padStart(9)}`);

  if (!APPLY) {
    console.log(`\nDRY-RUN. Adaugă --yes ca să creez Necesarul DRAFT (PR-${brand.brandCode}-…).`);
    await prisma.$disconnect();
    return;
  }

  // 4. Creează Necesarul DRAFT
  const res = await ProductionRequirementService.create({
    brandId: brand.id,
    forecastRunId: r.runId,
    lookbackDays: LOOKBACK,
    forecastDays: FDAYS,
    title: TITLE ?? undefined,
    notes: NOTES ?? undefined,
    items: rows.map((x) => ({ variantId: x.variantId, requestedQty: x.suggestedQty })),
  });
  if (!res.success) {
    console.error(`\nCreare Necesar eșuată: ${res.error}`);
    process.exit(1);
  }
  console.log(`\n✅ Necesar creat: ${res.data.number}  (id ${res.data.id}) — status DRAFT, ${rows.length} linii, ${total} buc.`);
  console.log(`   Deschide-l în app: /productie/necesar/${res.data.id}`);
  await prisma.$disconnect();
}

main().catch(async (e) => {
  console.error(e);
  await prisma.$disconnect();
  process.exit(1);
});
