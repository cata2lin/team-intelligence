/** Shim CLI: `redirect()` din server actions n-are sens în terminal → aruncă explicit. */
export function redirect(url: string): never {
  throw new Error(`Acțiunea a cerut redirect("${url}") — probabil nu ești autentificat/autorizat.`);
}
export function notFound(): never { throw new Error("notFound()"); }
