import { router } from "expo-router";
import { FlatList, Pressable, RefreshControl, Text, View } from "react-native";

import type { KnownGroup } from "../../lib/types";
import { Card, Chip, Empty, ErrorLine, s, useColors } from "../../lib/ui";
import { useLoad } from "../../lib/useLoad";

export default function Known() {
  const c = useColors();
  const { data, error, refreshing, reload } = useLoad<{ items: KnownGroup[] }>("/support/app/known-issues");

  return (
    <FlatList
      data={data?.items ?? []}
      keyExtractor={(g) => g.key}
      contentContainerStyle={{ padding: 16 }}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={reload} tintColor={c.brand} />}
      ListHeaderComponent={
        <>
          <ErrorLine text={error} />
          <Text style={{ color: c.sub, marginBottom: 12 }}>
            Open problems grouped by cause. Write the fix once - it shows on every job with the same cause.
          </Text>
        </>
      }
      ListEmptyComponent={data ? <Empty text="No open problems and no saved fixes yet." /> : null}
      renderItem={({ item: g }) => (
        <Pressable onPress={() => router.push({ pathname: "/known/[key]", params: { key: g.key, title: g.title, fix: g.fix ?? "" } })}>
          <Card tone={g.count ? (g.fix ? "blue" : "amber") : undefined}>
            <View style={[s.row, { justifyContent: "space-between" }]}>
              <Text style={{ color: c.text, fontWeight: "800", fontSize: 15, flex: 1 }}>{g.title}</Text>
              <Chip label={`${g.count} job${g.count === 1 ? "" : "s"}`} tone={g.count ? "red" : undefined} />
            </View>
            {g.fix ? (
              <Text style={{ color: c.green, marginTop: 8 }} numberOfLines={3}>💡 {g.fix}</Text>
            ) : (
              <Text style={{ color: c.amber, marginTop: 8 }}>No fix written yet - tap to add one</Text>
            )}
            {g.jobs.length > 0 && (
              <Text style={{ color: c.sub, fontSize: 12, marginTop: 8 }}>
                {g.jobs.map((j) => `${j.reference} (${j.client})`).join(", ")}
              </Text>
            )}
          </Card>
        </Pressable>
      )}
    />
  );
}
