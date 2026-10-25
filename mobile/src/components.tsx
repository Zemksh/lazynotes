import { PropsWithChildren } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import Animated, { FadeIn, ReduceMotion } from 'react-native-reanimated';
import { colors, ui } from './theme';

export function Button({ label, onPress, disabled, secondary, busy, accessibilityLabel }: {
  label: string; onPress: () => void; disabled?: boolean; secondary?: boolean; busy?: boolean; accessibilityLabel?: string;
}) {
  return <Pressable accessibilityRole="button" accessibilityLabel={accessibilityLabel ?? label}
    accessibilityState={{ disabled: !!disabled || !!busy, busy: !!busy }}
    disabled={disabled || busy} onPress={onPress}
    style={({ pressed }) => [styles.button, secondary && styles.secondary, (disabled || busy) && { opacity: 0.55 }, pressed && { opacity: 0.8 }]}>
    {busy && <ActivityIndicator color={secondary ? colors.accent : colors.white} />}
    <Text style={[styles.buttonText, secondary && { color: colors.accent }]}>{label}</Text>
  </Pressable>;
}

export function Entrance({ children }: PropsWithChildren) {
  return <Animated.View entering={FadeIn.duration(220).reduceMotion(ReduceMotion.System)} style={{ gap: 20 }}>{children}</Animated.View>;
}

export function ErrorMessage({ message }: { message?: string }) {
  return message ? <Text accessibilityRole="alert" accessibilityLiveRegion="polite" style={ui.error}>{message}</Text> : null;
}

export function MockNotice() {
  return <View style={ui.notice}><Text style={ui.label}>Development mock mode</Text>
    <Text style={ui.muted}>These are example notes and a sample transcript. They do not describe your recording.</Text></View>;
}

const styles = StyleSheet.create({
  button: { minHeight: 52, paddingHorizontal: 20, paddingVertical: 14, borderRadius: 14,
    backgroundColor: colors.accent, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 10 },
  secondary: { backgroundColor: colors.soft },
  buttonText: { fontSize: 16, lineHeight: 24, fontWeight: '600', color: colors.white },
});
