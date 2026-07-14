// Shim pt rulare din CLI: revalidatePath/Tag sunt no-op în afara contextului Next.
export function revalidatePath(_p?: string, _t?: string) {}
export function revalidateTag(_t?: string) {}
export function unstable_cache<T extends (...a: any[]) => any>(fn: T) { return fn; }
