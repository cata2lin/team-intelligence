/**
 * Shim de AUTENTIFICARE pentru CLI (NU face parte din app — e mapat doar prin tsconfig.cli.json).
 *
 * Acțiunile Scentum cheamă `requireAuth()` → `auth()` → `headers()` (next-auth), care există DOAR
 * într-un request Next. Din CLI n-avem request → ar crăpa. Aici întoarcem o sesiune REALĂ,
 * a unui utilizator REAL din tabela `users`, ca **audit-ul să rămână corect**
 * (acțiunile scriu `session.user.id` în createdBy/updatedBy/audit log).
 *
 * Cine ești = env `SCENTUM_USER` (email din Scentum). Fără el, CLI-ul refuză mutațiile.
 */
import { prisma } from "@/lib/prisma";
import type { UserRole } from "@/generated/prisma/client";

type CliSession = { user: { id: string; email: string; name: string; role: UserRole } };
let cached: CliSession | null = null;

export async function getSession(): Promise<CliSession | null> {
  if (cached) return cached;
  const email = process.env.SCENTUM_USER?.trim();
  if (!email) {
    throw new Error(
      "SCENTUM_USER lipsește. Pune în .env emailul TĂU din Scentum (ex. SCENTUM_USER=andreea.popa@scentumlabs.ro) — " +
        "acțiunile se semnează cu el în audit.",
    );
  }
  const u = await prisma.user.findUnique({
    where: { email },
    select: { id: true, email: true, fullName: true, role: true, isActive: true },
  });
  if (!u) throw new Error(`SCENTUM_USER="${email}" nu există în Scentum (tabela users).`);
  if (!u.isActive) throw new Error(`Userul ${email} e dezactivat în Scentum.`);
  cached = { user: { id: u.id, email: u.email, name: u.fullName, role: u.role } };
  return cached;
}

export async function requireAuth(): Promise<CliSession> {
  const s = await getSession();
  if (!s) throw new Error("Neautentificat.");
  return s;
}

export async function requireRole(role: UserRole): Promise<CliSession> {
  const s = await requireAuth();
  if (s.user.role !== role) {
    throw new Error(`Acțiunea cere rolul ${role}, dar ${s.user.email} e ${s.user.role}.`);
  }
  return s;
}

/** `auth()` — pentru acțiunile care importă direct din "@/lib/auth" (ex. product-files). */
export const auth = getSession;
