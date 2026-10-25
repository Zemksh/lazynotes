import 'react-native-reanimated';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ClassesProvider } from '../src/classes';
import { colors } from '../src/theme';

export default function Layout() {
  return <SafeAreaProvider><ClassesProvider><StatusBar style="dark" />
    <Stack screenOptions={{ headerStyle: { backgroundColor: colors.paper }, headerTintColor: colors.accent,
      headerShadowVisible: false, contentStyle: { backgroundColor: colors.paper }, headerBackTitle: 'Back' }}>
      <Stack.Screen name="index" options={{ headerShown: false }} />
      <Stack.Screen name="new" options={{ title: 'New class' }} />
      <Stack.Screen name="processing/[id]" options={{ title: 'Your class' }} />
      <Stack.Screen name="class/[id]" options={{ title: 'Class notes' }} />
    </Stack>
  </ClassesProvider></SafeAreaProvider>;
}
