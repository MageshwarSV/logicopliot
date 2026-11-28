import { router, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import { ScrollView, Text, TextInput, View } from "react-native";

import { api } from "../../lib/api";
import { Button, ErrorLine, s, useColors } from "../../lib/ui";

export default function KnownEdit() {
  const c = useColors();
  const p = useLocalSearchParams<{ key: string; title?: string; fix?: string }>();
  const [title, setTitle] = useState(p.title ?? "");
  const [fix, setFix] = useState(p.fix ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api(`/support/app/known-issues/${encodeURIComponent(p.key)}`, { method: "PUT", body: { title, fix } });
      router.back();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const input = { borderWidth: 1, borderColor: c.line, backgroundColor: c.card, color: c.text,
    borderRadius: 12, padding: 12, fontSize: 15, marginBottom: 14 } as const;

  return (
    <ScrollView contentContainerStyle={{ padding: 16 }} keyboardShouldPersistTaps="handled">
      <Text style={{ color: c.sub, marginBottom: 4 }}>Name of this problem</Text>
      <TextInput style={input} value={title} onChangeText={setTitle} />
      <Text style={{ color: c.sub, marginBottom: 4 }}>
        How to fix it - shown on every job with this cause (what to check, who to call, when to retry)
      </Text>
      <TextInput style={[input, { minHeight: 160, textAlignVertical: "top" }]} multiline value={fix} onChangeText={setFix}
        placeholder="e.g. ERP is down for maintenance after 9 PM - retry in the morning. If it fails again, call ..."
        placeholderTextColor={c.sub} />
      <Text style={{ color: c.sub, fontSize: 12, marginBottom: 14 }}>Cause key: {p.key}</Text>
      <ErrorLine text={error} />
      <View style={s.row}>
        <Button label="Save fix" onPress={save} busy={busy} />
      </View>
    </ScrollView>
  );
}
