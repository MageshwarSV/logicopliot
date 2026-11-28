// Push alerts: ask permission, get this phone's Expo push token, tell the server.
// Needs a real phone and an EAS project id (app.json extra.eas.projectId, set by `eas init`).
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import { api } from "./api";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

export async function registerForPush(): Promise<{ token: string | null; problem: string | null }> {
  if (!Device.isDevice) return { token: null, problem: "Alerts work on a real phone only." };
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("alerts", {
      name: "Cargora alerts",
      importance: Notifications.AndroidImportance.HIGH,
      sound: "default",
    });
  }
  let { status } = await Notifications.getPermissionsAsync();
  if (status !== "granted") status = (await Notifications.requestPermissionsAsync()).status;
  if (status !== "granted") return { token: null, problem: "Notifications are turned off for this app in phone settings." };

  const projectId =
    (Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined)?.eas?.projectId ??
    Constants.easConfig?.projectId;
  if (!projectId) return { token: null, problem: "This build has no EAS project id, so it cannot get alerts." };
  try {
    const token = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
    await api("/support/app/devices", { method: "POST", body: { token, platform: Platform.OS } });
    return { token, problem: null };
  } catch (e) {
    return { token: null, problem: `Could not turn on alerts: ${(e as Error).message}` };
  }
}
