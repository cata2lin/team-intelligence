/**
 * scentum-cli — operează Scentum ERP din terminal, prin SERVICIILE CANONICE ale aplicației
 * (aceeași logică ca UI-ul: validări, audit, numerotare). NU scrie SQL brut.
 *
 * TOATE mutațiile sunt DRY-RUN implicit — scriu doar cu `--yes`.
 * Recepția (push stoc în Shopify) cere în plus `--confirm-shopify` (e IREVERSIBILĂ din Scentum).
 *
 *   npx tsx scripts/scentum-cli.ts <comandă> [opțiuni]
 *
 * CITIRE (fără efecte):
 *   brands
 *   necesar list [--brand ESTEBAN] [--status DRAFT]
 *   necesar show   --id <prId>
 *   livrare list [--status DRAFT]
 *   livrare show   --id <deliveryId>
 *   livrare eligible                      # linii Necesar COMPLETED, neluate pe altă livrare
 *
 * NECESAR (mutații):
 *   necesar generate --brand ESTEBAN [--lookback 60 --forecast-days 60 --round 50] [--yes]
 *   necesar create   --brand ESTEBAN [--title "..."] [--notes "..."] [--yes]
 *   necesar add-line --id <prId> --variant <variantId> --qty 50 [--yes]
 *   necesar set-qty  --item <itemId> --qty 80 [--yes]
 *   necesar remove-line --item <itemId> [--yes]
 *   necesar approve  --id <prId> [--yes]
 *   necesar cancel   --id <prId> --reason "..." [--yes]
 *   necesar mark     --item <itemId> --qty 40 [--yes]      # marchează fabricat (parțial permis)
 *   necesar cancel-line --item <itemId> --reason "..." [--yes]
 *
 * LIVRARE (mutații):
 *   livrare create   [--items id1,id2] [--notes "..."] [--yes]
 *   livrare add-item --id <deliveryId> --pri-item <necesarItemId> [--yes]
 *   livrare remove-item --item <deliveryItemId> [--yes]
 *   livrare approve  --id <deliveryId> [--yes]
 *   livrare cancel   --id <deliveryId> --reason "..." [--yes]
 *   livrare receive  --id <deliveryId> [--items id1,id2] --yes --confirm-shopify
 */
import "dotenv/config";
import { PrismaClient } from "../src/generated/prisma/client";
import { PrismaPg } from "@prisma/adapter-pg";
import { ForecastService } from "../src/lib/services/forecast.service";
import { ProductionRequirementService } from "../src/lib/services/production-requirement.service";
import { DeliveryService } from "../src/lib/services/delivery.service";
import { receiveDelivery } from "../src/lib/services/delivery-receive.service";

const prisma = new PrismaClient({
  adapter: new PrismaPg({ connectionString: process.env.DATABASE_URL! }),
});

const argv = process.argv.slice(2);
const cmd = (argv[0] ?? "").toLowerCase();
const sub = (argv[1] ?? "").toLowerCase();
const has = (n: string) => argv.includes(`--${n}`);
const opt = (n: string, d?: string) => {
  const i = argv.indexOf(`--${n}`);
  return i > -1 && argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : d;
};
const APPLY = has("yes");
const num = (v: string | undefined, what: string) => {
  const n = Number(v);
  if (!Number.isFinite(n)) die(`--${what} lipsește sau nu e număr`);
  return n;
};
function die(msg: string): never {
  console.error(`✖ ${msg}`);
  process.exit(1);
}
function need(v: string | undefined, what: string): string {
  if (!v) die(`Lipsește --${what}`);
  return v;
}
/** Rulează o mutație doar cu --yes; altfel afișează ce AR face. */
async function mutate<T>(label: string, run: () => Promise<{ success: boolean; data?: T; error?: string }>) {
  if (!APPLY) {
    console.log(`DRY-RUN — aș executa: ${label}`);
    console.log(`   Adaugă --yes ca să aplic.`);
    return;
  }
  const res = await run();
  if (!res.success) die(`${label} → EȘUAT: ${res.error}`);
  console.log(`✅ ${label} → OK`, res.data ?? "");
}

async function brandByCode(code: string) {
  const b = await prisma.brand.findFirst({
    where: { brandCode: code.toUpperCase() },
    select: { id: true, brandCode: true, name: true },
  });
  if (!b) die(`Brand necunoscut: ${code}`);
  return b;
}

async function main() {
  // ── READ ────────────────────────────────────────────────────────
  if (cmd === "brands") {
    const bs = await prisma.brand.findMany({
      where: { isActive: true },
      select: { brandCode: true, name: true, productionLeadTimeDays: true, productionRoundingUnit: true },
      orderBy: { brandCode: "asc" },
    });
    for (const b of bs)
      console.log(`  ${b.brandCode.padEnd(14)} ${b.name.padEnd(20)} lead=${b.productionLeadTimeDays}z round=${b.productionRoundingUnit}`);
    return;
  }

  if (cmd === "necesar" && sub === "list") {
    const brand = opt("brand") ? await brandByCode(opt("brand")!) : null;
    const res = await ProductionRequirementService.list({
      brandId: brand?.id,
      status: opt("status") as never,
      take: Number(opt("take", "30")),
    });
    const rows = (res as { data?: { items?: unknown[] } }).data?.items ?? (res as never as { items?: unknown[] }).items ?? [];
    console.log(JSON.stringify(rows, null, 2).slice(0, 6000));
    return;
  }

  if (cmd === "necesar" && sub === "show") {
    const r = await ProductionRequirementService.get(need(opt("id"), "id"));
    console.log(JSON.stringify(r, null, 2).slice(0, 8000));
    return;
  }

  if (cmd === "livrare" && sub === "list") {
    const r = await DeliveryService.list({ status: opt("status") as never, take: Number(opt("take", "30")) });
    console.log(JSON.stringify(r, null, 2).slice(0, 6000));
    return;
  }
  if (cmd === "livrare" && sub === "show") {
    const r = await DeliveryService.get(need(opt("id"), "id"));
    console.log(JSON.stringify(r, null, 2).slice(0, 8000));
    return;
  }
  if (cmd === "livrare" && sub === "eligible") {
    const r = await DeliveryService.listEligibleNecesarItems({});
    console.log(JSON.stringify(r, null, 2).slice(0, 8000));
    return;
  }

  // ── NECESAR (mutații) ───────────────────────────────────────────
  if (cmd === "necesar" && sub === "generate") {
    const brand = await brandByCode(need(opt("brand"), "brand"));
    const lookback = Number(opt("lookback", "60"));
    const fdays = Number(opt("forecast-days", "60"));
    const rd = await ForecastService.readiness(brand.id);
    if (!rd.success) die(`Readiness: ${rd.error}`);
    if (!rd.data.ready) {
      console.error(`⛔ ${rd.data.unmappedVariants} variante NEMAPATE la ${brand.name} — mapează-le întâi.`);
      for (const u of rd.data.unmappedSample.slice(0, 8)) console.error(`   - ${u.sku ?? "(fără SKU)"} ${u.productTitle}`);
      process.exit(1);
    }
    const run = await ForecastService.runForBrand({
      brandId: brand.id,
      lookbackDays: lookback,
      forecastDays: fdays,
      roundingUnit: opt("round") ? Number(opt("round")) : undefined,
    });
    if (!run.success) die(`Forecast: ${run.error}`);
    const rows = await prisma.forecastRow.findMany({
      where: { forecastRunId: run.data.runId, suggestedQty: { gt: 0 } },
      orderBy: { suggestedQty: "desc" },
    });
    const total = rows.reduce((s, x) => s + x.suggestedQty, 0);
    console.log(`\n${brand.name}: ${rows.length} linii, total ${total} buc (lookback ${lookback}z / acoperire ${fdays}z)\n`);
    for (const x of rows)
      console.log(
        `  ${(x.sku ?? "—").padEnd(12)} ${x.productTitle.slice(0, 34).padEnd(35)} stoc ${String(x.onHand).padStart(5)} ` +
          `în prod ${String(x.pendingIncoming).padStart(5)} → SUGERAT ${String(x.suggestedQty).padStart(5)}`,
      );
    await mutate(`creez Necesar DRAFT pentru ${brand.brandCode} (${rows.length} linii, ${total} buc)`, () =>
      ProductionRequirementService.create({
        brandId: brand.id,
        forecastRunId: run.data.runId,
        lookbackDays: lookback,
        forecastDays: fdays,
        title: opt("title") ?? undefined,
        notes: opt("notes") ?? undefined,
        items: rows.map((x) => ({ variantId: x.variantId, requestedQty: x.suggestedQty })),
      }),
    );
    return;
  }

  if (cmd === "necesar" && sub === "create") {
    const brand = await brandByCode(need(opt("brand"), "brand"));
    await mutate(`creez Necesar DRAFT gol pentru ${brand.brandCode}`, () =>
      ProductionRequirementService.create({
        brandId: brand.id,
        title: opt("title") ?? undefined,
        notes: opt("notes") ?? undefined,
      }),
    );
    return;
  }

  if (cmd === "necesar" && sub === "add-line") {
    const id = need(opt("id"), "id"), variantId = need(opt("variant"), "variant"), qty = num(opt("qty"), "qty");
    await mutate(`adaug linie (variant ${variantId}, qty ${qty}) pe ${id}`, () =>
      ProductionRequirementService.addItem({ productionRequirementId: id, variantId, requestedQty: qty }),
    );
    return;
  }
  if (cmd === "necesar" && sub === "set-qty") {
    const itemId = need(opt("item"), "item"), qty = num(opt("qty"), "qty");
    await mutate(`setez qty=${qty} pe linia ${itemId}`, () =>
      ProductionRequirementService.updateItemQty({ itemId, requestedQty: qty }),
    );
    return;
  }
  if (cmd === "necesar" && sub === "remove-line") {
    const itemId = need(opt("item"), "item");
    await mutate(`șterg linia ${itemId}`, () => ProductionRequirementService.removeItem({ itemId }));
    return;
  }
  if (cmd === "necesar" && sub === "approve") {
    const id = need(opt("id"), "id");
    await mutate(`APROB Necesarul ${id}`, () => ProductionRequirementService.approve({ productionRequirementId: id }));
    return;
  }
  if (cmd === "necesar" && sub === "cancel") {
    const id = need(opt("id"), "id"), reason = need(opt("reason"), "reason");
    await mutate(`ANULEZ Necesarul ${id} (motiv: ${reason})`, () =>
      ProductionRequirementService.cancel({ productionRequirementId: id, reason }),
    );
    return;
  }
  if (cmd === "necesar" && sub === "mark") {
    const itemId = need(opt("item"), "item"), qty = num(opt("qty"), "qty");
    await mutate(`marchez FABRICAT ${qty} buc pe linia ${itemId}`, () =>
      ProductionRequirementService.markManufactured({ itemId, manufacturedQty: qty }),
    );
    return;
  }
  if (cmd === "necesar" && sub === "cancel-line") {
    const itemId = need(opt("item"), "item"), reason = need(opt("reason"), "reason");
    await mutate(`anulez linia ${itemId} (motiv: ${reason})`, () =>
      ProductionRequirementService.cancelLine({ itemId, reason }),
    );
    return;
  }

  // ── LIVRARE (mutații) ───────────────────────────────────────────
  if (cmd === "livrare" && sub === "create") {
    const items = opt("items")?.split(",").map((s) => s.trim()).filter(Boolean);
    await mutate(`creez Livrare DRAFT${items ? ` cu ${items.length} linii` : ""}`, () =>
      DeliveryService.create({ notes: opt("notes") ?? undefined, productionRequirementItemIds: items }),
    );
    return;
  }
  if (cmd === "livrare" && sub === "add-item") {
    const deliveryId = need(opt("id"), "id"), pri = need(opt("pri-item"), "pri-item");
    await mutate(`adaug linia Necesar ${pri} pe livrarea ${deliveryId}`, () =>
      DeliveryService.addItem({ deliveryId, productionRequirementItemId: pri }),
    );
    return;
  }
  if (cmd === "livrare" && sub === "remove-item") {
    const deliveryItemId = need(opt("item"), "item");
    await mutate(`scot linia ${deliveryItemId} din livrare`, () => DeliveryService.removeItem({ deliveryItemId }));
    return;
  }
  if (cmd === "livrare" && sub === "approve") {
    const deliveryId = need(opt("id"), "id");
    await mutate(`APROB livrarea ${deliveryId}`, () => DeliveryService.approve({ deliveryId }));
    return;
  }
  if (cmd === "livrare" && sub === "cancel") {
    const deliveryId = need(opt("id"), "id"), reason = need(opt("reason"), "reason");
    await mutate(`ANULEZ livrarea ${deliveryId} (motiv: ${reason})`, () =>
      DeliveryService.cancel({ deliveryId, reason }),
    );
    return;
  }
  if (cmd === "livrare" && sub === "receive") {
    const deliveryId = need(opt("id"), "id");
    const items = opt("items")?.split(",").map((s) => s.trim()).filter(Boolean);
    console.log("⚠️  RECEPȚIE — adaugă stocul în SHOPIFY (inventoryAdjustQuantities).");
    console.log("⚠️  NU e reversibilă din Scentum (corecția se face din Shopify admin).");
    if (!APPLY || !has("confirm-shopify")) {
      console.log("\nDRY-RUN. Cere AMBELE: --yes --confirm-shopify");
      return;
    }
    const res = await receiveDelivery({ deliveryId, deliveryItemIds: items });
    console.log("Rezultat recepție:", JSON.stringify(res, null, 2).slice(0, 4000));
    return;
  }

  console.log(String.raw`scentum-cli — comenzi:
  brands
  necesar list|show|generate|create|add-line|set-qty|remove-line|approve|cancel|mark|cancel-line
  livrare list|show|eligible|create|add-item|remove-item|approve|cancel|receive
Mutațiile sunt DRY-RUN implicit → adaugă --yes. Recepția cere și --confirm-shopify.
Ex: npx tsx scripts/scentum-cli.ts necesar generate --brand ESTEBAN --yes`);
}

main()
  .catch((e) => {
    console.error(e);
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
