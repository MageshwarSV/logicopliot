import { router } from "expo-router";
import { Pressable, RefreshControl, ScrollView, Text, View } from "react-native";

import { useAuth } from "../../lib/auth";
import type { Home } from "../../lib/types";
import { Card, Chip, ErrorLine, KIND_LABEL, agoIso, s, useColors, when } from "../../lib/ui";
import { useLoad } from "../../lib/useLoad";

function Tile({ label, value, tone, onPress }: {
  label: string; value: number | string; tone?: "red" | "amber" | "green" | "blue"; onPress?: () => void;
}) {
  const c = useColors();
  return (
    <Pressable onPress={onPress} disabled={!onPress}
      style={{ flexBasis: "47%", flexGrow: 1, backgroundColor: tone ? c[`${tone}Soft` as const] : c.card,
        borderColor: tone ? c[tone] + "55" : c.line, borderWidth: 1, borderRadius: 14, padding: 14 }}>
      <Text style={{ color: c.sub, fontSize: 12, fontWeight: "600", textTransform: "uppercase" }}>{label}</Text>
      <Text style={{ color: tone ? c[tone] : c.text, fontSize: 30, fontWeight: "800", marginTop: 4 }}>{value}</Text>
    </Pressable>
  );
}

export default function HomeScreen() {
  const c = useColors();
  const { email, server, pushProblem, signOut } = useAuth();
  const { data, error, refreshing, reload } = useLoad<Home>("/support/app/home");
  const h = data?.health;
  const allOk = h && h.problems.length === 0;
  const tone = !h ? undefined : h.status === "down" ? "red" : allOk ? "green" : "amber";

  return (
    <ScrollView contentContainerStyle={{ padding: 16 }}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={reload} tintColor={c.brand} />}>
      <ErrorLine text={error} />
      {h && (
        <Card tone={tone}>
          <Text style={[s.h2, { color: c[tone ?? "green"] }]}>
            {allOk ? "✓ All systems OK" : h.status === "down" ? `✕ ${h.down} system${h.down > 1 ? "s" : ""} DOWN` : "! Needs a look"}
          </Text>
          {h.problems.map((p) => (
            <View key={p.key} style={{ marginTop: 8 }}>
              <Text style={{ color: c.text, fontWeight: "700" }}>{p.label} · {p.status === "down" ? "Down" : "Warning"}</Text>
              <Text style={{ color: c.sub, marginTop: 2 }}>{p.detail}</Text>
            </View>
          ))}
          <Text style={{ color: c.sub, fontSize: 12, marginTop: 8 }}>Checked {when(h.checked_at)}</Text>
        </Card>
      )}

      {data && (
        <>
          <Text style={[s.h2, { color: c.text, marginVertical: 8 }]}>Today</Text>
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 10, marginBottom: 14 }}>
            <Tile label="Failed" value={data.today.failed} tone={data.today.failed ? "red" : undefined}
              onPress={() => router.push("/issues")} />
            <Tile label="Delayed" value={data.today.delayed} tone={data.today.delayed ? "amber" : undefined} />
            <Tile label="Stuck in AI" value={data.stuck_in_ai} tone={data.stuck_in_ai ? "amber" : undefined}
              onPress={() => router.push("/issues")} />
            <Tile label="Sent to ERP" value={data.today.submitted} tone="green" />
            <Tile label="Total jobs" value={data.today.total_jobs} />
            <Tile label="Pages" value={data.today.pages} />
          </View>

          <Pressable onPress={() => router.push("/issues")}>
            <Card tone={data.open_problems ? "red" : "green"}>
              <Text style={[s.h2, { color: c.text }]}>
                {data.open_problems ? `${data.open_problems} open problem${data.open_problems > 1 ? "s" : ""} →` : "No open problems"}
              </Text>
              <View style={[s.row, { flexWrap: "wrap", marginTop: 8 }]}>
                {Object.entries(data.open_by_kind).map(([k, n]) => (
                  <Chip key={k} label={`${KIND_LABEL[k] ?? k}: ${n}`} tone={k === "erp_failed" ? "red" : "amber"} />
                ))}
              </View>
            </Card>
          </Pressable>

          <Text style={[s.h2, { color: c.text, marginVertical: 8 }]}>Latest alerts</Text>
          {data.alerts.length === 0 && <Text style={{ color: c.sub, marginBottom: 12 }}>No alerts.</Text>}
          {data.alerts.map((a) => (
            <Card key={a.id}>
              <Text style={{ color: c.text }}>{a.text}</Text>
              <Text style={{ color: c.sub, fontSize: 12, marginTop: 4 }}>{when(a.at)} · {agoIso(a.at)}</Text>
            </Card>
          ))}
        </>
      )}

      {pushProblem && (
        <Card tone="amber"><Text style={{ color: c.amber }}>Alerts: {pushProblem}</Text></Card>
      )}
      <View style={{ marginTop: 16, alignItems: "center", gap: 6 }}>
        <Text style={{ color: c.sub, fontSize: 12 }}>{email} · {server.replace(/^https?:\/\//, "")}</Text>
        <Pressable onPress={signOut} hitSlop={10}>
          <Text style={{ color: c.red, fontWeight: "700" }}>Log out</Text>
        </Pressable>
      </View>
    </ScrollView>
  );
}
