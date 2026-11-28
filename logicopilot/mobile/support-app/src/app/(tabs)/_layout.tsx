import { Tabs } from "expo-router/js-tabs";
import { type ColorValue, Text } from "react-native";

import { useColors } from "../../lib/ui";

function Glyph({ ch, color }: { ch: string; color: ColorValue }) {
  return <Text style={{ color, fontSize: 20 }}>{ch}</Text>;
}

export default function TabsLayout() {
  const c = useColors();
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: c.card },
        headerTintColor: c.text,
        tabBarStyle: { backgroundColor: c.card, borderTopColor: c.line },
        tabBarActiveTintColor: c.brand,
        tabBarInactiveTintColor: c.sub,
        sceneStyle: { backgroundColor: c.bg },
      }}
    >
      <Tabs.Screen name="index" options={{ title: "Home", tabBarIcon: ({ color }) => <Glyph ch="⌂" color={color} /> }} />
      <Tabs.Screen name="issues" options={{ title: "Needs action", tabBarIcon: ({ color }) => <Glyph ch="⚠" color={color} /> }} />
      <Tabs.Screen name="known" options={{ title: "Known issues", tabBarIcon: ({ color }) => <Glyph ch="✎" color={color} /> }} />
    </Tabs>
  );
}
