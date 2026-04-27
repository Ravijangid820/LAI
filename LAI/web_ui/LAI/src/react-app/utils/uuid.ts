// crypto.randomUUID() is only defined in secure contexts (HTTPS or localhost).
// When the UI is served from a plain-HTTP LAN IP (e.g. http://192.168.x.x:5173)
// the call throws "crypto.randomUUID is not a function" and silently kills
// chat/upload handlers. randomId() returns a stable v4-shaped UUID using
// whatever the browser exposes (Web Crypto.getRandomValues, then Math.random).
export function randomId(): string {
  const c = (typeof globalThis !== "undefined" ? (globalThis as { crypto?: Crypto }).crypto : undefined);

  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (c && typeof c.getRandomValues === "function") {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  // RFC 4122 v4 format
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex: string[] = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
  return (
    hex.slice(0, 4).join("") +
    "-" +
    hex.slice(4, 6).join("") +
    "-" +
    hex.slice(6, 8).join("") +
    "-" +
    hex.slice(8, 10).join("") +
    "-" +
    hex.slice(10, 16).join("")
  );
}
