import { Stack, router, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import { Alert, Pressable, RefreshControl, ScrollView, Text, TextInput, View } from "react-native";

import { api } from "../../lib/api";
import { OUTCOMES, type JobDetail } from "../../lib/types";
import { Button, Card, Chip, ErrorLine, KIND_LABEL, ago, agoIso, s, useColors, when } from "../../lib/ui";
import { useLoad } from "../../lib/useLoad";

export default function Job() {
  const c = useColors();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: j, error, refreshing, reload } = useLoad<JobDetail>(`/support/app/jobs/${id}`);
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const run = async (what: string, fn: () => Promise<unknown>, done: string) => {
    setBusy(what);
    setActionError(null);
    try {
      await fn();
      Alert.alert("Done", done);
      setNote("");
      setOutcome(null);
      reload();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const confirmRetry = (what: "erp" | "extraction") =>
    Alert.alert(
      what === "erp" ? "Retry ERP entry?" : "Retry AI reading?",
      what === "erp" ? "The robot will enter this job in the ERP again." : "The documents will be read again.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Retry",
          onPress: () => run(what, () => api(what === "erp" ? `/jobs/${id}/gk2/approve` : `/jobs/${id}/extract`, { method: "POST" }),
            "Retry started. Pull down in a minute to see the result."),
        },
      ],
    );

  const input = { borderWidth: 1, borderColor: c.line, backgroundColor: c.card, color: c.text,
    borderRadius: 12, padding: 12, fontSize: 15, minHeight: 70, textAlignVertical: "top" as const };

  return (
    <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 48 }} keyboardShouldPersistTaps="handled"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={reload} tintColor={c.brand} />}>
      <Stack.Screen options={{ title: j?.reference ?? "Job" }} />
      <ErrorLine text={error} />
      {j && (
        <>
          <Text style={[s.h1, { color: c.text }]}>{j.reference}</Text>
          <Text style={{ color: c.sub, marginBottom: 12 }}>
            {j.client}{j.type ? ` · ${j.type}` : ""}{j.stage ? ` · ${j.stage}` : ""}
          </Text>

          {j.reason ? (
            <Card tone={j.handled ? "green" : j.problem === "erp_failed" ? "red" : "amber"}>
              <View style={[s.row, { flexWrap: "wrap", marginBottom: 6 }]}>
                {j.problem && <Chip label={KIND_LABEL[j.problem] ?? j.problem} tone={j.problem === "erp_failed" ? "red" : "amber"} />}
                {j.handled && <Chip label="✓ Handled" tone="green" />}
                {j.age_minutes != null && <Chip label={ago(j.age_minutes)} />}
              </View>
              <Text style={{ color: c.sub, fontSize: 12, fontWeight: "700" }}>WHY</Text>
              <Text style={{ color: c.text, fontSize: 16, marginTop: 2 }}>{j.reason}</Text>
            </Card>
          ) : (
            <Card tone="green"><Text style={{ color: c.green }}>No open problem on this job.</Text></Card>
          )}

          {j.corrections.length > 0 && (
            <Card>
              <Text style={[s.h2, { color: c.text, marginBottom: 6 }]}>What the ERP wants corrected</Text>
              {j.corrections.map((x, i) => (
                <View key={i} style={{ marginTop: 8 }}>
                  <Text style={{ color: c.text, fontWeight: "700" }}>{i + 1}. {x.field}</Text>
                  {!!x.value && <Text style={{ color: c.sub }}>Sent: {x.value}</Text>}
                  {!!x.needs && <Text style={{ color: c.text }}>{x.needs}</Text>}
                  {!!x.example && <Text style={{ color: c.sub }}>Example: {x.example}</Text>}
                  {!!x.suggest && <Text style={{ color: c.green }}>Suggestion: {x.suggest}</Text>}
                </View>
              ))}
            </Card>
          )}

          {j.issue_key && (
            <Pressable onPress={() => router.push({ pathname: "/known/[key]",
              params: { key: j.issue_key!, title: j.issue_title ?? "", fix: j.known_fix ?? "" } })}>
              <Card tone={j.known_fix ? "blue" : undefined}>
                <Text style={{ color: c.sub, fontSize: 12, fontWeight: "700" }}>KNOWN ISSUE · {j.issue_title}</Text>
                <Text style={{ color: j.known_fix ? c.text : c.amber, marginTop: 4 }}>
                  {j.known_fix ? `💡 ${j.known_fix}` : "No fix written yet for this cause - tap to add one"}
                </Text>
              </Card>
            </Pressable>
          )}

          {(j.can_retry_erp || j.can_retry_extraction || (j.reason && !j.handled)) && (
            <View style={[s.row, { marginBottom: 12 }]}>
              {j.can_retry_erp && <Button label="↻ Retry ERP" onPress={() => confirmRetry("erp")} busy={busy === "erp"} />}
              {j.can_retry_extraction && <Button label="↻ Retry AI" onPress={() => confirmRetry("extraction")} busy={busy === "extraction"} />}
              {j.reason && !j.handled && (
                <Button label="✓ Mark handled" kind="secondary" busy={busy === "handled"}
                  onPress={() => run("handled", () => api(`/support/issues/${id}/handled`, { method: "POST", body: { text: note } }),
                    "Marked handled. It comes back if the job fails again.")} />
              )}
            </View>
          )}
          <ErrorLine text={actionError} />

          <Card>
            <Text style={[s.h2, { color: c.text, marginBottom: 8 }]}>Add a note</Text>
            <View style={[s.row, { flexWrap: "wrap", marginBottom: 10 }]}>
              {OUTCOMES.map((o) => (
                <Chip key={o.key} label={o.label} tone="brand" active={outcome === o.key}
                  onPress={() => setOutcome(outcome === o.key ? null : o.key)} />
              ))}
            </View>
            <TextInput style={input} multiline placeholder="What did you check or do? (called the customer, ERP was down...)"
              placeholderTextColor={c.sub} value={note} onChangeText={setNote} />
            <View style={[s.row, { marginTop: 10 }]}>
              <Button label="Save note" busy={busy === "note"} disabled={!note.trim() && !outcome}
                onPress={() => run("note", () => api(`/support/app/jobs/${id}/notes`, { method: "POST", body: { text: note, outcome } }),
                  "Note saved. The team can see it.")} />
            </View>
          </Card>

          <Text style={[s.h2, { color: c.text, marginVertical: 8 }]}>Team notes</Text>
          {j.notes.length === 0 && <Text style={{ color: c.sub, marginBottom: 12 }}>No one has written on this job yet.</Text>}
          {j.notes.map((n) => (
            <Card key={n.id}>
              <View style={[s.row, { flexWrap: "wrap" }]}>
                {n.outcome_label && <Chip label={n.outcome_label} tone="blue" />}
                <Text style={{ color: c.sub, fontSize: 12 }}>{n.by ?? "System"} · {when(n.at)} · {agoIso(n.at)}</Text>
              </View>
              <Text style={{ color: c.text, marginTop: 6 }}>{n.text}</Text>
            </Card>
          ))}

          <Text style={[s.h2, { color: c.text, marginVertical: 8 }]}>Timeline</Text>
          <Card>
            {j.timeline.map((t, i) => (
              <View key={i} style={[s.row, { justifyContent: "space-between", paddingVertical: 5 }]}>
                <Text style={{ color: c.text }}>{t.label}</Text>
                <Text style={{ color: c.sub }}>{when(t.at)}</Text>
              </View>
            ))}
          </Card>

          {!!j.erp_message && (
            <>
              <Text style={[s.h2, { color: c.text, marginVertical: 8 }]}>ERP&apos;s own message</Text>
              <Card><Text style={{ color: c.sub, fontSize: 13 }}>{j.erp_message}</Text></Card>
            </>
          )}

          {j.events.length > 0 && (
            <>
              <Text style={[s.h2, { color: c.text, marginVertical: 8 }]}>Recent history</Text>
              <Card>
                {j.events.map((e, i) => (
                  <View key={i} style={{ paddingVertical: 5 }}>
                    <Text style={{ color: c.sub, fontSize: 12 }}>{when(e.at)} · {e.status}</Text>
                    {!!e.note && <Text style={{ color: c.text }}>{e.note}</Text>}
                  </View>
                ))}
              </Card>
            </>
          )}
        </>
      )}
    </ScrollView>
  );
}
