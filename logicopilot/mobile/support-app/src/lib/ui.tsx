// Shared colours, time formatting and small building blocks. Light and dark follow the phone.
import { ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View, useColorScheme } from "react-native";

const light = {
  bg: "#F6F7FB", card: "#FFFFFF", text: "#0F172A", sub: "#64748B", line: "#E2E8F0",
  brand: "#4F46E5", brandSoft: "#EEF2FF",
  red: "#DC2626", redSoft: "#FEF2F2", amber: "#B45309", amberSoft: "#FFFBEB",
  green: "#047857", greenSoft: "#ECFDF5", blue: "#1D4ED8", blueSoft: "#EFF6FF",
};
const dark: typeof light = {
  bg: "#0B1020", card: "#151B2E", text: "#F1F5F9", sub: "#94A3B8", line: "#26304A",
  brand: "#818CF8", brandSoft: "#1E1B4B",
  red: "#F87171", redSoft: "#3B1219", amber: "#FBBF24", amberSoft: "#3A2A08",
  green: "#34D399", greenSoft: "#0B2E24", blue: "#93C5FD", blueSoft: "#10223F",
};
export type Palette = typeof light;

export function useColors(): Palette {
  return useColorScheme() === "dark" ? dark : light;
}

const IST = "Asia/Kolkata";

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    timeZone: IST, day: "2-digit", month: "short", hour: "numeric", minute: "2-digit", hour12: true,
  });
}

export function ago(minutes: number | null | undefined): string {
  if (minutes == null) return "";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const h = Math.floor(minutes / 60);
  if (h < 24) return `${h} h ${minutes % 60} min ago`;
  const d = Math.floor(h / 24);
  return `${d} day${d > 1 ? "s" : ""} ago`;
}

export function agoIso(iso: string | null | undefined): string {
  if (!iso) return "";
  return ago(Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000)));
}

export function Card({ children, tone }: { children: ReactNode; tone?: "red" | "amber" | "green" | "blue" }) {
  const c = useColors();
  const bg = tone ? c[`${tone}Soft` as const] : c.card;
  const border = tone ? c[tone] : c.line;
  return (
    <View style={[s.card, { backgroundColor: bg, borderColor: border + (tone ? "55" : "") }]}>{children}</View>
  );
}

export function Chip({ label, tone, onPress, active }: {
  label: string; tone?: "red" | "amber" | "green" | "blue" | "brand"; onPress?: () => void; active?: boolean;
}) {
  const c = useColors();
  const fg = tone ? c[tone] : c.sub;
  const bg = active ? fg : tone ? c[`${tone}Soft` as const] : c.bg;
  return (
    <Pressable onPress={onPress} disabled={!onPress} hitSlop={6}
      style={[s.chip, { backgroundColor: bg, borderColor: fg + "55" }]}>
      <Text style={{ color: active ? c.card : fg, fontSize: 12, fontWeight: "600" }}>{label}</Text>
    </Pressable>
  );
}

export function Button({ label, onPress, kind = "primary", busy, disabled }: {
  label: string; onPress: () => void; kind?: "primary" | "secondary" | "danger"; busy?: boolean; disabled?: boolean;
}) {
  const c = useColors();
  const bg = kind === "primary" ? c.brand : kind === "danger" ? c.red : c.card;
  const fg = kind === "secondary" ? c.text : "#FFFFFF";
  return (
    <Pressable onPress={onPress} disabled={busy || disabled}
      style={({ pressed }) => [s.btn, { backgroundColor: bg, borderColor: kind === "secondary" ? c.line : bg,
        opacity: disabled ? 0.5 : pressed ? 0.85 : 1 }]}>
      {busy ? <ActivityIndicator color={fg} /> : <Text style={{ color: fg, fontWeight: "700", fontSize: 15 }}>{label}</Text>}
    </Pressable>
  );
}

export function Empty({ text }: { text: string }) {
  const c = useColors();
  return <Text style={{ color: c.sub, textAlign: "center", padding: 32, fontSize: 15 }}>{text}</Text>;
}

export function ErrorLine({ text }: { text: string | null }) {
  const c = useColors();
  if (!text) return null;
  return <Text style={{ color: c.red, padding: 12, fontSize: 14 }}>{text}</Text>;
}

export const s = StyleSheet.create({
  card: { borderRadius: 14, borderWidth: 1, padding: 14, marginBottom: 10 },
  chip: { borderRadius: 999, borderWidth: 1, paddingHorizontal: 10, paddingVertical: 4, alignSelf: "flex-start" },
  btn: { borderRadius: 12, borderWidth: 1, paddingVertical: 13, paddingHorizontal: 16, alignItems: "center", justifyContent: "center", flex: 1 },
  h1: { fontSize: 22, fontWeight: "800" },
  h2: { fontSize: 16, fontWeight: "700" },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
});

export const KIND_LABEL: Record<string, string> = {
  erp_failed: "ERP failed",
  erp_slow: "ERP slow",
  extraction_slow: "AI slow",
  extraction_failed: "AI did not finish",
  missing_documents: "Waiting for documents",
};
