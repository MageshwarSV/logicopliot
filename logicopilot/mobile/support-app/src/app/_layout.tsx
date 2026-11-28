import * as Notifications from "expo-notifications";
import { Stack, router, useSegments } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { ActivityIndicator, View } from "react-native";

import { AuthProvider, useAuth } from "../lib/auth";
import { useColors } from "../lib/ui";

function openFromNotification(data: unknown) {
  const jobId = (data as { job_id?: string } | undefined)?.job_id;
  router.push(jobId ? `/job/${jobId}` : "/issues");
}

function Gate() {
  const { ready, signedIn } = useAuth();
  const segments = useSegments();
  const c = useColors();
  const last = Notifications.useLastNotificationResponse();

  useEffect(() => {
    if (!ready) return;
    const onLogin = segments[0] === "login";
    if (!signedIn && !onLogin) router.replace("/login");
    else if (signedIn && onLogin) router.replace("/");
  }, [ready, signedIn, segments]);

  // Tapping an alert opens that job (or the Needs action list when it is about several).
  useEffect(() => {
    if (signedIn && last?.notification) openFromNotification(last.notification.request.content.data);
  }, [signedIn, last]);

  if (!ready) {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: c.bg }}>
        <ActivityIndicator color={c.brand} />
      </View>
    );
  }
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: c.card },
        headerTintColor: c.text,
        contentStyle: { backgroundColor: c.bg },
      }}
    >
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      <Stack.Screen name="login" options={{ headerShown: false }} />
      <Stack.Screen name="job/[id]" options={{ title: "Job" }} />
      <Stack.Screen name="known/[key]" options={{ title: "Known issue" }} />
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <AuthProvider>
      <StatusBar style="auto" />
      <Gate />
    </AuthProvider>
  );
}
