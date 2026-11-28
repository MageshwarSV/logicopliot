import { router } from "expo-router";
import { useMemo, useState } from "react";
import { FlatList, Pressable, RefreshControl, Text, View } from "react-native";

import type { Issue } from "../../lib/types";
import { Card, Chip, Empty, ErrorLine, KIND_LABEL, ago, s, useColors } from "../../lib/ui";
import { useLoad } from "../../lib/useLoad";

type Filter = "open" | "erp" | "ai" | "handled";

function status(i: Issue): { label: string; tone: "red" | "amber" | "green" | "blue" } {
  if (i.handled) return { label: "✓ Handled", tone: "green" };
  if (i.last_note) return { label: `👀 ${i.last_note.outcome_label ?? "Note"} · ${i.last_note.by ?? ""}`.trim(), tone: "blue" };
  return { label: "🆕 New", tone: "red" };
}

export default function Issues() {
  const c = useColors();
  const { data, error, refreshing, reload } = useLoad<{ open: number; items: Issue[] }>("/support/app/issues");
  const [filter, setFilter] = useState<Filter>("open");

  const items = useMemo(() => (data?.items ?? []).filter((i) =>
    filter === "handled" ? i.handled
      : i.handled ? false
      : filter === "erp" ? i.kind.startsWith("erp")
      : filter === "ai" ? i.kind.startsWith("extraction")
      : true), [data, filter]);

  return (
    <FlatList
      data={items}
      keyExtractor={(i) => i.job_id}
      contentContainerStyle={{ padding: 16 }}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={reload} tintColor={c.brand} />}
      ListHeaderComponent={
        <>
          <ErrorLine text={error} />
          <View style={[s.row, { flexWrap: "wrap", marginBottom: 12 }]}>
            {([["open", `Open ${data?.open ?? ""}`], ["erp", "ERP"], ["ai", "AI"], ["handled", "Handled"]] as const).map(([k, l]) => (
              <Chip key={k} label={l} tone="brand" active={filter === k} onPress={() => setFilter(k)} />
            ))}
          </View>
        </>
      }
      ListEmptyComponent={data ? <Empty text={filter === "handled" ? "Nothing handled yet." : "Nothing needs action. 🎉"} /> : null}
      renderItem={({ item: i, index }) => {
        const st = status(i);
        return (
          <Pressable onPress={() => router.push(`/job/${i.job_id}`)}>
            <Card tone={i.handled ? undefined : i.kind === "erp_failed" ? "red" : "amber"}>
              <View style={[s.row, { justifyContent: "space-between" }]}>
                <Text style={{ color: c.text, fontWeight: "800", fontSize: 15 }}>{index + 1}. {i.reference}</Text>
                <Text style={{ color: c.sub, fontSize: 12 }}>{ago(i.age_minutes)}</Text>
              </View>
              <Text style={{ color: c.sub, marginTop: 2 }}>{i.client}{i.type ? ` · ${i.type}` : ""}</Text>
              <Text style={{ color: c.text, marginTop: 8, fontSize: 15 }}>{i.reason}</Text>
              {i.known_fix && (
                <Text style={{ color: c.green, marginTop: 6 }} numberOfLines={2}>💡 {i.known_fix}</Text>
              )}
              <View style={[s.row, { flexWrap: "wrap", marginTop: 10 }]}>
                <Chip label={KIND_LABEL[i.kind] ?? i.kind} tone={i.kind === "erp_failed" ? "red" : "amber"} />
                <Chip label={st.label} tone={st.tone} />
              </View>
            </Card>
          </Pressable>
        );
      }}
    />
  );
}
