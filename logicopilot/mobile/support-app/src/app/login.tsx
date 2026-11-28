import { useEffect, useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, Text, TextInput, View } from "react-native";

import { DEFAULT_SERVER, loadSession } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Button, ErrorLine, s, useColors } from "../lib/ui";

export default function Login() {
  const c = useColors();
  const { signIn } = useAuth();
  const [server, setServer] = useState(DEFAULT_SERVER);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showServer, setShowServer] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadSession().then((x) => {
      setServer(x.server);
      setEmail(x.email);
    });
  }, []);

  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      await signIn(server, email, password);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const input = { borderWidth: 1, borderColor: c.line, backgroundColor: c.card, color: c.text,
    borderRadius: 12, padding: 14, fontSize: 16, marginBottom: 12 } as const;

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: c.bg }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView contentContainerStyle={{ padding: 24, paddingTop: 96 }} keyboardShouldPersistTaps="handled">
        <View style={[s.row, { marginBottom: 6 }]}>
          <View style={{ width: 44, height: 44, borderRadius: 12, backgroundColor: c.brand, alignItems: "center", justifyContent: "center" }}>
            <Text style={{ color: "#fff", fontSize: 22, fontWeight: "800" }}>C</Text>
          </View>
          <Text style={[s.h1, { color: c.text }]}>Cargora Support</Text>
        </View>
        <Text style={{ color: c.sub, marginBottom: 28, fontSize: 15 }}>
          Production Support login - every customer&apos;s jobs, what failed and why.
        </Text>

        <TextInput style={input} placeholder="Email" placeholderTextColor={c.sub} autoCapitalize="none"
          keyboardType="email-address" autoComplete="email" value={email} onChangeText={setEmail} />
        <TextInput style={input} placeholder="Password" placeholderTextColor={c.sub} secureTextEntry
          autoComplete="password" value={password} onChangeText={setPassword} onSubmitEditing={go} />

        {showServer ? (
          <>
            <Text style={{ color: c.sub, marginBottom: 6 }}>Server (production, or SIT for testing)</Text>
            <TextInput style={input} autoCapitalize="none" autoCorrect={false} keyboardType="url"
              value={server} onChangeText={setServer} />
          </>
        ) : (
          <Pressable onPress={() => setShowServer(true)} hitSlop={8}>
            <Text style={{ color: c.sub, marginBottom: 16 }}>Server: {server.replace(/^https?:\/\//, "")} · change</Text>
          </Pressable>
        )}

        <ErrorLine text={error} />
        <View style={s.row}>
          <Button label="Log in" onPress={go} busy={busy} disabled={!email || !password} />
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
